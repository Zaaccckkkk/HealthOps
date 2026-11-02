"""Deterministic scope enforcement and evidence/answer coverage, not semantic grading."""

import json

from healthops.assistant.cards import Card
from healthops.assistant.claims import VerifiedClaim
from healthops.assistant.contracts import CoverageItem, ToolCallRecord
from healthops.assistant.routing import RequestSpec

DATA_TOOLS = {"daily_summary", "period_comparison", "availability"}


def scope_error(name: str, arguments: str, spec: RequestSpec, timezone: str, provider: str | None):
    allowed = {
        "summary": {"daily_summary", "availability", "lookup_reference"},
        "comparison": {"period_comparison", "availability", "lookup_reference"},
        "availability": {"availability"},
        "reference": {"lookup_reference"},
    }
    if name in {"state_findings", "decline_request"}:
        return None
    if name not in allowed.get(spec.intent, set()):
        return "Tool does not match the confirmed intent."
    try:
        raw = json.loads(arguments)
    except ValueError:
        return None  # The toolbox reports malformed arguments without executing them.
    if not isinstance(raw, dict):
        return None
    if name not in DATA_TOOLS:
        return None
    if "timezone" in raw and raw["timezone"] != timezone:
        return "Timezone differs from the authenticated request context."
    if "provider" in raw and raw["provider"] != provider:
        return "Provider differs from the source selected in the interface."
    if name != "availability" and not isinstance(raw.get("metric"), str):
        return 'metric must be a plain enum string, e.g. "steps", not an object or list.'
    if name != "availability" and raw.get("metric") not in spec.metrics:
        return "Metric is not in the confirmed request."
    if spec.window is None:
        return "No confirmed date window."
    if name == "period_comparison":
        if spec.baseline is None:
            return "No confirmed comparison baseline."
        expected = {
            "baseline_start": str(spec.baseline.start_date),
            "baseline_end": str(spec.baseline.end_date),
            "current_start": str(spec.window.start_date),
            "current_end": str(spec.window.end_date),
        }
    else:
        expected = {
            "start_date": str(spec.window.start_date),
            "end_date": str(spec.window.end_date),
        }
    if any(raw.get(key) != value for key, value in expected.items()):
        return "Dates differ from the confirmed window; do not widen, shorten or swap it."
    return None


def card_reported(card: Card, claims: list[VerifiedClaim]) -> bool:
    chosen = [
        v.claim for v in claims if v.valid and v.claim and v.claim.evidence_id == card.evidence_id
    ]
    if card.kind != "daily_table":
        return bool(chosen)
    if any(c.type == "window_summary" for c in chosen):
        return True
    if any(c.type == "missing_data" and c.date is None for c in chosen):
        return True  # The verifier already proved the whole window has no usable values.
    dates = {c.date for c in chosen}
    if not card.rows:
        return card.start_date == card.end_date and card.start_date in dates
    # A partial list of dates must not count as a complete multi-day response.
    return all(row.date in dates for row in card.rows)


def coverage(
    spec: RequestSpec, cards: list[Card], records: list[ToolCallRecord], claims: list[VerifiedClaim]
) -> list[CoverageItem]:
    tool, kind = {
        "summary": ("daily_summary", "daily_table"),
        "comparison": ("period_comparison", "comparison"),
        "availability": ("availability", "availability"),
        "reference": ("lookup_reference", "reference"),
    }[spec.intent]
    targets = spec.metrics if spec.intent in {"summary", "comparison"} else [spec.intent]
    result = []
    for target in targets:
        matching = [
            c
            for c in cards
            if c.kind == kind
            and (c.metric == target if spec.intent in {"summary", "comparison"} else True)
        ]
        calls = [
            r
            for r in records
            if r.name == tool
            and (
                r.arguments.get("metric") == target
                if spec.intent in {"summary", "comparison"}
                else True
            )
        ]
        status = "not_queried"
        if any(r.status == "error" for r in calls):
            status = "query_failed"
        if any(r.status == "ok" for r in calls):
            status = "no_records"
            if any(
                any(r.quality == "value" for r in c.rows)
                or c.series
                or c.kind == "reference"
                or (c.kind == "comparison" and c.direction != "unavailable")
                for c in matching
            ):
                status = "available"
            if matching and all(
                (
                    c.kind == "daily_table"
                    and c.rows
                    and all(r.quality != "value" for r in c.rows)
                    and any(r.quality == "unusable" for r in c.rows)
                )
                or (c.kind == "comparison" and c.direction == "unavailable")
                for c in matching
            ):
                status = "insufficient_data" if kind == "comparison" else "unusable"
        missing = [c.evidence_id for c in matching if not card_reported(c, claims)]
        # Retrieval returns candidate passages, not a requirement to quote every hit.
        if kind == "reference" and any(card_reported(c, claims) for c in matching):
            missing = []
        result.append(
            CoverageItem(
                target=str(target),
                query_status=status,
                evidence_ids=[c.evidence_id for c in matching],
                unreported_evidence_ids=missing,
                reported=bool(matching) and not missing,
            )
        )
    return result
