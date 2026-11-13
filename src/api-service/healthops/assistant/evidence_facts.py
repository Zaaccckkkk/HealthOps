"""Exact descriptive facts for writer inputs; no interpolation or health inference.

The original packet is immutable. Analyses are per source and recording basis, use
Decimal, and break contiguous segments at missing, unusable or unreported dates.
There is no smoothing, significance threshold, or seven-day assumption.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from .presentation import PROVIDER_NAMES, duration_text, presentation_packet

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
BASES = {
    "sample_mean": "mean of recorded samples, not an all-day average",
    "daily_summary": "source-provided daily summary",
    "vendor_daily_summary": "source-provided daily summary",
    "daily_total": "source-provided daily total",
    "interval_total_sum": "sum of recorded intervals, not guaranteed full-day coverage",
}
REASONS = {
    "overlapping_intervals": "overlapping records prevent a reliable total",
    "no_records_in_window": "no records in this window; this does not establish inactivity",
    "google_heart_rate_not_synced": "Google Health heart-rate samples are not imported",
    "multiple_daily_records": "multiple daily records prevent choosing one value",
    "unsupported_aggregation": "records use an unsupported aggregation",
    "interval_totals_used": "recorded intervals do not guarantee full-day coverage",
    "mixed_bases_within_window": "recording definitions differ within the window",
    "basis_differs_between_windows": "recording definitions differ between the windows",
}
MAX_SEGMENTS = 5


def date_text(value: str, language: str) -> str:
    """Keep the year, independent of process locale and wall-clock date."""
    day = date.fromisoformat(value)
    if day.isoformat() != value:
        raise ValueError("Expected an ISO calendar date")
    if language == "zh":
        return f"{day.year}\u5e74{day.month}\u6708{day.day}\u65e5"
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def number(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite evidence value")
    return result


def value_text(value, unit, language):
    if unit == "s":
        return duration_text(value, language)
    text = format(number(value), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def direction(change):
    return "increase" if change > 0 else "decrease" if change < 0 else "unchanged"


def _point(row, unit, language):
    return {
        "date": row["date"],
        "date_display": date_text(row["date"], language),
        "value": row["value"],
        "value_display": value_text(row["value"], unit, language),
    }


def daily_facts(source, language):
    """Validate before deriving facts; malformed inputs never become a trend."""
    rows = sorted(source.get("days", []), key=lambda row: row["date"])
    dates = [date.fromisoformat(row["date"]) for row in rows]
    start = date.fromisoformat(source["start_date"])
    end = date.fromisoformat(source["end_date"])
    if end < start or len(set(dates)) != len(dates):
        raise ValueError("Reversed window or duplicate daily dates")
    if any(day < start or day > end for day in dates):
        raise ValueError("Daily date outside source window")
    for row in rows:
        quality = row.get("quality")
        if quality not in {"value", "none", "unusable"}:
            raise ValueError("Unknown daily quality")
        if (quality == "value") != (row.get("value") is not None):
            raise ValueError("Daily quality and value disagree")
        if quality == "value":
            number(row["value"])
    usable = [row for row in rows if row["quality"] == "value"]
    window_days = (end - start).days + 1
    coverage = {
        "calendar_days": window_days,
        "days_with_values": len(usable),
        "days_without_records": sum(row["quality"] == "none" for row in rows),
        "days_with_unusable_records": sum(row["quality"] == "unusable" for row in rows),
        "days_not_supplied": window_days - len(rows),
        "all_calendar_days_have_values": len(usable) == window_days,
        "full_day_measurement_coverage_established": False,
    }
    if not rows and "no_records_in_window" in source.get("warnings", []):
        coverage["days_without_records"] = window_days
        coverage["days_not_supplied"] = 0
    result = {"coverage": coverage}
    if not usable:
        result["status"] = "no_usable_values"
        return result
    bases = {row.get("basis") for row in usable}
    if len(bases) != 1 or next(iter(bases)) not in BASES:
        result["status"] = "mixed_or_unknown_recording_basis"
        return result
    basis = next(iter(bases))
    result.update(status="available", recording_basis=BASES[basis])
    unit = source.get("unit")
    values = [number(row["value"]) for row in usable]
    first, last = usable[0], usable[-1]
    result["first"] = _point(first, unit, language)
    result["last"] = _point(last, unit, language)
    for label, extreme in (("minimum", min(values)), ("maximum", max(values))):
        matches = [row for row in usable if number(row["value"]) == extreme]
        result[label] = {
            "value": str(extreme),
            "value_display": value_text(extreme, unit, language),
            "dates_display": [date_text(row["date"], language) for row in matches],
        }
    net = values[-1] - values[0]
    result["first_to_last"] = {
        "direction": direction(net),
        "absolute_change": str(abs(net)),
        "absolute_change_display": value_text(abs(net), unit, language),
    }
    counts = {"increase": 0, "decrease": 0, "unchanged": 0}
    contiguous_counts = dict(counts)
    segments = []
    active = None
    for left, right in zip(usable, usable[1:]):
        change = number(right["value"]) - number(left["value"])
        label = direction(change)
        counts[label] += 1
        adjacent = (date.fromisoformat(right["date"]) - date.fromisoformat(left["date"])).days == 1
        if not adjacent:
            active = None
            continue
        contiguous_counts[label] += 1
        if active is not None and active["direction"] == label:
            active["end"] = right
            active["daily_transitions"] += 1
        else:
            active = {"start": left, "end": right, "direction": label, "daily_transitions": 1}
            segments.append(active)
    pattern = (
        "insufficient_values"
        if len(usable) < 2
        else "fluctuating"
        if counts["increase"] and counts["decrease"]
        else "nondecreasing"
        if counts["increase"]
        else "nonincreasing"
        if counts["decrease"]
        else "all_available_values_equal"
    )
    result["between_available_readings"] = {"pattern": pattern, **counts}
    result["consecutive_calendar_day_transitions"] = contiguous_counts
    # Prefer longest observed runs, then chronological order. Do not imply the list is exhaustive.
    selected = sorted(segments, key=lambda s: (-s["daily_transitions"], s["start"]["date"]))[
        :MAX_SEGMENTS
    ]
    result["continuous_segments"] = [
        {
            "start": _point(s["start"], unit, language),
            "end": _point(s["end"], unit, language),
            "direction": s["direction"],
            "daily_transitions": s["daily_transitions"],
        }
        for s in sorted(selected, key=lambda s: s["start"]["date"])
    ]
    result["omitted_segment_count"] = max(0, len(segments) - MAX_SEGMENTS)
    return result


def reconciliation_facts(source, language):
    readings = source.get("readings", [])
    providers = [r["provider"] for r in readings]
    if len(set(providers)) != len(providers):
        raise ValueError("Duplicate reconciliation source")
    present = [r for r in readings if r.get("value") is not None]
    result = {
        "available_sources": [PROVIDER_NAMES.get(r["provider"], r["provider"]) for r in present],
        "sources_without_values": [
            PROVIDER_NAMES.get(r["provider"], r["provider"])
            for r in readings
            if r.get("value") is None
        ],
        "does_not_establish_device_accuracy": True,
    }
    estimate = source.get("model_estimate", {}).get("q50")
    if estimate is None:
        result["status"] = "no_numerical_estimate_supplied"
    elif len(present) < 2:
        result["status"] = "fewer_than_two_source_values"
    else:
        distances = {r["provider"]: abs(number(r["value"]) - number(estimate)) for r in present}
        best = min(distances.values())
        result.update(
            status="available",
            distances=[
                {
                    "provider_display": PROVIDER_NAMES.get(p, p),
                    "value": str(v),
                    "value_display": value_text(v, source.get("unit"), language),
                }
                for p, v in distances.items()
            ],
            closest_sources=[PROVIDER_NAMES.get(p, p) for p, v in distances.items() if v == best],
            tie=sum(v == best for v in distances.values()) > 1,
        )
    return result


def enriched_packet(facts, language):
    """Shared live/replay boundary. Original v5 presentation remains reproducible."""
    packet = presentation_packet(facts, language)

    def dates(container):
        if isinstance(container, dict):
            for key, value in list(container.items()):
                if key == "computed_facts":
                    continue
                if (
                    key in {"date", "today", "start_date", "end_date", "first_date", "last_date"}
                    and value
                ):
                    try:
                        container[key + "_display"] = date_text(value, language)
                    except (TypeError, ValueError):
                        pass  # Analysis below rejects invalid daily dates; never guess a date.
                elif isinstance(value, (dict, list)):
                    dates(value)
        elif isinstance(container, list):
            for value in container:
                dates(value)

    dates(packet)
    for source in packet.get("sources", []):
        source.pop("computed_facts", None)
        source["warning_reasons_display"] = [
            REASONS[w] for w in source.get("warnings", []) if w in REASONS
        ]
        try:
            if source.get("kind") == "daily_table":
                source["computed_facts"] = daily_facts(source, language)
            elif source.get("kind") == "reconciliation":
                source["computed_facts"] = reconciliation_facts(source, language)
            elif source.get("kind") == "comparison":
                # Preserve the original aggregate definition; never invent a new total.
                definition = source.get("statistic", "")
                source["computed_facts"] = {
                    "statistic": definition,
                    "difference_scope": "per day"
                    if definition == "mean of daily totals"
                    else "as defined by statistic",
                    "difference_is_not_a_window_total": definition == "mean of daily totals",
                }
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            source["computed_facts"] = {
                "status": "unavailable_invalid_evidence",
                "reason": str(exc),
            }
    return packet
