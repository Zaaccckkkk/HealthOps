"""LangGraph intake followed by the existing bounded evidence/tool graph.

Only clarification state is durable. No DB connection or model request remains open while
waiting for a human; a later HTTP request routes the saved transcript again.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from healthops.assistant.conversations import ConversationConflict
from healthops.assistant.model import MODEL_UNAVAILABLE, ModelConnectionError, ModelError
from healthops.assistant.orchestrator import REFUSAL_TEXT, TodayOverrideNotAllowed, _RunState
from healthops.assistant.routing import RequestSpec, clarification, clarification_form, route
from healthops.assistant.time_semantics import resolve


class IntakeState(TypedDict, total=False):
    decision: str
    spec: dict


class RoutedAssistant:
    def __init__(self, assistant, store):
        self.assistant = assistant
        self.store = store

    async def ask(self, request, user_id):
        core = self.assistant
        if request.today is not None and not core.settings.allow_today_override:
            raise TodayOverrideNotAllowed()
        semantics = resolve(request.timezone, request.today)
        messages = []
        rounds = 0
        expires_at = datetime.now(UTC) + timedelta(hours=1)
        if request.conversation_id:
            saved = await asyncio.to_thread(
                self.store.load, request.conversation_id, user_id, request.conversation_version
            )
            if (
                saved["timezone"] != request.timezone
                or saved["provider"] != request.provider
                or (request.today is not None and str(request.today) != saved["today"])
            ):
                raise ConversationConflict()
            from datetime import date

            semantics = resolve(saved["timezone"], date.fromisoformat(saved["today"]))
            messages = saved["messages"]
            rounds = saved["rounds"]
            expires_at = (
                datetime.fromisoformat(saved["expires_at"]) if saved.get("expires_at") else None
            )
        messages.append({"role": "user", "content": request.question})

        def reply(status, answer, decision=None, spec=None):
            state = _RunState(semantics)
            state.finish(status, answer)
            state.explanation_status = "not_applicable"
            response = core._response(state)
            response.routing_status = decision
            response.request_spec = spec
            return response

        async def save(pending, spec=None):
            payload = {
                "messages": messages,
                "timezone": semantics.timezone,
                "provider": request.provider,
                "today": str(semantics.today),
                "rounds": rounds + 1,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "request_spec": spec,
            }
            return await asyncio.to_thread(
                self.store.save,
                request.conversation_id,
                user_id,
                request.conversation_version,
                payload,
                pending,
            )

        if request.cancel:
            await save(False)
            return reply("refused", "Clarification cancelled. Start a new question.", "cancelled")

        response = None

        async def intake(_):
            nonlocal response
            async with core._gate:
                decision, spec, reason = await route(core.model, messages, semantics)
            spec_json = spec.model_dump(mode="json")
            if decision == "needs_clarification":
                if rounds >= 3:
                    await save(False)
                    response = reply(
                        "needs_clarification",
                        "Clarification limit reached. Start a new question "
                        "with explicit dates and metrics.",
                        decision,
                        spec_json,
                    )
                else:
                    answer = clarification(spec)
                    messages.append({"role": "assistant", "content": answer})
                    cid, version = await save(True, spec_json)
                    response = reply("needs_clarification", answer, decision, spec_json)
                    response.conversation_id = cid
                    response.conversation_version = version
                    response.clarification = clarification_form(spec)
                    response.conversation_expires_at = expires_at
            elif decision == "unsupported":
                await save(False)
                response = reply("refused", REFUSAL_TEXT[reason], decision, spec_json)
            else:
                # Compare-and-swap BEFORE executing tools: only one reply can consume a version.
                await save(False)
            return {"decision": decision, "spec": spec_json}

        async def execute(state):
            nonlocal response
            effective = request.model_copy(update={"today": None})
            context = json.dumps({"user_dialogue": messages, "confirmed_request": state["spec"]})
            response = await core.ask(
                effective,
                user_id,
                today=semantics.today,
                context=context,
                request_spec=RequestSpec.model_validate(state["spec"]),
            )
            response.routing_status = "ready"
            response.request_spec = state["spec"]
            return {}

        graph = StateGraph(IntakeState)
        graph.add_node("router", intake)
        graph.add_node("execute", execute)
        graph.add_edge(START, "router")
        graph.add_conditional_edges(
            "router",
            lambda s: "execute" if s["decision"] == "ready" else "end",
            {"execute": "execute", "end": END},
        )
        graph.add_edge("execute", END)
        try:
            async with asyncio.timeout(core.settings.deadline_seconds):
                if len(json.dumps(messages)) > min(
                    16000, core.settings.model_context_budget_chars // 2
                ):
                    return reply(
                        "budget_exceeded",
                        "Clarification context is too long. Start a shorter question.",
                    )
                with tracing_context(enabled=False):
                    await graph.compile().ainvoke({})
        except ModelConnectionError:
            return reply("model_error", MODEL_UNAVAILABLE)
        except ModelError:
            return reply("model_error", "The router could not interpret the request. Please retry.")
        except TimeoutError:
            return reply(
                "budget_exceeded",
                "The request exceeded its time budget. Please start a new question.",
            )
        assert response is not None
        return response
