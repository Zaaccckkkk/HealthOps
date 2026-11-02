"""Deterministic synthetic history for the evaluation user. No real person's data."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 2)  # fixed evaluation "today"; questions pin it as well


def _at(day: date, hour: int, minute: int = 0) -> str:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZONE).isoformat()


def _record(provider, record_id, metric, value, unit, started, ended, aggregation) -> dict:
    return {
        "provider": provider,
        "source_record_id": record_id,
        "metric": metric,
        "value": value,
        "unit": unit,
        "started_at": started,
        "ended_at": ended,
        "source_timezone": "America/New_York",
        "aggregation": aggregation,
    }


def observations() -> list[dict]:
    """About six weeks of data with deliberate gaps, a sparse week and a zero-step day."""
    records: list[dict] = []
    start = TODAY - timedelta(days=42)
    for offset in range(43):
        day = start + timedelta(days=offset)
        week_index = offset // 7
        sparse_week = week_index == 3  # only two days of data in that week
        if sparse_week and offset % 7 not in (1, 4):
            continue
        if day == TODAY - timedelta(days=10):
            continue  # a missing day
        rhr = 56 + (offset % 5) - (2 if week_index >= 5 else 0)
        records.append(
            _record(
                "healthkit",
                f"eval-rhr-{day}",
                "resting_heart_rate",
                rhr,
                "bpm",
                _at(day, 6),
                _at(day, 6),
                "sample",
            )
        )
        for hour, bpm in ((9, 70 + offset % 7), (14, 78 + offset % 4), (20, 66 + offset % 3)):
            records.append(
                _record(
                    "healthkit",
                    f"eval-hr-{day}-{hour}",
                    "heart_rate",
                    bpm,
                    "bpm",
                    _at(day, hour),
                    _at(day, hour),
                    "sample",
                )
            )
        records.append(
            _record(
                "healthkit",
                f"eval-sdnn-{day}",
                "hrv_sdnn",
                40 + (offset % 9),
                "ms",
                _at(day, 6, 30),
                _at(day, 6, 30),
                "sample",
            )
        )
        steps = 0 if day == TODAY - timedelta(days=3) else 4000 + 350 * (offset % 8)
        records.append(
            _record(
                "google_health",
                f"eval-steps-{day}",
                "steps",
                steps,
                "count",
                _at(day, 0),
                _at(day + timedelta(days=1), 0),
                "daily_total",
            )
        )
        records.append(
            _record(
                "healthkit",
                f"eval-sleep-{day}",
                "sleep_duration",
                6 * 3600 + 900 * (offset % 5),
                "s",
                _at(day - timedelta(days=1), 23),
                _at(day, 6),
                "interval_total",
            )
        )
    return records
