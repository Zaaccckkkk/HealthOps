"""Deterministic data cards built from tool evidence. No model involvement, no new statistics.

Cards show what the tools returned: one row per local day with the recorded value, an
explicit "no data" marker (never zero), the kind of statistic, unit, provider, window and
warnings. The only transformation is a unit-faithful display conversion (seconds into hours
and minutes). Nothing is summed or merged across days or providers.
"""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field

RowKind = Literal["sample_mean", "daily_total", "interval_total_sum", "daily_summary", "none"]

STATISTIC_LABELS = {
    "sample_mean": "mean of recorded samples (not an all-day average)",
    "daily_total": "vendor daily total",
    "interval_total_sum": "sum of recorded intervals (not full-day coverage)",
    "daily_summary": "vendor daily summary",
    "none": "no data",
}

WARNING_TEXT = {
    "sparse_window": "Fewer than half of the days in this window have data.",
    "no_records_in_window": "No records in this window. This says nothing about activity, "
    "device wear or authorization.",
    "insufficient_data": "Not enough data for a window value.",
    "interval_totals_used": "Totals come from recorded intervals and do not guarantee full-day "
    "coverage.",
    "overlapping_intervals": "Intervals from the same channel overlap; no reliable total for "
    "those days.",
    "interval_crosses_day_boundary": "Intervals crossing midnight are assigned to the day they "
    "start.",
    "unaligned_daily_record": "A vendor daily record did not match the local day and was not used.",
    "multiple_daily_records": "Several vendor daily records for one day; none was chosen.",
    "mixed_total_kinds": "Both vendor daily totals and interval sums exist; they are shown "
    "separately, never added.",
    "unsupported_aggregation": "Some records have an aggregation this metric does not support "
    "and were not used.",
    "zero_length_interval": "Zero-length intervals were excluded.",
    "provider_missing_in_window": "This channel has no records in one of the windows.",
    "mixed_bases_within_window": "Some days use vendor totals and others interval sums inside "
    "one window.",
    "basis_differs_between_windows": "The two windows rest on different kinds of totals.",
    "daily_summary_not_used": "Vendor daily summaries exist but do not enter the mean.",
}


RowQuality = Literal["value", "unusable", "none"]


class CardRow(BaseModel):
    date: str
    kind: RowKind
    value: str | None  # decimal text exactly as the tool returned it; None means no usable value
    display: str  # human-readable value, "no reliable value" or "no data"
    # Three distinct states: a value (zero included), records that could not be summarised
    # (conflicts, overlaps, unsupported aggregation), or no records at all.
    quality: RowQuality = "none"
    records: int = 0
    sample_count: int | None = None
    warnings: list[str] = Field(default_factory=list)


class Card(BaseModel):
    evidence_id: str
    kind: Literal["daily_table", "comparison", "availability", "reference"]
    title: str
    tool: str
    timezone: str
    start_date: str | None
    end_date: str | None
    provider: str | None
    metric: str | None
    unit: str | None
    statistic: str | None
    rows: list[CardRow] = Field(default_factory=list)
    # Comparison cards only.
    baseline: dict[str, Any] | None = None
    current: dict[str, Any] | None = None
    difference: str | None = None
    direction: Literal["higher", "lower", "unchanged", "unavailable"] | None = None
    # Availability cards only.
    series: list[dict[str, Any]] = Field(default_factory=list)
    # Reference cards only (knowledge base): the passage and its provenance. `text` is the
    # part that may be quoted (already cut to the source's quotation limit).
    text: str | None = None
    section: str | None = None
    publisher: str | None = None
    source_url: str | None = None
    license: str | None = None
    similarity: float | None = None
    warnings: list[str] = Field(default_factory=list)
    warning_text: list[str] = Field(default_factory=list)


def _decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        return Decimal(str(text))
    except InvalidOperation:
        return None


