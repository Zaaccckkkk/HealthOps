"""Bounded tool-calling loop. Identity comes from the caller; the model never sees it."""

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from healthops.assistant import numeric
from healthops.assistant.contracts import (
    AskRequest,
    AskResponse,
    AskStatus,
    Limits,
    NumericConsistency,
    ToolCallRecord,
)
from healthops.assistant.model import ChatModel, ModelError, ModelTurn
from healthops.assistant.prompts import DISCLAIMER, SYSTEM_PROMPT
from healthops.assistant.time_semantics import describe, resolve
from healthops.assistant.toolbox import Toolbox
from healthops.tools.local_days import ToolInputError


@dataclass
class AssistantSettings:
    max_tool_calls: int = 4
    max_model_turns: int = 6
    # Wall-clock budget for the whole request: queueing, model calls and tool execution.
    deadline_seconds: float = 120.0
    # Characters of the full request sent to the model (system prompt, question, tool schemas,
    # model output and tool results). Characters, not tokens: a conservative proxy.
    model_context_budget_chars: int = 24_000
    evidence_budget_bytes: int = 262_144
    max_concurrency: int = 2
    allow_today_override: bool = False


class TodayOverrideNotAllowed(Exception):
    pass


class _Deadline:
    def __init__(self, seconds: float):
        self.ends = time.monotonic() + seconds

    @property
    def remaining(self) -> float:
        return self.ends - time.monotonic()

    def expired(self) -> bool:
        return self.remaining <= 0


