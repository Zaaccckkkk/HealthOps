"""Which accessible records exist for the authenticated user, grouped by source and definition."""

from uuid import UUID

from sqlalchemy import Row
from sqlalchemy.orm import Session

from healthops.tools.contracts import (
    AvailabilityParams,
    AvailabilityResult,
    AvailabilitySeries,
    ToolWindow,
)
from healthops.tools.local_days import MAX_AVAILABILITY_DAYS, LocalDayWindow
from healthops.tools.queries import availability_rows

NOTES = [
    "Each series is one (provider, metric, aggregation) definition held by this system for the "
    "authenticated user. Absence of a series or of records means no accessible records here; "
    "it does not indicate inactivity, no device wear, or denied authorization.",
    "local_days_with_records counts distinct local dates (requested timezone) containing at least "
    "one record start. It is not a coverage ratio or a completeness guarantee.",
    "Only records starting inside the requested window (at most 366 local days) are counted; "
    "older history is not examined by this call.",
    "provider is the acquisition channel, not a verified single device.",
]


def build_availability(rows: list[Row], window: ToolWindow) -> AvailabilityResult:
    return AvailabilityResult(
        window=window,
        series=[
            AvailabilitySeries(
                provider=row.provider,
                metric=row.metric,
                aggregation=row.aggregation,
                unit=row.unit,
                record_count=row.record_count,
                first_started_at=row.first_started_at,
                last_started_at=row.last_started_at,
                local_days_with_records=row.local_days_with_records,
            )
            for row in rows
        ],
        notes=NOTES,
    )


def availability_tool(
    session: Session, user_id: UUID, params: AvailabilityParams
) -> AvailabilityResult:
    window = LocalDayWindow.from_dates(
        params.timezone, params.start_date, params.end_date, MAX_AVAILABILITY_DAYS
    )
    rows = availability_rows(session, user_id, window)
    return build_availability(
        rows,
        ToolWindow(
            timezone=window.zone.key,
            start_date=window.start_date,
            end_date=window.end_date,
            start_utc=window.start_utc,
            end_utc=window.end_utc,
        ),
    )
