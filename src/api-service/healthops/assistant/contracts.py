from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from healthops.assistant.cards import Card
from healthops.assistant.claims import VerifiedClaim
from healthops.schemas import Provider

AskStatus = Literal[
    "answered",
    "insufficient_data",
    "refused",
    "needs_clarification",
    "no_tool_answer",
    "tool_error",
    "budget_exceeded",
    "model_error",
]
# verified: every claim checked against the cards; unverified: a claim failed or the model
# produced prose instead of claims, so the fixed fallback text is shown; not_applicable: the
# request did not reach the explanation step.
ExplanationStatus = Literal["verified", "unverified", "not_applicable"]
ToolCallStatus = Literal["ok", "invalid_arguments", "rejected", "error", "not_executed"]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    conversation_id: UUID | None = None
    conversation_version: int | None = Field(default=None, ge=1)
    cancel: bool = False

    @model_validator(mode="after")
    def continuation_pair(self):
        if (self.conversation_id is None) != (self.conversation_version is None):
            raise ValueError("Conversation ID and version must be supplied together.")
        if self.cancel and self.conversation_id is None:
            raise ValueError("Cancellation requires a conversation.")
        return self

    question: str = Field(min_length=1, max_length=2000)
    timezone: str = Field(min_length=1, max_length=80)
    # Data channel chosen in the interface and injected by the server into every tool call.
    # Channel names written inside the question are not applied; they trigger a clarification.
    provider: Provider | None = None
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
    # Arguments actually executed after validation and server adjustments.
    arguments: dict[str, Any]
    # Arguments exactly as the model wrote them, before any server adjustment.
    model_arguments: dict[str, Any] = Field(default_factory=dict)
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
    # Recorded from the server when available (Ollama: /api/tags, /api/show, /api/ps).
    digest: str | None = None  # full digest; shorten only for display
    quantization: str | None = None
    model_max_context: int | None = None  # the model's maximum context length
    loaded_context: int | None = None  # context the running instance was loaded with, if running


class Limits(BaseModel):
    max_tool_calls: int
    max_model_turns: int
    deadline_seconds: float
    model_context_budget_chars: int
    evidence_budget_bytes: int


class CoverageItem(BaseModel):
    target: str
    query_status: Literal[
        "not_queried", "query_failed", "no_records", "unusable", "insufficient_data", "available"
    ]
    evidence_ids: list[str]
    unreported_evidence_ids: list[str]
    reported: bool


class VerificationAttempt(BaseModel):
    valid_claims: int
    invalid_reasons: list[str]
    missing_targets: list[str]


class ClarificationOption(BaseModel):
    id: str
    label: str
    reply_text: str


class ClarificationField(BaseModel):
    id: Literal["intent", "metrics", "time_range", "baseline"]
    label: str
    selection: Literal["single", "multiple", "text"]
    options: list[ClarificationOption] = Field(default_factory=list)
    allows_custom: bool = True


class ClarificationForm(BaseModel):
    version: Literal[1] = 1
    fields: list[ClarificationField]


class AskResponse(BaseModel):
    clarification: ClarificationForm | None = None
    conversation_expires_at: datetime | None = None
    coverage: list[CoverageItem] = Field(default_factory=list)
    completion_status: Literal["not_checked", "complete", "incomplete"] = "not_checked"
    verification_attempts: list[VerificationAttempt] = Field(default_factory=list)
    conversation_id: UUID | None = None
    conversation_version: int | None = None
    request_spec: dict[str, Any] | None = None
    routing_status: Literal["ready", "needs_clarification", "unsupported", "cancelled"] | None = (
        None
    )
    # Data query status: did the tools run and return usable data.
    status: AskStatus
    # Explanation status is independent: data can be present while the explanation failed.
    explanation_status: ExplanationStatus
    # Rendered from verified claims by fixed templates, or the fixed fallback text. For
    # refusals and errors this is the fixed message. Never free model prose.
    answer: str
    claims: list[VerifiedClaim]
    # Deterministic cards built from the evidence; shown regardless of the explanation.
    cards: list[Card]
    # Raw model prose when the model produced any (debug only). Clients must not display it
    # as an answer or execute anything embedded in it.
    model_text: str | None = None
    tool_calls: list[ToolCallRecord]
    # Full tool results for traceability, within evidence_budget_bytes; a result that does not
    # fit is replaced by an explicit omission marker, never silently cut.
    evidence: list[dict[str, Any]]
    numeric_consistency: NumericConsistency
    time_semantics: TimeSemantics
    model: ModelInfo
    limits: Limits
    disclaimer: str
