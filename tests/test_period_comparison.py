"""Period comparison rules on synthetic in-memory daily summaries; no database required."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from healthops.models import Observation
from healthops.schemas import UNITS, Metric
from healthops.tools.contracts import PeriodComparisonParams
from healthops.tools.daily_summary import summarize_days
from healthops.tools.local_days import MAX_SUMMARY_DAYS, LocalDayWindow, ToolInputError
from healthops.tools.period_comparison import (
    PERIOD_DEFINITIONS,
    compare_periods,
    validate_windows,
)

NY = "America/New_York"
DAY = {
    "metric": Metric.STEPS,
    "timezone": NY,
    "baseline_start": date(2026, 10, 1),
    "baseline_end": date(2026, 10, 2),
    "current_start": date(2026, 10, 3),
    "current_end": date(2026, 10, 4),
}


def window(start: str, end: str) -> LocalDayWindow:
    return LocalDayWindow.from_dates(
        NY, date.fromisoformat(start), date.fromisoformat(end), MAX_SUMMARY_DAYS
    )


def obs(metric, aggregation, value, started, ended=None, provider="healthkit") -> Observation:
    return Observation(
        provider=provider,
        source_record_id=f"synthetic-{started}-{value}",
        metric=metric,
        value=Decimal(str(value)),
        unit=UNITS[metric],
        started_at=datetime.fromisoformat(started),
        ended_at=datetime.fromisoformat(ended or started),
        source_timezone=NY,
        aggregation=aggregation,
    )


def daily(provider, value, day: str):
    return obs(
        Metric.STEPS,
        "daily_total",
        value,
        f"{day}T00:00:00-04:00",
        f"{date.fromisoformat(day).replace(day=int(day[-2:]) + 1).isoformat()}T00:00:00-04:00",
        provider=provider,
    )


def compare(
    metric,
    baseline_records,
    current_records,
    b=("2026-10-01", "2026-10-02"),
    c=("2026-10-03", "2026-10-04"),
):
    before = summarize_days(baseline_records, metric, window(*b))
    after = summarize_days(current_records, metric, window(*c))
    return compare_periods(metric, before, after)


def test_point_metric_uses_mean_of_daily_means_not_pooled_mean():
    baseline = [
        obs(Metric.HEART_RATE, "sample", 60, "2026-10-01T12:00:00Z"),
        obs(Metric.HEART_RATE, "sample", 60, "2026-10-01T13:00:00Z"),
        obs(Metric.HEART_RATE, "sample", 60, "2026-10-01T14:00:00Z"),
        obs(Metric.HEART_RATE, "sample", 90, "2026-10-02T12:00:00Z"),
    ]
    current = [obs(Metric.HEART_RATE, "sample", 80, "2026-10-03T12:00:00Z")]
    result = compare(Metric.HEART_RATE, baseline, current, c=("2026-10-03", "2026-10-05"))
    provider = result.providers[0]
    # Daily means are 60 and 90 -> 75. The pooled mean of the four samples would be 67.5.
    assert provider.baseline.value == Decimal("75.000000")
    assert provider.baseline.method == "mean_of_daily_sample_means"
    assert provider.current.value == Decimal("80.000000")
    assert provider.difference == Decimal("5.000000")
    assert provider.baseline.days_with_data == 2 and provider.current.days_with_data == 1
    assert "sparse_window" not in provider.baseline.warnings  # 2 of 2 valid days
    assert "sparse_window" in provider.current.warnings  # 1 of 3 valid days is fewer than half
    assert "pooled mean" in PERIOD_DEFINITIONS["mean_of_daily_sample_means"]


def test_daily_summary_records_are_not_used_and_not_counted_as_missing():
    baseline = [
        obs(
            Metric.RESTING_HEART_RATE,
            "daily_summary",
            55,
            "2026-10-01T00:00:00-04:00",
            "2026-10-02T00:00:00-04:00",
        ),
        obs(Metric.RESTING_HEART_RATE, "sample", 58, "2026-10-02T12:00:00Z"),
    ]
    current = [obs(Metric.RESTING_HEART_RATE, "sample", 57, "2026-10-03T12:00:00Z")]
    before = compare(Metric.RESTING_HEART_RATE, baseline, current).providers[0].baseline
    assert before.value == Decimal("58.000000") and before.days_with_data == 1
    assert before.days_with_unused_daily_summary == 1 and before.days_without_records == 0
    assert "daily_summary_not_used" in before.warnings


def test_cumulative_prefers_unique_aligned_daily_total_and_keeps_zero():
    baseline = [
        daily("healthkit", 0, "2026-10-01"),  # zero is a valid total
        obs(Metric.STEPS, "interval_total", 500, "2026-10-02T14:00:00Z", "2026-10-02T15:00:00Z"),
    ]
    current = [
        daily("healthkit", 8000, "2026-10-03"),
        obs(Metric.STEPS, "interval_total", 100, "2026-10-03T14:00:00Z", "2026-10-03T15:00:00Z"),
        daily("healthkit", 6000, "2026-10-04"),
    ]
    provider = compare(Metric.STEPS, baseline, current).providers[0]
    assert provider.baseline.value == Decimal("250.000000")  # (0 + 500) / 2
    assert provider.baseline.days_from_daily_total == 1
    assert provider.baseline.days_from_interval_total == 1
    assert {"interval_totals_used", "mixed_bases_within_window"} <= set(provider.baseline.warnings)
    assert provider.current.value == Decimal("7000.000000")  # interval on Oct 3 is ignored
    assert provider.current.days_from_interval_total == 0
    assert "mixed_total_kinds" in provider.current.warnings
    assert "basis_differs_between_windows" in provider.warnings


def test_conflicting_or_unaligned_daily_totals_invalidate_the_day_without_fallback():
    baseline = [
        daily("healthkit", 8000, "2026-10-01"),
        daily("healthkit", 8100, "2026-10-01"),  # conflict: two aligned totals
        obs(Metric.STEPS, "interval_total", 500, "2026-10-01T14:00:00Z", "2026-10-01T15:00:00Z"),
        # UTC-day total starting 20:00 Oct 1 New York is unaligned; Oct 2 has only intervals
        # plus this unaligned record assigned to Oct 1, so Oct 1 stays invalid either way.
        obs(Metric.STEPS, "daily_total", 7000, "2026-10-02T00:00:00Z", "2026-10-03T00:00:00Z"),
        obs(Metric.STEPS, "daily_total", 7500, "2026-10-02T05:00:00Z", "2026-10-03T05:00:00Z"),
        obs(Metric.STEPS, "interval_total", 300, "2026-10-02T14:00:00Z", "2026-10-02T15:00:00Z"),
    ]
    current = [daily("healthkit", 6000, "2026-10-03")]
    provider = compare(Metric.STEPS, baseline, current).providers[0]
    # Oct 1: conflicting totals; Oct 2: an unaligned total (01:00 local start). No fallback.
    assert provider.baseline.days_invalid == 2 and provider.baseline.days_with_data == 0
    assert provider.baseline.value is None and provider.difference is None
    assert "insufficient_data" in provider.baseline.warnings


def test_aligned_and_unaligned_daily_totals_on_one_day_invalidate_it():
    baseline = [
        daily("healthkit", 8000, "2026-10-01"),  # aligned to the New York day
        # Same day, unaligned (UTC day starting 20:00 Oct 1 New York); not shifted, not chosen.
        obs(Metric.STEPS, "daily_total", 9000, "2026-10-02T00:00:00Z", "2026-10-03T00:00:00Z"),
        obs(Metric.STEPS, "interval_total", 500, "2026-10-01T14:00:00Z", "2026-10-01T15:00:00Z"),
        daily("healthkit", 7000, "2026-10-02"),
    ]
    current = [daily("healthkit", 6000, "2026-10-03")]
    provider = compare(Metric.STEPS, baseline, current).providers[0]
    # Oct 1 is invalid despite the aligned 8000 and the 500-step interval; only Oct 2 counts.
    assert provider.baseline.days_invalid == 1 and provider.baseline.days_with_data == 1
    assert provider.baseline.value == Decimal("7000.000000")
    assert provider.baseline.days_from_daily_total == 1
    assert provider.baseline.days_from_interval_total == 0
    assert "unaligned_daily_record" in provider.baseline.warnings


def test_overlapping_intervals_make_the_day_invalid():
    baseline = [
        obs(Metric.STEPS, "interval_total", 100, "2026-10-01T14:00:00Z", "2026-10-01T15:00:00Z"),
        obs(Metric.STEPS, "interval_total", 40, "2026-10-01T14:30:00Z", "2026-10-01T16:00:00Z"),
    ]
    provider = compare(Metric.STEPS, baseline, [daily("healthkit", 1, "2026-10-03")]).providers[0]
    assert provider.baseline.days_invalid == 1 and provider.baseline.days_without_records == 1
    assert provider.baseline.value is None


def test_provider_union_keeps_one_sided_providers_with_null_side():
    baseline = [daily("healthkit", 8000, "2026-10-01")]
    current = [daily("google_health", 6000, "2026-10-03")]
    result = compare(Metric.STEPS, baseline, current)
    assert [p.provider for p in result.providers] == ["google_health", "healthkit"]
    google, healthkit = result.providers
    assert google.baseline.value is None and google.baseline.days_without_records == 2
    assert google.current.value == Decimal("6000.000000") and google.difference is None
    assert {"provider_missing_in_window", "insufficient_data"} <= set(google.baseline.warnings)
    assert healthkit.current.value is None and healthkit.difference is None
    assert result.record_count == 2


def test_no_records_anywhere():
    result = compare(Metric.STEPS, [], [])
    assert result.providers == [] and result.warnings == ["no_records_in_window"]


def test_difference_is_current_minus_baseline_and_details_are_attached():
    result = compare(
        Metric.STEPS,
        [daily("healthkit", 8000, "2026-10-01")],
        [daily("healthkit", 6000, "2026-10-03")],
    )
    provider = result.providers[0]
    assert provider.difference == Decimal("-2000.000000")
    assert result.details["baseline"].record_count == 1
    assert result.details["current"].providers[0].days[0].daily_total == Decimal("6000")
    assert "no percentage" in PERIOD_DEFINITIONS["difference"].lower()


def test_windows_may_touch_but_not_overlap():
    validate_windows(PeriodComparisonParams(**DAY))
    reversed_order = DAY | {
        "baseline_start": date(2026, 10, 3),
        "baseline_end": date(2026, 10, 4),
        "current_start": date(2026, 10, 1),
        "current_end": date(2026, 10, 2),
    }
    validate_windows(PeriodComparisonParams(**reversed_order))
    with pytest.raises(ToolInputError, match="must not overlap"):
        validate_windows(PeriodComparisonParams(**(DAY | {"current_start": date(2026, 10, 2)})))
    with pytest.raises(ToolInputError, match="at most 93"):
        validate_windows(PeriodComparisonParams(**(DAY | {"current_end": date(2027, 1, 10)})))


def test_every_period_warning_is_defined():
    codes = {
        "sparse_window",
        "insufficient_data",
        "provider_missing_in_window",
        "interval_totals_used",
        "mixed_bases_within_window",
        "basis_differs_between_windows",
        "daily_summary_not_used",
    }
    assert {f"warning:{code}" for code in codes} <= PERIOD_DEFINITIONS.keys()
    assert "not that the device covered the whole day" in PERIOD_DEFINITIONS["valid_day"]
