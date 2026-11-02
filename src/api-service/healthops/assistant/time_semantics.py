"""Relative time phrases resolved by the server into concrete local dates."""

from datetime import UTC, date, datetime, timedelta

from healthops.assistant.contracts import TimeSemantics
from healthops.tools.local_days import load_timezone

DEFINITIONS = {
    "today": "The current local date in the request timezone (or the evaluation override).",
    "yesterday": "The local day before today.",
    "last_night": "For sleep: the local day before today, because sleep intervals are assigned "
    "to the day they start (the evening before counts as that day's sleep).",
    "this_week": "Monday of the current week through today, inclusive.",
    "last_week": "The previous calendar week, Monday through Sunday, inclusive.",
    "week_before_last": "The calendar week before last week (two weeks ago), Monday to Sunday.",
    "three_weeks_ago": "The calendar week three weeks before the current week, Monday to Sunday.",
    "past_7_days": "The seven local days ending today, inclusive of today.",
    "previous_7_days": "The seven local days immediately before the past 7 days.",
    "past_30_days": "The thirty local days ending today, inclusive of today.",
    "previous_30_days": "The thirty local days immediately before the past 30 days.",
    "timezone": "All dates use the request timezone; the server injects it into every tool call.",
    "explicit_dates": "Dates written in the question are used as written. Any other relative "
    "phrase is unsupported and must be declined rather than guessed.",
}


def resolve(timezone: str, today: date | None, now: datetime | None = None) -> TimeSemantics:
    zone = load_timezone(timezone)
    if today is None:
        today = (now or datetime.now(UTC)).astimezone(zone).date()
    this_week_start = today - timedelta(days=today.weekday())
    last_week_start = this_week_start - timedelta(days=7)
    past_7_days_start = today - timedelta(days=6)
    past_30_days_start = today - timedelta(days=29)
    return TimeSemantics(
        timezone=zone.key,
        today=today,
        yesterday=today - timedelta(days=1),
        this_week_start=this_week_start,
        last_week_start=last_week_start,
        last_week_end=this_week_start - timedelta(days=1),
        week_before_last_start=last_week_start - timedelta(days=7),
        week_before_last_end=last_week_start - timedelta(days=1),
        three_weeks_ago_start=this_week_start - timedelta(days=21),
        three_weeks_ago_end=this_week_start - timedelta(days=15),
        past_7_days_start=past_7_days_start,
        previous_7_days_start=past_7_days_start - timedelta(days=7),
        previous_7_days_end=past_7_days_start - timedelta(days=1),
        past_30_days_start=past_30_days_start,
        previous_30_days_start=past_30_days_start - timedelta(days=30),
        previous_30_days_end=past_30_days_start - timedelta(days=1),
        definitions=DEFINITIONS,
    )


def describe(semantics: TimeSemantics) -> str:
    s = semantics
    return (
        f"Timezone: {s.timezone}. Today: {s.today.isoformat()} ({s.today.strftime('%A')}). "
        f"Yesterday: {s.yesterday.isoformat()}. "
        f"Last night (sleep, assigned to its start day): {s.yesterday.isoformat()}. "
        f"This week: {s.this_week_start.isoformat()} to {s.today.isoformat()}. "
        f"Last week: {s.last_week_start.isoformat()} to {s.last_week_end.isoformat()}. "
        f"Week before last (two weeks ago): {s.week_before_last_start.isoformat()} to "
        f"{s.week_before_last_end.isoformat()}. "
        f"Three weeks ago: {s.three_weeks_ago_start.isoformat()} to "
        f"{s.three_weeks_ago_end.isoformat()}. "
        f"Past 7 days: {s.past_7_days_start.isoformat()} to {s.today.isoformat()}. "
        f"Previous 7 days (the 7 days before that): {s.previous_7_days_start.isoformat()} to "
        f"{s.previous_7_days_end.isoformat()}. "
        f"Past 30 days: {s.past_30_days_start.isoformat()} to {s.today.isoformat()}. "
        f"Previous 30 days: {s.previous_30_days_start.isoformat()} to "
        f"{s.previous_30_days_end.isoformat()}. "
        "Dates written explicitly in the question are used as written; any other relative phrase "
        "is unsupported and must be declined."
    )
