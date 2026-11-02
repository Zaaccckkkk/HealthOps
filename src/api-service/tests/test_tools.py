"""Deterministic tool computations with synthetic in-memory records; no database required."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from healthops.models import Observation
from healthops.schemas import UNITS, Metric
from healthops.tools.availability import build_availability
from healthops.tools.contracts import ToolWindow
from healthops.tools.daily_summary import DEFINITIONS, metric_class, summarize_days
from healthops.tools.local_days import (
    MAX_AVAILABILITY_DAYS,
    MAX_SUMMARY_DAYS,
    LocalDayWindow,
    ToolInputError,
)

NY = "America/New_York"


def window(start: str, end: str | None = None, timezone: str = NY) -> LocalDayWindow:
    return LocalDayWindow.from_dates(
        timezone, date.fromisoformat(start), date.fromisoformat(end or start), MAX_SUMMARY_DAYS
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


def day(result, provider_index=0, day_index=0):
    return result.providers[provider_index].days[day_index]


def test_spring_forward_day_has_23_hours():
    w = window("2026-03-08")
    assert w.start_utc == datetime(2026, 3, 8, 5, tzinfo=UTC)
    assert w.end_utc == datetime(2026, 3, 9, 4, tzinfo=UTC)
    assert w.end_utc - w.start_utc == timedelta(hours=23)


def test_fall_back_day_has_25_hours_and_repeated_hour_stays_on_that_day():
    w = window("2026-11-01")
    assert w.end_utc - w.start_utc == timedelta(hours=25)
    first_0130 = datetime.fromisoformat("2026-11-01T05:30:00Z")
    second_0130 = datetime.fromisoformat("2026-11-01T06:30:00Z")
    assert w.local_date(first_0130) == w.local_date(second_0130) == date(2026, 11, 1)
    assert w.local_date(datetime.fromisoformat("2026-11-02T04:59:00Z")) == date(2026, 11, 1)
    assert w.local_date(datetime.fromisoformat("2026-11-02T05:00:00Z")) == date(2026, 11, 2)


def test_window_limits_are_explicit_errors():
    with pytest.raises(ToolInputError, match="at most 93"):
        window("2026-01-01", "2026-04-04")
    assert len(list(window("2026-01-01", "2026-04-03").dates())) == MAX_SUMMARY_DAYS
    with pytest.raises(ToolInputError, match="precede"):
        window("2026-01-02", "2026-01-01")
    with pytest.raises(ToolInputError, match="IANA"):
        window("2026-01-01", timezone="Mars/Olympus")


@pytest.mark.parametrize("day", ["9999-12-31", "0001-01-01", "1999-12-31", "2101-01-01"])
def test_dates_outside_supported_calendar_are_rejected_not_overflowed(day):
    with pytest.raises(ToolInputError, match="between 2000-01-01 and 2100-12-31"):
        LocalDayWindow.from_dates(NY, date.fromisoformat(day), date.fromisoformat(day), 1)
    with pytest.raises(ToolInputError, match="between 2000-01-01 and 2100-12-31"):
        LocalDayWindow.from_dates(
            NY, date(2026, 1, 1), date.fromisoformat(day), MAX_AVAILABILITY_DAYS
        )


def test_supported_calendar_edges_work_in_extreme_zones():
    for zone in ("Pacific/Kiritimati", "Etc/GMT+12"):  # UTC+14 and UTC-12
        LocalDayWindow.from_dates(zone, date(2000, 1, 1), date(2000, 1, 1), 1)
        LocalDayWindow.from_dates(zone, date(2100, 12, 31), date(2100, 12, 31), 1)


def test_missing_days_are_null_not_zero():
    w = window("2026-10-01", "2026-10-03")
    result = summarize_days(
        [obs(Metric.HEART_RATE, "sample", 72, "2026-10-02T12:00:00Z")], Metric.HEART_RATE, w
    )
    assert [d.date for d in result.providers[0].days] == [
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 3),
    ]
    empty = day(result, 0, 0)
    assert empty.sample_count == 0 and empty.sample_mean is None and empty.sample_min is None
    assert empty.interval_total_sum is None and empty.daily_total is None
    assert day(result, 0, 1).sample_mean == Decimal("72")
    assert result.record_count == 1 and result.warnings == []


def test_sample_mean_is_unweighted_and_rounded_half_even():
    w = window("2026-10-01")
    records = [
        obs(Metric.HEART_RATE, "sample", 1, "2026-10-01T10:00:00Z"),
        obs(Metric.HEART_RATE, "sample", 1, "2026-10-01T10:00:05Z"),
        obs(Metric.HEART_RATE, "sample", 2, "2026-10-01T22:00:00Z"),
    ]
    summary = day(summarize_days(records, Metric.HEART_RATE, w))
    # Three samples count equally regardless of the time between them.
    assert summary.sample_mean == Decimal("1.333333")
    assert (summary.sample_count, summary.sample_min, summary.sample_max) == (3, 1, 2)
    assert "sample_mean" in DEFINITIONS and "Not a time-weighted" in DEFINITIONS["sample_mean"]


def test_local_date_follows_requested_timezone_not_utc():
    record = obs(Metric.HEART_RATE, "sample", 60, "2026-10-01T03:00:00Z")  # 23:00 Sep 30 in NY
    ny = summarize_days([record], Metric.HEART_RATE, window("2026-09-30", "2026-10-01"))
    assert day(ny, 0, 0).sample_count == 1 and day(ny, 0, 1).sample_count == 0
    utc = summarize_days(
        [record], Metric.HEART_RATE, window("2026-09-30", "2026-10-01", timezone="UTC")
    )
    assert day(utc, 0, 0).sample_count == 0 and day(utc, 0, 1).sample_count == 1


@pytest.mark.parametrize(
    ("metric", "aggregation"),
    [
        (Metric.HEART_RATE, "interval_total"),
        (Metric.HRV_RMSSD, "daily_total"),
        (Metric.STEPS, "sample"),
        (Metric.SLEEP_DURATION, "daily_summary"),
    ],
)
def test_unsupported_aggregation_is_counted_not_summarized(metric, aggregation):
    w = window("2026-10-01")
    record = obs(metric, aggregation, 10, "2026-10-01T04:00:00Z", "2026-10-02T04:00:00Z")
    summary = day(summarize_days([record], metric, w))
    assert summary.unsupported_count == 1 and summary.warnings == ["unsupported_aggregation"]
    assert summary.sample_count == 0 and summary.interval_total_count == 0
    assert summary.daily_total is None and summary.daily_summary is None


def test_interval_totals_are_summed_on_start_day_without_splitting():
    w = window("2026-10-01", "2026-10-02")
    records = [
        obs(Metric.STEPS, "interval_total", 100, "2026-10-02T02:00:00Z", "2026-10-02T03:00:00Z"),
        # 23:30 Oct 1 to 00:30 Oct 2 New York: crosses midnight, assigned wholly to Oct 1.
        obs(Metric.STEPS, "interval_total", 50, "2026-10-02T03:30:00Z", "2026-10-02T04:30:00Z"),
    ]
    result = summarize_days(records, Metric.STEPS, w)
    first, second = result.providers[0].days
    assert (first.interval_total_count, first.interval_total_sum) == (2, Decimal("150"))
    assert first.warnings == ["interval_crosses_day_boundary"]
    assert (second.interval_total_count, second.interval_total_sum) == (0, None)


def test_sleep_value_is_reported_not_derived_from_timestamps():
    w = window("2026-10-01", "2026-10-02")
    record = obs(
        Metric.SLEEP_DURATION,
        "interval_total",
        25200,
        "2026-10-02T03:00:00Z",
        "2026-10-02T11:00:00Z",
    )
    first = day(summarize_days([record], Metric.SLEEP_DURATION, w))
    assert first.interval_total_sum == Decimal("25200")  # 7 h asleep in an 8 h interval
    assert first.warnings == ["interval_crosses_day_boundary"]


def test_overlapping_intervals_produce_no_total():
    w = window("2026-10-01")
    overlapping = [
        obs(Metric.STEPS, "interval_total", 100, "2026-10-01T14:00:00Z", "2026-10-01T15:00:00Z"),
        obs(Metric.STEPS, "interval_total", 40, "2026-10-01T14:30:00Z", "2026-10-01T16:00:00Z"),
    ]
    summary = day(summarize_days(overlapping, Metric.STEPS, w))
    assert summary.interval_total_count == 2 and summary.interval_total_sum is None
    assert summary.warnings == ["overlapping_intervals"]
    touching = [
        obs(Metric.STEPS, "interval_total", 100, "2026-10-01T14:00:00Z", "2026-10-01T15:00:00Z"),
        obs(Metric.STEPS, "interval_total", 40, "2026-10-01T15:00:00Z", "2026-10-01T16:00:00Z"),
    ]
    summary = day(summarize_days(touching, Metric.STEPS, w))
    assert summary.interval_total_sum == Decimal("140") and summary.warnings == []


def test_zero_length_interval_totals_are_excluded_not_summed():
    w = window("2026-10-01")
    records = [
        obs(Metric.STEPS, "interval_total", 100, "2026-10-01T14:00:00Z"),
        obs(Metric.STEPS, "interval_total", 100, "2026-10-01T14:00:00Z"),
        obs(Metric.STEPS, "interval_total", 40, "2026-10-01T15:00:00Z", "2026-10-01T16:00:00Z"),
    ]
    summary = day(summarize_days(records, Metric.STEPS, w))
    # Two identical zero-length records cannot be checked for overlap; they never reach the sum.
    assert (summary.interval_total_count, summary.interval_total_sum) == (1, Decimal("40"))
    assert summary.unsupported_count == 2 and summary.warnings == ["zero_length_interval"]


def test_overlap_across_midnight_blanks_both_days():
    w = window("2026-10-01", "2026-10-03")
    records = [
        # 23:30 Oct 1 to 00:30 Oct 2 New York, and 00:00 to 01:00 Oct 2: 30 minutes overlap.
        obs(Metric.STEPS, "interval_total", 100, "2026-10-02T03:30:00Z", "2026-10-02T04:30:00Z"),
        obs(Metric.STEPS, "interval_total", 80, "2026-10-02T04:00:00Z", "2026-10-02T05:00:00Z"),
        obs(Metric.STEPS, "interval_total", 10, "2026-10-03T12:00:00Z", "2026-10-03T12:30:00Z"),
    ]
    oct1, oct2, oct3 = summarize_days(records, Metric.STEPS, w).providers[0].days
    assert oct1.interval_total_sum is None and oct1.interval_total_count == 1
    assert oct1.warnings == ["interval_crosses_day_boundary", "overlapping_intervals"]
    assert oct2.interval_total_sum is None and oct2.warnings == ["overlapping_intervals"]
    assert oct3.interval_total_sum == Decimal("10") and oct3.warnings == []


def test_context_intervals_outside_the_window_blank_the_overlapped_day():
    w = window("2026-10-02", "2026-10-03")
    before = obs(
        Metric.STEPS, "interval_total", 100, "2026-10-02T03:30:00Z", "2026-10-02T04:30:00Z"
    )  # starts 23:30 Oct 1, before the window
    after = obs(
        Metric.STEPS, "interval_total", 5, "2026-10-04T04:00:00Z", "2026-10-04T05:00:00Z"
    )  # starts 00:00 Oct 4, after the window, overlapping an Oct 3 interval that runs late
    inside = [
        obs(Metric.STEPS, "interval_total", 80, "2026-10-02T04:00:00Z", "2026-10-02T05:00:00Z"),
        obs(Metric.STEPS, "interval_total", 10, "2026-10-04T03:30:00Z", "2026-10-04T04:30:00Z"),
    ]
    result = summarize_days(inside, Metric.STEPS, w, context_intervals=[before, after])
    oct2, oct3 = result.providers[0].days
    assert oct2.interval_total_sum is None and oct2.warnings == ["overlapping_intervals"]
    assert oct3.interval_total_sum is None
    assert oct3.warnings == ["interval_crosses_day_boundary", "overlapping_intervals"]
    assert result.record_count == 2  # context records are never summarized or counted
    with pytest.raises(ValueError, match="context interval inside"):
        summarize_days(inside, Metric.STEPS, w, context_intervals=inside[:1])


def test_same_day_result_does_not_depend_on_window_size():
    a = obs(Metric.STEPS, "interval_total", 100, "2026-10-02T03:30:00Z", "2026-10-02T04:30:00Z")
    b = obs(Metric.STEPS, "interval_total", 80, "2026-10-02T04:00:00Z", "2026-10-02T05:00:00Z")
    wide = day(summarize_days([a, b], Metric.STEPS, window("2026-10-01", "2026-10-02")))
    narrow = day(summarize_days([a], Metric.STEPS, window("2026-10-01"), context_intervals=[b]))
    assert wide.interval_total_sum is None and narrow.interval_total_sum is None
    assert "overlapping_intervals" in wide.warnings and "overlapping_intervals" in narrow.warnings


def test_overlap_detection_is_per_provider():
    w = window("2026-10-01")
    records = [
        obs(Metric.STEPS, "interval_total", 100, "2026-10-01T14:00:00Z", "2026-10-01T15:00:00Z"),
        obs(
            Metric.STEPS,
            "interval_total",
            40,
            "2026-10-01T14:30:00Z",
            "2026-10-01T16:00:00Z",
            provider="google_health",
        ),
    ]
    result = summarize_days(records, Metric.STEPS, w)
    assert [day(result, i).interval_total_sum for i in range(2)] == [Decimal("40"), Decimal("100")]


def test_daily_total_must_match_the_local_day_exactly():
    w = window("2026-09-30", "2026-10-01")
    aligned = obs(
        Metric.STEPS, "daily_total", 8000, "2026-10-01T00:00:00-04:00", "2026-10-02T00:00:00-04:00"
    )
    utc_day = obs(Metric.STEPS, "daily_total", 7000, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
    result = summarize_days([aligned, utc_day], Metric.STEPS, w)
    sep30, oct1 = result.providers[0].days
    assert oct1.daily_total == Decimal("8000") and oct1.daily_total_count == 1
    # The UTC-day record starts at 20:00 Sep 30 New York; it is neither shifted nor split.
    assert sep30.daily_total is None and sep30.unaligned_count == 1
    assert sep30.warnings == ["unaligned_daily_record"]


def test_multiple_daily_records_choose_no_value():
    w = window("2026-10-01")
    records = [
        obs(
            Metric.STEPS, "daily_total", v, "2026-10-01T00:00:00-04:00", "2026-10-02T00:00:00-04:00"
        )
        for v in (8000, 8100)
    ]
    summary = day(summarize_days(records, Metric.STEPS, w))
    assert summary.daily_total is None and summary.daily_total_count == 2
    assert summary.warnings == ["multiple_daily_records"]


def test_daily_total_and_interval_total_are_never_added():
    w = window("2026-10-01")
    records = [
        obs(
            Metric.STEPS,
            "daily_total",
            8000,
            "2026-10-01T00:00:00-04:00",
            "2026-10-02T00:00:00-04:00",
        ),
        obs(Metric.STEPS, "interval_total", 500, "2026-10-01T14:00:00Z", "2026-10-01T15:00:00Z"),
    ]
    summary = day(summarize_days(records, Metric.STEPS, w))
    assert summary.daily_total == Decimal("8000") and summary.interval_total_sum == Decimal("500")
    assert summary.warnings == ["mixed_total_kinds"]


def test_providers_are_reported_separately():
    w = window("2026-10-01")
    records = [
        obs(
            Metric.STEPS,
            "daily_total",
            8000,
            "2026-10-01T00:00:00-04:00",
            "2026-10-02T00:00:00-04:00",
        ),
        obs(
            Metric.STEPS,
            "daily_total",
            6000,
            "2026-10-01T00:00:00-04:00",
            "2026-10-02T00:00:00-04:00",
            provider="google_health",
        ),
    ]
    result = summarize_days(records, Metric.STEPS, w)
    assert [p.provider for p in result.providers] == ["google_health", "healthkit"]
    assert [day(result, i).daily_total for i in range(2)] == [Decimal("6000"), Decimal("8000")]
    assert result.warnings == []


def test_point_daily_summary_alignment():
    w = window("2026-10-01")
    record = obs(
        Metric.HRV_RMSSD,
        "daily_summary",
        42.5,
        "2026-10-01T00:00:00-04:00",
        "2026-10-02T00:00:00-04:00",
    )
    summary = day(summarize_days([record], Metric.HRV_RMSSD, w))
    assert summary.daily_summary == Decimal("42.5") and summary.daily_summary_count == 1


def test_sdnn_and_rmssd_are_separate_metrics():
    assert metric_class(Metric.HRV_SDNN) == metric_class(Metric.HRV_RMSSD) == "point"
    w = window("2026-10-01")
    rmssd = obs(Metric.HRV_RMSSD, "sample", 40, "2026-10-01T12:00:00Z")
    with pytest.raises(ValueError, match="outside the requested scope"):
        summarize_days([rmssd], Metric.HRV_SDNN, w)
    with pytest.raises(ValueError, match="outside the requested scope"):
        summarize_days(
            [obs(Metric.HRV_SDNN, "sample", 40, "2026-10-03T12:00:00Z")], Metric.HRV_SDNN, w
        )


def test_no_records_is_an_explicit_warning_not_zero():
    result = summarize_days([], Metric.RESTING_HEART_RATE, window("2026-10-01"))
    assert result.providers == [] and result.warnings == ["no_records_in_window"]
    assert "authorization" in DEFINITIONS["warning:no_records_in_window"]


def test_every_emitted_warning_is_defined():
    codes = {
        "interval_crosses_day_boundary",
        "overlapping_intervals",
        "unaligned_daily_record",
        "multiple_daily_records",
        "mixed_total_kinds",
        "unsupported_aggregation",
        "zero_length_interval",
        "no_records_in_window",
    }
    assert {f"warning:{code}" for code in codes} <= DEFINITIONS.keys()


def test_availability_shape_and_notes():
    row = SimpleNamespace(
        provider="healthkit",
        metric="heart_rate",
        aggregation="sample",
        unit="bpm",
        record_count=3,
        first_started_at=datetime(2026, 10, 1, tzinfo=UTC),
        last_started_at=datetime(2026, 10, 2, tzinfo=UTC),
        local_days_with_records=2,
    )
    w = window("2026-10-01", "2026-10-02")
    tool_window = ToolWindow(
        timezone=NY,
        start_date=w.start_date,
        end_date=w.end_date,
        start_utc=w.start_utc,
        end_utc=w.end_utc,
    )
    result = build_availability([row], tool_window)
    assert result.series[0].local_days_with_records == 2
    assert any("denied authorization" in note for note in result.notes)
    assert any("not a coverage ratio" in note for note in result.notes)
    assert any("older history is not examined" in note for note in result.notes)
