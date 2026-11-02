"""Tool registry: schemas for the model, strict validation, bounded execution.

Identity is injected by the server; no tool schema has a user field. Tools run in a worker
thread with a short-lived database session so the event loop and the connection pool are
not held while the model thinks.
"""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from healthops.assistant.claims import FindingsParams
from healthops.assistant.contracts import ToolCallRecord
from healthops.tools.availability import availability_tool
from healthops.tools.contracts import (
    AvailabilityParams,
    DailySummaryParams,
    PeriodComparisonParams,
)
from healthops.tools.daily_summary import daily_summary_tool
from healthops.tools.local_days import ToolInputError
from healthops.tools.period_comparison import period_comparison_tool

DECLINE_CATEGORIES = (
    "medical_advice",
    "other_person",
    "unsupported_time_range",
    "invented_value_requested",
    "out_of_scope",
)


class DeclineParams(BaseModel):
    """Why the question is declined. Only the category reaches the user, as fixed text."""

    model_config = ConfigDict(extra="ignore")
    category: str = Field(
        default="out_of_scope",
        description="One of: " + ", ".join(DECLINE_CATEGORIES),
    )
    detail: str | None = Field(default=None, description="Optional free text; debugging only")

    @field_validator("category", mode="before")
    @classmethod
    def normalise(cls, value):
        text = str(value or "").strip().lower().replace(" ", "_")
        return text if text in DECLINE_CATEGORIES else "out_of_scope"


class ReferenceLookupParams(BaseModel):
    """What to look up in the reference library (definitions and public guidelines)."""

    model_config = ConfigDict(extra="ignore")
    query: str = Field(min_length=3, max_length=300, description="Topic or question to look up")


class ReferencePassage(BaseModel):
    source_id: str
    source_title: str
    publisher: str
    url: str
    license: str
    section: str
    page: int | None = None
    quote_limit_chars: int
    text: str  # the passage, already cut to what may be quoted
    similarity: float


class ReferenceLookupResult(BaseModel):
    query: str
    references: list[ReferencePassage]
    warnings: list[str] = Field(default_factory=list)
    definitions: dict[str, str] = Field(default_factory=dict)


@dataclass
class KnowledgeContext:
    """Retrieval dependencies for lookup_reference; None means the library is unavailable."""

    embedder: Any
    top_k: int = 3
    min_similarity: float = 0.35


def lookup_reference_tool(
    session: Session,
    user_id: UUID,
    params: ReferenceLookupParams,
    knowledge: KnowledgeContext | None,
) -> ReferenceLookupResult:
    """Semantic search over the reference library. Reads nothing about the user."""
    from healthops.assistant.cards import quotable
    from healthops.knowledge.store import search

    definitions = {
        "reference": "A passage from a licence-checked public source or this project's own "
        "documentation. It describes definitions and public guidelines only; it is not an "
        "assessment of the user's values."
    }
    if knowledge is None or knowledge.embedder is None:
        return ReferenceLookupResult(
            query=params.query,
            references=[],
            warnings=["knowledge_base_unavailable"],
            definitions=definitions,
        )
    vector = knowledge.embedder.embed([params.query])[0]
    hits = search(
        session, vector, knowledge.top_k, knowledge.min_similarity, knowledge.embedder.model
    )
    references = [
        ReferencePassage(
            source_id=hit.chunk.source_id,
            source_title=hit.chunk.source_title,
            publisher=hit.chunk.publisher,
            url=hit.chunk.url,
            license=hit.chunk.license,
            section=hit.chunk.section,
            page=hit.chunk.page,
            quote_limit_chars=hit.chunk.quote_limit_chars,
            text=quotable(hit.chunk.text, hit.chunk.quote_limit_chars),
            similarity=round(hit.similarity, 4),
        )
        for hit in hits
    ]
    from healthops.assistant.reference_safety import BLOCKED, reference_allowed

    # Screen the full stored chunk, before quotation truncation could hide an instruction.
    safe = [
        reference
        for reference, hit in zip(references, hits)
        if reference_allowed(reference.model_dump() | {"text": hit.chunk.text})
    ]
    warnings = [BLOCKED] if len(safe) != len(references) else []
    references = safe
    if not references:
        warnings.append("no_reference_found")
    return ReferenceLookupResult(
        query=params.query, references=references, warnings=warnings, definitions=definitions
    )


