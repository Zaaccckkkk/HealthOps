from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AskStatus = Literal[
    "answered", "insufficient_data", "refused", "tool_error", "budget_exceeded", "model_error"
]
ToolCallStatus = Literal["ok", "invalid_arguments", "rejected", "error", "not_executed"]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)
    timezone: str = Field(min_length=1, max_length=80)
    # Allowed only when the server runs with assistant_allow_today_override (evaluation mode).
    today: date | None = None


class TimeSemantics(BaseModel):
    """Dates the server computed and handed to the model; the model never computes dates."""

    timezone: str
    today: date
    yesterday: date
    this_week_start: date
    last_week_start: date
    last_week_end: date
    week_before_last_start: date
    week_before_last_end: date
    three_weeks_ago_start: date
    three_weeks_ago_end: date
    past_7_days_start: date
    previous_7_days_start: date
    previous_7_days_end: date
    past_30_days_start: date
    previous_30_days_start: date
    previous_30_days_end: date
    definitions: dict[str, str]


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    status: ToolCallStatus
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None


class NumericConsistency(BaseModel):
    """Diagnostic only: whether numbers in the answer appear in the tool results.

    Matching numbers do not make an answer correct (direction, unit, window and provider can
    still be wrong). This is not fact verification.
    """

    status: Literal["consistent", "unmatched", "not_applicable"]
    numbers_in_answer: list[str]
    unmatched: list[str]
    note: str = (
        "Numeric consistency diagnostic: numbers in the answer were looked up in the tool "
        "results. It does not verify direction, unit, window, provider or interpretation."
    )


class ModelInfo(BaseModel):
    name: str
    endpoint_host: str | None
    prompt_version: str
    max_output_tokens: int
    temperature: float
    # None: the adapter does not control the model's reasoning mode; the server default applies.
    thinking: bool | None = None
    digest: str | None = None
    context_window: int | None = None


class Limits(BaseModel):
    max_tool_calls: int
    max_model_turns: int
    deadline_seconds: float
    model_context_budget_chars: int
    evidence_budget_bytes: int


class AskResponse(BaseModel):
    status: AskStatus
    answer: str
    tool_calls: list[ToolCallRecord]
    # Full tool results for traceability, within evidence_budget_bytes; a result that does not
    # fit is replaced by an explicit omission marker, never silently cut.
    evidence: list[dict[str, Any]]
    numeric_consistency: NumericConsistency
    time_semantics: TimeSemantics
    model: ModelInfo
    limits: Limits
    disclaimer: str
