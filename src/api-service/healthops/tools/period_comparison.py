"""Compare two local-day windows of one metric, built on the daily-summary rules."""

from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from healthops.schemas import UNITS, Metric
from healthops.tools import queries
from healthops.tools.contracts import (
    DailySummaryResult,
    DaySummary,
    PeriodComparisonParams,
    PeriodComparisonResult,
    ProviderComparison,
    ProviderDailySummary,
    WindowSummary,
)
from healthops.tools.daily_summary import DEFINITIONS, metric_class, summarize_days
from healthops.tools.local_days import MAX_SUMMARY_DAYS, LocalDayWindow, ToolInputError
from healthops.tools.queries import MetricHistory, fetch_metric_history

MEAN_PLACES = Decimal("0.000001")

PERIOD_DEFINITIONS = {
    "mean_of_daily_sample_means": "Unweighted mean of sample_mean over valid days (days with "
    "at least one sample). Every day weighs the same; this is not the pooled mean of all samples, "
    "which would weigh densely sampled days more, and not a time-weighted average.",
    "mean_of_daily_totals": "Unweighted mean of one total per valid day. A day's total is its "
    "unique, day-aligned daily_total; only days with no daily_total record at all fall back to "
    "interval_total_sum. A zero total is a valid value.",
    "valid_day": "A day that contributes a value. Validity means a usable value exists, not that "
    "the device covered the whole day.",
    "days_invalid": "Days with records but no usable value: conflicting or unaligned daily "
    "totals, overlapping intervals, or only unsupported records.",
    "days_from_interval_total": "Days whose total came from interval_total_sum. Interval sums "
    "cover only the recorded intervals and do not guarantee full-day coverage.",
    "days_with_unused_daily_summary": "Point-metric days that have a vendor daily_summary but "
    "no samples. daily_summary does not enter the window mean; such days are not valid days, "
    "but they are not days without data either.",
    "difference": "current.value minus baseline.value, null when either window value is null. "
    "No percentage is reported.",
    "warning:sparse_window": "Fewer than half of the days in the window are valid.",
    "warning:insufficient_data": "A window has no valid day, so its value and the difference "
    "are null.",
    "warning:provider_missing_in_window": "The provider has records in only one of the two "
    "windows; the other side is null.",
    "warning:interval_totals_used": "At least one day's total is an interval sum, which does not "
    "guarantee full-day coverage.",
    "warning:mixed_bases_within_window": "Some days use daily_total and others "
    "interval_total_sum inside one window.",
    "warning:basis_differs_between_windows": "The two windows do not use the same set of total "
    "bases, so part of the difference may reflect how data was recorded.",
    "warning:daily_summary_not_used": "Vendor daily_summary records exist but are not part of "
    "the window mean.",
}


def _mean(values: list[Decimal]) -> Decimal:
    return (sum(values) / len(values)).quantize(MEAN_PLACES, rounding=ROUND_HALF_EVEN)


def _point_day_value(day: DaySummary) -> tuple[Decimal | None, str]:
    if day.sample_count > 0:
        return day.sample_mean, "sample"
    if day.daily_summary_count > 0 or day.unaligned_count > 0:
        return None, "unused_daily_summary"
    if day.unsupported_count > 0:
        return None, "invalid"
    return None, "none"


def _cumulative_day_value(day: DaySummary) -> tuple[Decimal | None, str]:
    has_daily_records = day.daily_total_count > 0 or day.unaligned_count > 0
    if has_daily_records:
        # Any unaligned daily total, or several aligned ones, makes the day invalid, even when
        # one aligned total exists. There is no fallback to interval sums on such a day.
        if day.unaligned_count == 0 and day.daily_total is not None:
            return day.daily_total, "daily_total"
        return None, "invalid"
    if day.interval_total_count > 0:
        if day.interval_total_sum is not None:
            return day.interval_total_sum, "interval_total"
        return None, "invalid"  # overlapping intervals
    if day.unsupported_count > 0:
        return None, "invalid"
    return None, "none"