@dataclass
class ToolSpec:
    name: str
    description: str
    params: type[BaseModel]
    # (session, user_id, params) -> result model; None for tools without data access.
    run: Callable[[Session, UUID, BaseModel], BaseModel] | None
    compact: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    # Fields the model never sees. `timezone` is injected by the server. `provider` is hidden
    # because results are already grouped per provider and, on the development set, the local
    # model added a wrong provider filter to most calls and reported missing data.
    HIDDEN_FIELDS = frozenset({"timezone", "provider"})

    @property
    def takes_timezone(self) -> bool:
        return "timezone" in self.params.model_fields

    def schema(self) -> dict[str, Any]:
        parameters = self.params.model_json_schema()
        definitions = parameters.pop("$defs", {})

        def inline(value, ancestors=()):
            if isinstance(value, list):
                return [inline(item, ancestors) for item in value]
            if not isinstance(value, dict):
                return value
            if "$ref" in value:
                ref = value["$ref"]
                if not ref.startswith("#/$defs/") or ref in ancestors:
                    raise ValueError("Tool schemas require nonrecursive local definitions")
                target = definitions[ref.removeprefix("#/$defs/")]
                value = target | {key: item for key, item in value.items() if key != "$ref"}
                ancestors = (*ancestors, ref)
            return {key: inline(item, ancestors) for key, item in value.items() if key != "title"}

        for hidden in self.HIDDEN_FIELDS:
            parameters.get("properties", {}).pop(hidden, None)
        parameters["required"] = [
            r for r in parameters.get("required", []) if r not in self.HIDDEN_FIELDS
        ]
        parameters = inline(parameters)
        parameters["additionalProperties"] = False
        properties = parameters.get("properties", {})
        if "metric" in properties:
            properties["metric"]["description"] = (
                'One metric as a plain string from the enum, e.g. "steps". '
                'Never send an object such as {"type": "steps"}, or a list. '
                "Use one call per requested metric."
            )
        for name, prop in properties.items():
            if prop.get("format") == "date":
                prop["description"] = (
                    f"{name}: ISO YYYY-MM-DD string from the confirmed request; "
                    "do not widen or shorten its window."
                )
        if self.name == "daily_summary":
            parameters["examples"] = [
                {"metric": "steps", "start_date": "2026-09-01", "end_date": "2026-09-01"}
            ]
        elif self.name == "period_comparison":
            parameters["examples"] = [
                {
                    "metric": "steps",
                    "baseline_start": "2026-09-01",
                    "baseline_end": "2026-09-07",
                    "current_start": "2026-09-08",
                    "current_end": "2026-09-14",
                }
            ]
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


@dataclass
class ToolOutcome:
    record: ToolCallRecord
    result: dict[str, Any] | None  # full result for evidence
    for_model: dict[str, Any] | None  # bounded representation sent back to the model
    declined: bool = False
    has_usable_data: bool = False
    findings: FindingsParams | None = (
        None  # the model's structured claims, if this was state_findings
    )


_WINDOW_FIELDS = ("baseline_start", "baseline_end", "current_start", "current_end")


def _windows_reversed(raw: dict[str, Any]) -> bool:
    """True only when all four window fields are present strings and baseline starts later.

    Incomplete or non-string fields are left for Pydantic to report; reordering them first
    would raise or hide the real validation error.
    """
    if not all(isinstance(raw.get(field), str) for field in _WINDOW_FIELDS):
        return False
    return raw["baseline_start"] > raw["current_start"]  # ISO dates sort as text


def has_usable_data(name: str, result: dict[str, Any]) -> bool:
    """Per-tool definition of 'the result carries data the answer can rest on'."""
    if name == "availability":
        return bool(result.get("series"))
    if name == "daily_summary":
        return result.get("record_count", 0) > 0
    if name == "period_comparison":
        return any(
            provider["baseline"]["value"] is not None or provider["current"]["value"] is not None
            for provider in result.get("providers", [])
        )
    if name == "lookup_reference":
        return bool(result.get("references"))
    return True