def format_value(value: str | None, unit: str | None) -> str:
    """Display text for one tool value. Seconds become hours and minutes by integer arithmetic."""
    number = _decimal(value)
    if number is None:
        return "no data"
    if unit == "s":
        total = int(number)  # tool values are whole seconds for duration metrics
        hours, rest = divmod(total, 3600)
        minutes = rest // 60
        return f"{hours} h {minutes} min ({total} s)"
    if unit in ("count",):
        return f"{int(number):,} {unit}"
    if number == number.to_integral_value():
        return f"{int(number)} {unit}"
    # One decimal, rounded half-even (never truncated): 57.75 -> 57.8, 56.83 -> 56.8.
    rounded = number.quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)
    return f"{rounded.normalize():f} {unit}"


def _day_row(day: dict[str, Any], metric_class: str, unit: str) -> CardRow:
    if metric_class == "point":
        value, kind = day.get("sample_mean"), "sample_mean"
        if value is None and day.get("daily_summary") is not None:
            value, kind = day["daily_summary"], "daily_summary"
    else:
        value, kind = day.get("daily_total"), "daily_total"
        if value is None and day.get("interval_total_sum") is not None:
            value, kind = day["interval_total_sum"], "interval_total_sum"
    records = sum(
        int(day.get(field) or 0)
        for field in (
            "sample_count",
            "interval_total_count",
            "daily_total_count",
            "daily_summary_count",
            "unaligned_count",
            "unsupported_count",
        )
    )
    if value is None:
        kind = "none"
        quality = "unusable" if records > 0 else "none"
        display = "no reliable value" if records > 0 else "no data"
    else:
        quality = "value"
        display = format_value(value, unit)
    return CardRow(
        date=day["date"],
        kind=kind,
        value=value,
        display=display,
        quality=quality,
        records=records,
        sample_count=day.get("sample_count") if metric_class == "point" else None,
        warnings=list(day.get("warnings", [])),
    )


def _texts(codes) -> list[str]:
    return [WARNING_TEXT.get(code, code.replace("_", " ")) for code in sorted(set(codes))]


def build_cards(evidence: list[dict[str, Any]]) -> list[Card]:
    cards: list[Card] = []
    for index, item in enumerate(evidence):
        result = item.get("result")
        if not result:
            continue
        evidence_id = f"e{index + 1}"
        tool = item["tool"]
        if tool == "daily_summary":
            cards.extend(_daily_cards(evidence_id, result))
        elif tool == "period_comparison":
            cards.extend(_comparison_cards(evidence_id, result))
        elif tool == "availability":
            cards.append(_availability_card(evidence_id, result))
        elif tool == "lookup_reference":
            cards.extend(_reference_cards(evidence_id, result))
    return cards


