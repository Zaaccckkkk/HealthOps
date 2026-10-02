"""Which accessible records exist for the authenticated user, grouped by source and definition."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import Row
from sqlalchemy.orm import Session

from healthops.tools.contracts import (
    AvailabilityParams,
    AvailabilityResult,
    AvailabilitySeries,
    ToolWindow,
)
from healthops.tools.local_days import ToolInputError, load_timezone, local_midnight
from healthops.tools.queries import availability_rows

NOTES = [
    "Each series is one (provider, metric, aggregation) definition held by this system for the "
    "authenticated user. Absence of a series or of records means no accessible records here; "
    "it does not indicate inactivity, no device wear, or denied authorization.",
    "local_days_with_records counts distinct local dates (requested timezone) containing at least "
    "one record start. It is not a coverage ratio or a completeness guarantee.",
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
    zone = load_timezone(params.timezone)
    if params.start_date and params.end_date and params.end_date < params.start_date:
        raise ToolInputError("end_date must not precede start_date.")
    start_utc = local_midnight(params.start_date, zone) if params.start_date else None
    end_utc = local_midnight(params.end_date + timedelta(days=1), zone) if params.end_date else None
    rows = availability_rows(session, user_id, zone.key, start_utc, end_utc)
    window = ToolWindow(
        timezone=zone.key,
        start_date=params.start_date,
        end_date=params.end_date,
        start_utc=start_utc,
        end_utc=end_utc,
    )
    return build_availability(rows, window)