def _compact_daily_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Drop null fields and unused definitions; keep window, unit, every warning."""
    used_codes: set[str] = set()
    providers = []
    for provider in result["providers"]:
        days = []
        for day in provider["days"]:
            kept = {k: v for k, v in day.items() if v not in (None, 0, []) or k in ("date",)}
            used_codes.update(day["warnings"])
            days.append(kept)
        providers.append(
            {
                "provider": provider["provider"],
                "record_count": provider["record_count"],
                "days": days,
            }
        )
    definitions = {
        k: v
        for k, v in result["definitions"].items()
        if not k.startswith("warning:")
        or k.removeprefix("warning:") in used_codes | set(result["warnings"])
    }
    return {
        "metric": result["metric"],
        "unit": result["unit"],
        "metric_class": result["metric_class"],
        "window": result["window"],
        "record_count": result["record_count"],
        "providers": providers,
        "warnings": result["warnings"],
        "definitions": definitions,
    }


def _compact_period_comparison(result: dict[str, Any]) -> dict[str, Any]:
    """Per-provider window summaries without the full daily details."""
    used_codes = set(result["warnings"])
    for provider in result["providers"]:
        used_codes.update(provider["warnings"])
    definitions = {
        k: v
        for k, v in result["definitions"].items()
        if not k.startswith("warning:") or k.removeprefix("warning:") in used_codes
    }
    return {k: v for k, v in result.items() if k != "details"} | {"definitions": definitions}


def build_registry(knowledge: KnowledgeContext | None = None) -> dict[str, ToolSpec]:
    specs = [
        ToolSpec(
            name="lookup_reference",
            description="Look up definitions and public guidelines in the reference library "
            "(what a metric means, how SDNN and RMSSD differ, what activity or sleep guidelines "
            "recommend, what a card warning means). Returns quotable passages with sources. "
            "Never use it to judge whether the user's own values are good or bad.",
            params=ReferenceLookupParams,
            run=lambda session, user_id, params: lookup_reference_tool(
                session, user_id, params, knowledge
            ),
        ),
        ToolSpec(
            name="availability",
            description="List which (provider, metric, aggregation) series the user has in a date "
            "window, with record counts and first/last timestamps. Use for 'what data do I have'.",
            params=AvailabilityParams,
            run=lambda session, user_id, params: availability_tool(session, user_id, params),
        ),
        ToolSpec(
            name="daily_summary",
            description="Per-provider, per-local-day statistics of one metric over an inclusive "
            "date window of at most 93 days. Point metrics give sample_mean/min/max; cumulative "
            "metrics give daily totals. Null means no data, never zero.",
            params=DailySummaryParams,
            run=lambda session, user_id, params: daily_summary_tool(session, user_id, params),
            compact=_compact_daily_summary,
        ),
        ToolSpec(
            name="period_comparison",
            description="Compare a baseline window and a current window of one metric per "
            "provider: window values, difference, valid-day counts and warnings. Windows must "
            "not overlap.",
            params=PeriodComparisonParams,
            run=lambda session, user_id, params: period_comparison_tool(session, user_id, params),
            compact=_compact_period_comparison,
        ),
        ToolSpec(
            name="state_findings",
            description="Finish the answer. Pass findings as plain strings, one per item, in the "
            "form '<type> <evidence_id> [<ISO date>]'. Types: value_on_date (date required), "
            "window_difference (no date), missing_data (date optional), window_summary "
            "(whole daily table; no date), available_series (inventory; no date), "
            "reference (no date). Example: "
            "['value_on_date e1.healthkit 2026-10-01', 'missing_data e1.healthkit 2026-10-02']. "
            "The server reads the numbers and writes the sentences.",
            params=FindingsParams,
            run=None,
        ),
        ToolSpec(
            name="decline_request",
            description="Decline a question. Pass category: medical_advice, other_person, "
            "unsupported_time_range, invented_value_requested or out_of_scope. The user sees a "
            "fixed sentence for the category; detail is kept for debugging only.",
            params=DeclineParams,
            run=None,
        ),
    ]
    return {spec.name: spec for spec in specs}


class Toolbox:
    def __init__(
        self,
        session_factory: sessionmaker | None,
        registry: dict[str, ToolSpec] | None = None,
        knowledge: KnowledgeContext | None = None,
    ):
        self._session_factory = session_factory
        self.registry = registry or build_registry(knowledge)

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self.registry.values()]

    async def execute(
        self,
        name: str,
        arguments_json: str,
        user_id: UUID,
        timezone: str | None = None,
        provider: str | None = None,
    ) -> ToolOutcome:
        started = time.monotonic()
        spec = self.registry.get(name)
        if spec is None:
            return ToolOutcome(
                ToolCallRecord(
                    name=name, arguments={}, status="invalid_arguments", error="unknown tool"
                ),
                None,
                None,
            )
        try:
            raw = json.loads(arguments_json) if arguments_json else {}
            if not isinstance(raw, dict):
                raise ValueError("arguments must be an object")
        except ValueError as exc:
            return ToolOutcome(
                ToolCallRecord(
                    name=name,
                    arguments={},
                    status="invalid_arguments",
                    error=f"malformed arguments: {exc}",
                ),
                None,
                None,
            )
        model_arguments = dict(raw)  # what the model wrote, kept for evaluation and audit
        if spec.takes_timezone and timezone is not None:
            raw["timezone"] = timezone  # request timezone always wins over anything the model wrote
        notes: list[str] = []
        if "provider" in raw and "provider" in spec.params.model_fields:
            raw.pop("provider")  # hidden field; results stay grouped per provider
            notes.append("provider_filter_ignored")
        if provider is not None and "provider" in spec.params.model_fields:
            raw["provider"] = provider  # the channel the user chose in the interface
            notes.append("provider_from_request")
        if name == "period_comparison" and _windows_reversed(raw):
            # Convention: baseline is the earlier window. Reorder deterministically and say so.
            raw["baseline_start"], raw["current_start"] = (
                raw["current_start"],
                raw["baseline_start"],
            )
            raw["baseline_end"], raw["current_end"] = raw["current_end"], raw["baseline_end"]
            notes.append("windows_reordered_baseline_is_earlier")
        try:
            params = spec.params.model_validate(raw)
        except ValidationError as exc:
            locations = ", ".join(
                ".".join(str(p) for p in e["loc"]) or "(root)" for e in exc.errors()
            )
            return ToolOutcome(
                ToolCallRecord(
                    name=name,
                    arguments={},
                    model_arguments=model_arguments,
                    status="invalid_arguments",
                    error=f"invalid fields: {locations}",
                ),
                None,
                None,
            )
        arguments = params.model_dump(mode="json")
        if spec.run is None:
            record = ToolCallRecord(
                name=name,
                arguments=arguments,
                model_arguments=model_arguments,
                status="ok",
                duration_ms=0,
            )
            if isinstance(params, FindingsParams):
                return ToolOutcome(record, None, None, findings=params)
            return ToolOutcome(record, None, None, declined=True)
        if self._session_factory is None:
            return ToolOutcome(
                ToolCallRecord(
                    name=name,
                    arguments=arguments,
                    model_arguments=model_arguments,
                    status="error",
                    error="no database",
                ),
                None,
                None,
            )

        def run_in_thread() -> BaseModel:
            # Short-lived session: opened for this call only, closed before the next model turn.
            with self._session_factory() as session:
                return spec.run(session, user_id, params)

        try:
            result_model = await asyncio.to_thread(run_in_thread)
        except ToolInputError as exc:
            # The tool's own limits (span, overlap, budget, dates) still apply after Pydantic.
            return ToolOutcome(
                ToolCallRecord(
                    name=name,
                    arguments=arguments,
                    model_arguments=model_arguments,
                    status="rejected",
                    error=str(exc),
                    duration_ms=int((time.monotonic() - started) * 1000),
                ),
                None,
                None,
            )
        except SQLAlchemyError:
            return ToolOutcome(
                ToolCallRecord(
                    name=name,
                    arguments=arguments,
                    model_arguments=model_arguments,
                    status="error",
                    error="storage error",
                    duration_ms=int((time.monotonic() - started) * 1000),
                ),
                None,
                None,
            )
        result = result_model.model_dump(mode="json")
        warnings = notes + list(result.get("warnings", []))
        record = ToolCallRecord(
            name=name,
            arguments=arguments,
            model_arguments=model_arguments,
            status="ok",
            warnings=warnings,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return ToolOutcome(
            record,
            result,
            spec.compact(result) if spec.compact else result,
            has_usable_data=has_usable_data(name, result),
        )