class Assistant:
    def __init__(self, model: ChatModel, toolbox: Toolbox, settings: AssistantSettings):
        self.model = model
        self.toolbox = toolbox
        self.settings = settings
        self._gate = asyncio.Semaphore(settings.max_concurrency)

    def limits(self) -> Limits:
        s = self.settings
        return Limits(
            max_tool_calls=s.max_tool_calls,
            max_model_turns=s.max_model_turns,
            deadline_seconds=s.deadline_seconds,
            model_context_budget_chars=s.model_context_budget_chars,
            evidence_budget_bytes=s.evidence_budget_bytes,
        )

    async def ask(
        self, request: AskRequest, user_id: UUID, today: date | None = None
    ) -> AskResponse:
        if request.today is not None and not self.settings.allow_today_override:
            raise TodayOverrideNotAllowed()
        semantics = resolve(request.timezone, request.today if request.today is not None else today)
        deadline = _Deadline(self.settings.deadline_seconds)
        state = _RunState(semantics)
        try:
            # Queueing time counts against the deadline too.
            async with asyncio.timeout(max(deadline.remaining, 0)):
                async with self._gate:
                    await self._run(request, user_id, state, deadline)
        except TimeoutError:
            state.finish(
                "budget_exceeded",
                "The request exceeded its time budget before an answer was produced. A tool "
                "already running in a worker thread finishes on its own; its result is discarded.",
            )
        return self._response(state)

    async def _run(
        self, request: AskRequest, user_id: UUID, state: "_RunState", deadline: _Deadline
    ):
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(time_semantics=describe(state.semantics)),
            },
            {"role": "user", "content": request.question},
        ]
        tools = self.toolbox.schemas()
        invalid_retry_used = False
        for _turn in range(self.settings.max_model_turns):
            # Full request size, not only tool results: the model receives all of this.
            request_chars = len(json.dumps(messages, separators=(",", ":"))) + len(
                json.dumps(tools, separators=(",", ":"))
            )
            if request_chars > self.settings.model_context_budget_chars:
                state.finish(
                    "budget_exceeded",
                    "The request to the model would exceed the context budget "
                    f"({request_chars} characters); ask about a shorter window or one metric.",
                )
                return
            try:
                async with asyncio.timeout(max(deadline.remaining, 0)):
                    turn: ModelTurn = await self.model.complete(messages, tools)
            except ModelError as exc:
                state.finish(
                    "model_error", f"The model could not be reached or answered usably: {exc}"
                )
                return
            if not turn.tool_calls:
                answer = (turn.text or "").strip()
                if not answer:
                    state.finish("model_error", "The model returned an empty answer.")
                else:
                    state.answer = answer
                return
            messages.append(
                {
                    "role": "assistant",
                    "content": turn.text or "",
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments_json},
                        }
                        for c in turn.tool_calls
                    ],
                }
            )
            for call in turn.tool_calls:
                if state.tool_calls_made >= self.settings.max_tool_calls:
                    state.records.append(
                        ToolCallRecord(
                            name=call.name,
                            arguments={},
                            status="not_executed",
                            error="tool call budget exhausted",
                        )
                    )
                    state.finish(
                        "budget_exceeded", "The question needed more tool calls than allowed."
                    )
                    return
                state.tool_calls_made += 1
                async with asyncio.timeout(max(deadline.remaining, 0)):
                    outcome = await self.toolbox.execute(
                        call.name, call.arguments_json, user_id, timezone=state.semantics.timezone
                    )
                state.records.append(outcome.record)
                if outcome.declined:
                    reason = outcome.record.arguments.get("reason", "")
                    state.finish(
                        "refused",
                        "I can only answer questions about your own recorded health history "
                        f"using the available tools. Reason: {reason}".strip(),
                    )
                    return
                if outcome.record.status == "invalid_arguments":
                    if invalid_retry_used:
                        state.finish(
                            "tool_error",
                            f"The model repeated an invalid tool call: {outcome.record.error}",
                        )
                        return
                    invalid_retry_used = True
                    payload = {
                        "error": outcome.record.error,
                        "hint": "Fix the arguments and call once more.",
                    }
                elif outcome.record.status == "error":
                    state.finish("tool_error", "A tool failed while reading the history.")
                    return
                elif outcome.record.status == "rejected":
                    payload = {"error": outcome.record.error}
                else:
                    state.evidence.append(
                        {
                            "tool": call.name,
                            "arguments": outcome.record.arguments,
                            "result": outcome.result,
                        }
                    )
                    state.usable_data.append(outcome.has_usable_data)
                    payload = outcome.for_model
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(payload, separators=(",", ":")),
                    }
                )
        state.finish(
            "budget_exceeded", "The conversation reached the maximum number of model turns."
        )

    def _response(self, state: "_RunState") -> AskResponse:
        status: AskStatus = state.status or (
            "insufficient_data" if state.usable_data and not any(state.usable_data) else "answered"
        )
        diagnostic = (
            numeric.check(state.answer, state.evidence)
            if status in ("answered", "insufficient_data")
            else NumericConsistency(status="not_applicable", numbers_in_answer=[], unmatched=[])
        )
        return AskResponse(
            status=status,
            answer=state.answer,
            tool_calls=state.records,
            evidence=self._bounded_evidence(state.evidence),
            numeric_consistency=diagnostic,
            time_semantics=state.semantics,
            model=self.model.info,
            limits=self.limits(),
            disclaimer=DISCLAIMER,
        )

    def _bounded_evidence(self, evidence: list[dict]) -> list[dict]:
        kept, used = [], 0
        for item in evidence:
            size = len(json.dumps(item, separators=(",", ":")).encode())
            if used + size > self.settings.evidence_budget_bytes:
                kept.append(
                    {
                        "tool": item["tool"],
                        "arguments": item["arguments"],
                        "result": None,
                        "omitted": True,
                        "reason": f"result of {size} bytes exceeds the evidence budget",
                    }
                )
                continue
            used += size
            kept.append(item)
        return kept


class _RunState:
    def __init__(self, semantics):
        self.semantics = semantics
        self.records: list[ToolCallRecord] = []
        self.evidence: list[dict] = []
        self.usable_data: list[bool] = []
        self.tool_calls_made = 0
        self.status: AskStatus | None = None
        self.answer = ""

    def finish(self, status: AskStatus, answer: str) -> None:
        if self.status is None:
            self.status = status
            self.answer = answer


__all__ = ["Assistant", "AssistantSettings", "TodayOverrideNotAllowed", "ToolInputError"]