def quotable(text: str, limit: int) -> str:
    """The longest prefix within `limit` that ends at a sentence boundary, else a hard cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in (". ", "! ", "? ", ".\n"):
        index = cut.rfind(mark)
        if index >= limit // 3:
            return cut[: index + 1]
    return cut.rstrip() + "..."


def _reference_cards(evidence_id: str, result: dict[str, Any]) -> list[Card]:
    cards = []
    for index, reference in enumerate(result.get("references") or []):
        cards.append(
            Card(
                evidence_id=f"{evidence_id}.r{index + 1}",
                kind="reference",
                title=reference.get("source_title", "reference"),
                tool="lookup_reference",
                timezone="",
                start_date=None,
                end_date=None,
                provider=None,
                metric=None,
                unit=None,
                statistic=None,
                text=quotable(
                    reference.get("text", ""), int(reference.get("quote_limit_chars", 300))
                ),
                section=reference.get("section"),
                publisher=reference.get("publisher"),
                source_url=reference.get("url"),
                license=reference.get("license"),
                similarity=reference.get("similarity"),
            )
        )
    return cards


def _daily_cards(evidence_id: str, result: dict[str, Any]) -> list[Card]:
    window = result.get("window", {})
    unit = result.get("unit")
    metric = result.get("metric")
    metric_class = result.get("metric_class", "point")
    common = {
        "tool": "daily_summary",
        "timezone": window.get("timezone", ""),
        "start_date": window.get("start_date"),
        "end_date": window.get("end_date"),
        "metric": metric,
        "unit": unit,
    }
    providers = result.get("providers") or []
    if not providers:
        codes = result.get("warnings", [])
        return [
            Card(
                evidence_id=evidence_id,
                kind="daily_table",
                title=f"{metric}: no records",
                provider=None,
                statistic=None,
                warnings=list(codes),
                warning_text=_texts(codes),
                **common,
            )
        ]
    cards = []
    for provider in providers:
        rows = [_day_row(day, metric_class, unit) for day in provider.get("days", [])]
        kinds = {row.kind for row in rows if row.kind != "none"}
        if len(kinds) == 1:
            statistic = STATISTIC_LABELS[next(iter(kinds))]
        elif kinds:
            statistic = "mixed statistics; see each row"
        else:
            statistic = STATISTIC_LABELS["none"]
        codes = set(result.get("warnings", [])) | {w for row in rows for w in row.warnings}
        cards.append(
            Card(
                evidence_id=f"{evidence_id}.{provider['provider']}",
                kind="daily_table",
                title=f"{metric} per day ({provider['provider']})",
                provider=provider["provider"],
                statistic=statistic,
                rows=rows,
                warnings=sorted(codes),
                warning_text=_texts(codes),
                **common,
            )
        )
    return cards


def _comparison_cards(evidence_id: str, result: dict[str, Any]) -> list[Card]:
    unit = result.get("unit")
    metric = result.get("metric")
    bwin = result.get("baseline_window", {})
    cwin = result.get("current_window", {})
    common = {
        "tool": "period_comparison",
        "timezone": bwin.get("timezone", ""),
        "start_date": bwin.get("start_date"),
        "end_date": cwin.get("end_date"),
        "metric": metric,
        "unit": unit,
    }
    providers = result.get("providers") or []
    if not providers:
        codes = result.get("warnings", [])
        return [
            Card(
                evidence_id=evidence_id,
                kind="comparison",
                title=f"{metric}: no records",
                provider=None,
                statistic=None,
                direction="unavailable",
                warnings=list(codes),
                warning_text=_texts(codes),
                **common,
            )
        ]
    cards = []
    for provider in providers:
        before, after = provider.get("baseline", {}), provider.get("current", {})
        diff = _decimal(provider.get("difference"))
        if diff is None:
            direction = "unavailable"
        elif diff > 0:
            direction = "higher"
        elif diff < 0:
            direction = "lower"
        else:
            direction = "unchanged"
        codes = set(provider.get("warnings", []))
        cards.append(
            Card(
                evidence_id=f"{evidence_id}.{provider['provider']}",
                kind="comparison",
                title=f"{metric}: two windows ({provider['provider']})",
                provider=provider["provider"],
                statistic=str(before.get("method", "")).replace("_", " "),
                baseline=_side(bwin, before, unit),
                current=_side(cwin, after, unit),
                difference=provider.get("difference"),
                direction=direction,
                warnings=sorted(codes),
                warning_text=_texts(codes),
                **common,
            )
        )
    return cards


def _side(window: dict[str, Any], side: dict[str, Any], unit: str | None) -> dict[str, Any]:
    return {
        "start_date": window.get("start_date"),
        "end_date": window.get("end_date"),
        "value": side.get("value"),
        "display": format_value(side.get("value"), unit),
        "days_with_data": side.get("days_with_data"),
        "days_in_window": side.get("days_in_window"),
    }


def _availability_card(evidence_id: str, result: dict[str, Any]) -> Card:
    window = result.get("window", {})
    return Card(
        evidence_id=evidence_id,
        kind="availability",
        title="Recorded series in this window",
        tool="availability",
        timezone=window.get("timezone", ""),
        start_date=window.get("start_date"),
        end_date=window.get("end_date"),
        provider=None,
        metric=None,
        unit=None,
        statistic=None,
        series=[
            {
                "provider": s["provider"],
                "metric": s["metric"],
                "aggregation": s["aggregation"],
                "unit": s["unit"],
                "record_count": s["record_count"],
                "local_days_with_records": s["local_days_with_records"],
            }
            for s in result.get("series", [])
        ],
        warnings=[],
        warning_text=list(result.get("notes", [])),
    )
