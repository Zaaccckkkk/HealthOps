"""Per-provider, per-local-day summary of one metric with explicit statistical definitions."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from healthops.models import Observation
from healthops.schemas import UNITS, Metric
from healthops.tools.contracts import (
    DailySummaryParams,
    DailySummaryResult,
    DaySummary,
    MetricClass,
    ProviderDailySummary,
    ToolWindow,
)
from healthops.tools.local_days import MAX_SUMMARY_DAYS, LocalDayWindow
from healthops.tools.queries import fetch_metric_history

POINT_METRICS = frozenset(
    {Metric.HEART_RATE, Metric.RESTING_HEART_RATE, Metric.HRV_SDNN, Metric.HRV_RMSSD}
)
CUMULATIVE_METRICS = frozenset(
    {Metric.STEPS, Metric.ACTIVE_ENERGY, Metric.EXERCISE_DURATION, Metric.SLEEP_DURATION}
)
SUPPORTED_AGGREGATIONS: dict[MetricClass, frozenset[str]] = {
    "point": frozenset({"sample", "daily_summary"}),
    "cumulative": frozenset({"interval_total", "daily_total"}),
}
MEAN_PLACES = Decimal("0.000001")

DEFINITIONS = {
    "local_day": "[local midnight, next local midnight) in the requested timezone; records are "
    "assigned by their start time. Record source_timezone is provenance only.",
    "sample_mean": "Unweighted arithmetic mean of the sample values recorded on the local day, "
    "rounded half-even to 6 decimals. Not a time-weighted all-day average.",
    "sample_min_max": "Smallest and largest sample value recorded on the local day.",
    "interval_total_sum": "Sum of interval_total values whose interval starts on the local day. "
    "Values are taken as reported; durations are never derived from timestamps.",
    "daily_total": "Vendor daily total whose interval equals the local day exactly. Never added to "
    "interval_total_sum.",
    "daily_summary": "Vendor daily summary value whose interval equals the local day exactly.",
    "null": "No supported data for that statistic on that day. Missing data is never zero.",
    "warning:interval_crosses_day_boundary": "An interval ends on a later local day. It is "
    "assigned to its start day without splitting or proportional allocation.",
    "warning:overlapping_intervals": "interval_total records for the same provider and day "
    "overlap in time; a reliable total cannot be derived, so interval_total_sum is null.",
    "warning:unaligned_daily_record": "A daily_total or daily_summary interval does not equal "
    "the local day exactly; it is counted in unaligned_count and otherwise ignored.",
    "warning:multiple_daily_records": "Several aligned daily records exist for one provider "
    "and day (for example several devices); no value is chosen.",
    "warning:mixed_total_kinds": "Both daily_total and interval_total exist for the same "
    "provider and day; they are reported separately and never combined.",
    "warning:unsupported_aggregation": "Records whose aggregation is not supported for this "
    "metric class are counted in unsupported_count and excluded from statistics.",
    "warning:no_records_in_window": "The user has no records for this metric in the window. "
    "This says nothing about activity, device wear or authorization.",
    "provider": "Acquisition channel, not a single device. Grouping by provider does not "
    "deduplicate several devices reporting through one channel.",
}


def metric_class(metric: Metric) -> MetricClass:
    return "point" if metric in POINT_METRICS else "cumulative"


def _quantized_mean(values: list[Decimal]) -> Decimal:
    return (sum(values) / len(values)).quantize(MEAN_PLACES, rounding=ROUND_HALF_EVEN)


def _has_overlap(intervals: list[Observation]) -> bool:
    latest_end = None
    for record in sorted(intervals, key=lambda r: (r.started_at, r.ended_at)):
        non_empty = record.started_at < record.ended_at
        if non_empty and latest_end is not None and record.started_at < latest_end:
            return True
        if latest_end is None or record.ended_at > latest_end:
            latest_end = record.ended_at
    return False


def summarize_day(
    day: date, records: list[Observation], kind: MetricClass, window: LocalDayWindow
) -> DaySummary:
    summary = DaySummary(date=day)
    warnings: set[str] = set()
    day_start, day_end = window.day_bounds(day)
    samples: list[Observation] = []
    intervals: list[Observation] = []
    dailies: dict[str, list[Observation]] = {"daily_total": [], "daily_summary": []}
    for record in records:
        if record.aggregation not in SUPPORTED_AGGREGATIONS[kind]:
            summary.unsupported_count += 1
            warnings.add("unsupported_aggregation")
        elif record.aggregation == "sample":
            samples.append(record)
            if window.local_date(record.ended_at) != day:
                warnings.add("interval_crosses_day_boundary")
        elif record.aggregation == "interval_total":
            intervals.append(record)
            if record.ended_at > day_end:
                warnings.add("interval_crosses_day_boundary")
        elif record.started_at == day_start and record.ended_at == day_end:
            dailies[record.aggregation].append(record)
        else:
            summary.unaligned_count += 1
            warnings.add("unaligned_daily_record")
    if samples:
        values = [record.value for record in samples]
        summary.sample_count = len(values)
        summary.sample_min = min(values)
        summary.sample_max = max(values)
        summary.sample_mean = _quantized_mean(values)
    if intervals:
        summary.interval_total_count = len(intervals)
        if _has_overlap(intervals):
            warnings.add("overlapping_intervals")
        else:
            summary.interval_total_sum = sum(record.value for record in intervals)
    for name, matches in dailies.items():
        setattr(summary, f"{name}_count", len(matches))
        if len(matches) == 1:
            setattr(summary, name, matches[0].value)
        elif matches:
            warnings.add("multiple_daily_records")
    if intervals and dailies["daily_total"]:
        warnings.add("mixed_total_kinds")
    summary.warnings = sorted(warnings)
    return summary


def summarize_days(
    records: Iterable[Observation], metric: Metric, window: LocalDayWindow
) -> DailySummaryResult:
    """Pure computation over already user-scoped records; raises on records outside its scope."""
    kind = metric_class(metric)
    grouped: dict[str, dict[date, list[Observation]]] = defaultdict(lambda: defaultdict(list))
    record_count = 0
    for record in records:
        day = window.local_date(record.started_at)
        if record.metric != metric or not window.start_date <= day <= window.end_date:
            raise ValueError("summarize_days received a record outside the requested scope.")
        grouped[record.provider][day].append(record)
        record_count += 1
    providers = [
        ProviderDailySummary(
            provider=provider,
            record_count=sum(len(day_records) for day_records in grouped[provider].values()),
            days=[
                summarize_day(day, grouped[provider].get(day, []), kind, window)
                for day in window.dates()
            ],
        )
        for provider in sorted(grouped)
    ]
    warnings = sorted({code for p in providers for day in p.days for code in day.warnings})
    if not providers:
        warnings.append("no_records_in_window")
    return DailySummaryResult(
        metric=metric,
        unit=UNITS[metric],
        metric_class=kind,
        window=ToolWindow(
            timezone=window.zone.key,
            start_date=window.start_date,
            end_date=window.end_date,
            start_utc=window.start_utc,
            end_utc=window.end_utc,
        ),
        record_count=record_count,
        providers=providers,
        warnings=warnings,
        definitions=DEFINITIONS,
    )


def daily_summary_tool(
    session: Session, user_id: UUID, params: DailySummaryParams
) -> DailySummaryResult:
    """Orchestrates query and computation; user_id comes from the authenticated route."""
    window = LocalDayWindow.from_dates(
        params.timezone, params.start_date, params.end_date, MAX_SUMMARY_DAYS
    )
    records = fetch_metric_history(session, user_id, params.metric, window, params.provider)
    return summarize_days(records, params.metric, window)
