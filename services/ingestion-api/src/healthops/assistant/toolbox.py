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

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

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


class DeclineParams(BaseModel):
    reason: str


@dataclass
class ToolSpec:
    name: str
    description: str
    params: type[BaseModel]
    # (session, user_id, params) -> result model; None for tools without data access.
    run: Callable[[Session, UUID, BaseModel], BaseModel] | None
    compact: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    @property
    def takes_timezone(self) -> bool:
        return "timezone" in self.params.model_fields

    def schema(self) -> dict[str, Any]:
        parameters = self.params.model_json_schema()
        parameters.pop("title", None)
        # The server injects the request timezone; the model never sees or chooses it.
        parameters.get("properties", {}).pop("timezone", None)
        parameters["required"] = [r for r in parameters.get("required", []) if r != "timezone"]
        for prop in parameters.get("properties", {}).values():
            prop.pop("title", None)
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


def build_registry() -> dict[str, ToolSpec]:
    specs = [
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
            name="decline_request",
            description="Decline a question that is not about this user's own recorded health "
            "history or that asks for medical advice, another person's data or anything outside "
            "the tools.",
            params=DeclineParams,
            run=None,
        ),
    ]
    return {spec.name: spec for spec in specs}


class Toolbox:
    def __init__(
        self, session_factory: sessionmaker | None, registry: dict[str, ToolSpec] | None = None
    ):
        self._session_factory = session_factory
        self.registry = registry or build_registry()

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self.registry.values()]

    async def execute(
        self, name: str, arguments_json: str, user_id: UUID, timezone: str | None = None
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
        if spec.takes_timezone and timezone is not None:
            raw["timezone"] = timezone  # request timezone always wins over anything the model wrote
        try:
            params = spec.params.model_validate(raw)
        except ValidationError as exc:
            locations = ", ".join(
                ".".join(str(p) for p in e["loc"]) or "(root)" for e in exc.errors()
            )
            return ToolOutcome(
                ToolCallRecord(
                    name=name,
                    arguments=raw,
                    status="invalid_arguments",
                    error=f"invalid fields: {locations}",
                ),
                None,
                None,
            )
        arguments = params.model_dump(mode="json")
        if spec.run is None:
            return ToolOutcome(
                ToolCallRecord(name=name, arguments=arguments, status="ok", duration_ms=0),
                None,
                None,
                declined=True,
            )
        if self._session_factory is None:
            return ToolOutcome(
                ToolCallRecord(name=name, arguments=arguments, status="error", error="no database"),
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
                    status="error",
                    error="storage error",
                    duration_ms=int((time.monotonic() - started) * 1000),
                ),
                None,
                None,
            )
        result = result_model.model_dump(mode="json")
        warnings = list(result.get("warnings", []))
        record = ToolCallRecord(
            name=name,
            arguments=arguments,
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
