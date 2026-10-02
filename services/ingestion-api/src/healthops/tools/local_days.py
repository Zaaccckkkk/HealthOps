"""Local-day windows: one explicit IANA timezone per query, half-open day bounds."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_SUMMARY_DAYS = 93


class ToolInputError(ValueError):
    """The request is well-formed but outside what the tool supports; safe to show the client."""


def load_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ToolInputError("timezone must be an IANA name such as America/New_York.") from exc


def local_midnight(day: date, zone: ZoneInfo) -> datetime:
    # A DST transition exactly at midnight is resolved by Python's fold=0 rule; such zones are
    # documented as a limitation rather than handled specially.
    return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)


@dataclass(frozen=True)
class LocalDayWindow:
    """Inclusive local dates [start_date, end_date] as a half-open UTC interval."""

    zone: ZoneInfo
    start_date: date
    end_date: date
    start_utc: datetime
    end_utc: datetime

    @classmethod
    def from_dates(
        cls, timezone: str, start_date: date, end_date: date, max_days: int | None
    ) -> "LocalDayWindow":
        zone = load_timezone(timezone)
        if end_date < start_date:
            raise ToolInputError("end_date must not precede start_date.")
        days = (end_date - start_date).days + 1
        if max_days is not None and days > max_days:
            raise ToolInputError(
                f"The window spans {days} local days; this tool accepts at most {max_days}."
            )
        return cls(
            zone=zone,
            start_date=start_date,
            end_date=end_date,
            start_utc=local_midnight(start_date, zone),
            end_utc=local_midnight(end_date + timedelta(days=1), zone),
        )

    def dates(self) -> Iterator[date]:
        current = self.start_date
        while current <= self.end_date:
            yield current
            current += timedelta(days=1)

    def local_date(self, instant: datetime) -> date:
        return instant.astimezone(self.zone).date()

    def day_bounds(self, day: date) -> tuple[datetime, datetime]:
        return local_midnight(day, self.zone), local_midnight(day + timedelta(days=1), self.zone)
