"""Bounded tool-calling loop. Identity comes from the caller; the model never sees it.

The model chooses tools and, at the end, structured claims. Python validates arguments, runs
tools, builds data cards, verifies the claims against those cards and renders the explanation
from fixed templates. The model's prose is never shown as the answer.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from healthops.assistant import numeric
from healthops.assistant.cards import build_cards
from healthops.assistant.claims import FALLBACK_EXPLANATION, render_explanation
from healthops.assistant.contracts import (
    AskRequest,
    AskResponse,
    AskStatus,
    ExplanationStatus,
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


# Channel or device names inside the question are not applied as filters; the interface
# selection is. Such a question gets a clarification instead of a silently widened answer.
CHANNEL_WORDS = re.compile(
    r"\b(fitbit|apple watch|watch|google health|healthkit|health ?kit|garmin|oura|whoop|manual)\b",
    re.IGNORECASE,
)
CLARIFICATION = (
    "Choose the data source in the interface; names of devices or channels written in the "
    "question are not applied as filters. Note that a channel such as healthkit is not the "
    "same as one device."
)

REFUSAL_PREFIX = "I can only describe your own recorded health history using the available tools. "
REFUSAL_TEXT = {
    "medical_advice": REFUSAL_PREFIX
    + "This asks for medical advice, a diagnosis or a treatment decision, which I do not give.",
    "other_person": REFUSAL_PREFIX
    + "This asks about another person's data, which is not accessible.",
    "unsupported_time_range": REFUSAL_PREFIX
    + "This time range is not supported; ask about a listed window or explicit dates within "
    "93 days.",
    "invented_value_requested": REFUSAL_PREFIX
    + "This asks me to state a value that is not in the recorded data.",
    "out_of_scope": REFUSAL_PREFIX + "This question is outside that scope.",
}

ARGUMENT_NOTES = {
    "provider_filter_ignored": "The provider filter was ignored; results are grouped per provider.",
    "provider_from_request": "Results are limited to the data channel chosen in the interface.",
    "windows_reordered_baseline_is_earlier": "The windows were reordered so that baseline is the "
    "earlier window and current the later one; read the result accordingly.",
}


def card_hint(card) -> dict:
    """What the model may legitimately claim about one card: types and dates."""
    if card.kind == "daily_table":
        return {
            "evidence_id": card.evidence_id,
            "claim_types": ["value_on_date", "missing_data"],
            "dates_with_values": [r.date for r in card.rows if r.quality == "value"],
            "dates_with_records_but_no_reliable_value": [
                r.date for r in card.rows if r.quality == "unusable"
            ],
            "dates_without_records": [r.date for r in card.rows if r.quality == "none"],
        }
    if card.kind == "comparison":
        return {
            "evidence_id": card.evidence_id,
            "claim_types": ["window_difference"]
            if card.direction != "unavailable"
            else ["missing_data"],
        }
    return {
        "evidence_id": card.evidence_id,
        "claim_types": ["missing_data"] if not card.series else [],
        "note": "availability has no numeric claims; the card itself is shown",
    }


class _Deadline:
    def __init__(self, seconds: float):
        self.ends = time.monotonic() + seconds

    @property
    def remaining(self) -> float:
        return self.ends - time.monotonic()


class _RunState:
    def __init__(self, semantics):
        self.semantics = semantics
        self.records: list[ToolCallRecord] = []
        self.evidence: list[dict] = []
        self.usable_data: list[bool] = []
        self.tool_calls_made = 0
        self.card_count = 0
        self.status: AskStatus | None = None
        self.answer = FALLBACK_EXPLANATION
        self.explanation_status: ExplanationStatus = "unverified"
        self.claims = []
        self.model_text: str | None = None

    def finish(self, status: AskStatus, answer: str) -> None:
        if self.status is None:
            self.status = status
            self.answer = answer


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
        if request.provider is None and CHANNEL_WORDS.search(request.question):
            state.finish("needs_clarification", CLARIFICATION)
            return self._response(state)
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

    async def _run(self, request: AskRequest, user_id: UUID, state: _RunState, deadline: _Deadline):
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
                text = (turn.text or "").strip()
                state.model_text = text or None
                if not text:
                    state.finish("model_error", "The model returned an empty answer.")
                elif not state.evidence:
                    # Text with no successful tool result behind it (no call, or only failed
                    # calls): nothing supports it, so it is not "answered".
                    state.finish("no_tool_answer", FALLBACK_EXPLANATION)
                else:
                    # Prose instead of structured claims: the data stands, the explanation does not.
                    state.explanation_status = "unverified"
                    state.answer = FALLBACK_EXPLANATION
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
                        call.name,
                        call.arguments_json,
                        user_id,
                        timezone=state.semantics.timezone,
                        provider=request.provider,
                    )
                state.records.append(outcome.record)
                if outcome.findings is not None:
                    cards = build_cards(state.evidence)
                    text, verified, all_valid = render_explanation(outcome.findings, cards)
                    state.claims = verified
                    state.explanation_status = "verified" if all_valid else "unverified"
                    state.answer = text
                    return
                if outcome.declined:
                    # Fixed text per category; the model's free-text reason never reaches
                    # `answer` (it is kept in model_text for debugging only).
                    category = outcome.record.arguments.get("category", "out_of_scope")
                    state.model_text = outcome.record.model_arguments.get("detail") or None
                    state.finish(
                        "refused", REFUSAL_TEXT.get(category, REFUSAL_TEXT["out_of_scope"])
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
                    new_cards = build_cards(state.evidence)[state.card_count :]
                    state.card_count += len(new_cards)
                    payload = {
                        "evidence": [card_hint(c) for c in new_cards],
                        "hint": "Use state_findings with the allowed claim types and dates listed "
                        "under evidence; one to three findings that answer the question.",
                    } | outcome.for_model
                    notes = [w for w in outcome.record.warnings if w in ARGUMENT_NOTES]
                    if notes:
                        payload = {
                            "server_adjustments": [ARGUMENT_NOTES[n] for n in notes]
                        } | payload
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

    def _response(self, state: _RunState) -> AskResponse:
        status: AskStatus = state.status or (
            "insufficient_data" if state.usable_data and not any(state.usable_data) else "answered"
        )
        diagnostic = (
            numeric.check(state.answer, state.evidence)
            if status in ("answered", "insufficient_data")
            else NumericConsistency(status="not_applicable", numbers_in_answer=[], unmatched=[])
        )
        explanation_status: ExplanationStatus = (
            state.explanation_status
            if status in ("answered", "insufficient_data")
            else "not_applicable"
        )
        return AskResponse(
            status=status,
            explanation_status=explanation_status,
            answer=state.answer,
            claims=state.claims,
            cards=build_cards(state.evidence),
            model_text=state.model_text,
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


__all__ = ["Assistant", "AssistantSettings", "TodayOverrideNotAllowed", "ToolInputError"]