def summarize_window(provider_days: ProviderDailySummary | None, kind: str, days_in_window: int):
    """Pure: reduce one provider's daily summaries of one window to a WindowSummary."""
    method = "mean_of_daily_sample_means" if kind == "point" else "mean_of_daily_totals"
    values: list[Decimal] = []
    counts = {"invalid": 0, "none": 0, "daily_total": 0, "interval_total": 0, "unused": 0}
    warnings: set[str] = set()
    days = provider_days.days if provider_days is not None else []
    for day in days:
        value, basis = _point_day_value(day) if kind == "point" else _cumulative_day_value(day)
        warnings.update(day.warnings)
        if value is not None:
            values.append(value)
        if basis == "daily_total":
            counts["daily_total"] += 1
        elif basis == "interval_total":
            counts["interval_total"] += 1
        elif basis == "unused_daily_summary":
            counts["unused"] += 1
        elif basis == "invalid":
            counts["invalid"] += 1
        elif basis == "none":
            counts["none"] += 1
    if provider_days is None:
        counts["none"] = days_in_window
        warnings.add("provider_missing_in_window")
    if counts["interval_total"]:
        warnings.add("interval_totals_used")
    if counts["interval_total"] and counts["daily_total"]:
        warnings.add("mixed_bases_within_window")
    if counts["unused"]:
        warnings.add("daily_summary_not_used")
    if not values:
        warnings.add("insufficient_data")
    elif 2 * len(values) < days_in_window:
        warnings.add("sparse_window")
    return WindowSummary(
        value=_mean(values) if values else None,
        method=method,
        days_in_window=days_in_window,
        days_with_data=len(values),
        days_without_records=counts["none"],
        days_invalid=counts["invalid"],
        days_from_daily_total=counts["daily_total"],
        days_from_interval_total=counts["interval_total"],
        days_with_unused_daily_summary=counts["unused"],
        record_count=provider_days.record_count if provider_days is not None else 0,
        warnings=sorted(warnings),
    )


def _bases(summary: WindowSummary) -> frozenset[str]:
    bases = set()
    if summary.days_from_daily_total:
        bases.add("daily_total")
    if summary.days_from_interval_total:
        bases.add("interval_total")
    return frozenset(bases)


def compare_periods(
    metric: Metric, baseline: DailySummaryResult, current: DailySummaryResult
) -> PeriodComparisonResult:
    """Pure: providers are the union of both windows; a missing side is null, never zero."""
    kind = metric_class(metric)
    by_provider = {
        "baseline": {p.provider: p for p in baseline.providers},
        "current": {p.provider: p for p in current.providers},
    }
    # Day counts come from the windows themselves, not from whichever provider has data.
    days = {
        "baseline": (baseline.window.end_date - baseline.window.start_date).days + 1,
        "current": (current.window.end_date - current.window.start_date).days + 1,
    }
    providers = []
    for provider in sorted(set(by_provider["baseline"]) | set(by_provider["current"])):
        before = summarize_window(by_provider["baseline"].get(provider), kind, days["baseline"])
        after = summarize_window(by_provider["current"].get(provider), kind, days["current"])
        warnings = set(before.warnings) | set(after.warnings)
        if before.value is not None and after.value is not None:
            difference = after.value - before.value
        else:
            difference = None
        if _bases(before) and _bases(after) and _bases(before) != _bases(after):
            warnings.add("basis_differs_between_windows")
        providers.append(
            ProviderComparison(
                provider=provider,
                baseline=before,
                current=after,
                difference=difference,
                warnings=sorted(warnings),
            )
        )
    warnings = sorted({code for p in providers for code in p.warnings})
    if not providers:
        warnings.append("no_records_in_window")
    return PeriodComparisonResult(
        metric=metric,
        unit=UNITS[metric],
        metric_class=kind,
        baseline_window=baseline.window,
        current_window=current.window,
        record_count=baseline.record_count + current.record_count,
        providers=providers,
        warnings=warnings,
        definitions={**DEFINITIONS, **PERIOD_DEFINITIONS},
        details={"baseline": baseline, "current": current},
    )


def validate_windows(params: PeriodComparisonParams) -> tuple[LocalDayWindow, LocalDayWindow]:
    baseline = LocalDayWindow.from_dates(
        params.timezone, params.baseline_start, params.baseline_end, MAX_SUMMARY_DAYS
    )
    current = LocalDayWindow.from_dates(
        params.timezone, params.current_start, params.current_end, MAX_SUMMARY_DAYS
    )
    if baseline.start_date <= current.end_date and current.start_date <= baseline.end_date:
        raise ToolInputError("Windows must not overlap; adjacent windows are allowed.")
    return baseline, current


def period_comparison_tool(
    session: Session, user_id: UUID, params: PeriodComparisonParams
) -> PeriodComparisonResult:
    """Orchestrates two bounded fetches under one shared budget, then pure comparison."""
    baseline, current = validate_windows(params)
    with_context = metric_class(params.metric) == "cumulative"
    histories: dict[str, MetricHistory] = {}
    seen: list = []
    for name, window in (("baseline", baseline), ("current", current)):
        # The second window reads at most the budget left after the first, counted by unique
        # record id; records already loaded are reused instead of being read again.
        histories[name] = fetch_metric_history(
            session,
            user_id,
            params.metric,
            window,
            params.provider,
            include_overlap_context=with_context,
            budget=queries.MAX_SUMMARY_RECORDS - len({r.id for r in seen}),
            seen=seen,
            budget_scope="across both windows, including overlap context",
        )
        seen = [*seen, *histories[name].records, *histories[name].context_intervals]
    summaries = {
        name: summarize_days(
            histories[name].records, params.metric, window, histories[name].context_intervals
        )
        for name, window in (("baseline", baseline), ("current", current))
    }
    return compare_periods(params.metric, summaries["baseline"], summaries["current"])
