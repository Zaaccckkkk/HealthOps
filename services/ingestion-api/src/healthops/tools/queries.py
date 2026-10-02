"""Bounded, user-scoped reads. Every statement filters by the authenticated user_id."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Row, distinct, func, select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from healthops.models import Observation
from healthops.schemas import Metric, Provider
from healthops.tools.local_days import LocalDayWindow, ToolInputError

MAX_SUMMARY_RECORDS = 10_000


class RecordLimitExceeded(ToolInputError):
    """Too many records for an in-memory summary; the caller must narrow the request."""


def fetch_metric_history(
    session: Session,
    user_id: UUID,
    metric: Metric,
    window: LocalDayWindow,
    provider: Provider | None = None,
) -> list[Observation]:
    statement = select(Observation).where(
        Observation.user_id == user_id,
        Observation.metric == metric,
        Observation.started_at >= window.start_utc,
        Observation.started_at < window.end_utc,
    )
    if provider is not None:
        statement = statement.where(Observation.provider == provider)
    rows = session.scalars(
        statement.order_by(Observation.started_at, Observation.id).limit(MAX_SUMMARY_RECORDS + 1)
    ).all()
    if len(rows) > MAX_SUMMARY_RECORDS:
        # Refuse rather than truncate: a partial sum would look like a complete statistic.
        raise RecordLimitExceeded(
            f"More than {MAX_SUMMARY_RECORDS} records in the window; "
            "narrow the date range or filter by provider."
        )
    return list(rows)


def availability_rows(
    session: Session,
    user_id: UUID,
    timezone: str,
    start_utc: datetime | None,
    end_utc: datetime | None,
) -> list[Row]:
    # PostgreSQL converts each start instant to the requested zone before counting distinct dates.
    local_day = func.date(func.timezone(timezone, Observation.started_at))
    statement = (
        select(
            Observation.provider,
            Observation.metric,
            Observation.aggregation,
            Observation.unit,
            func.count(Observation.id).label("record_count"),
            func.min(Observation.started_at).label("first_started_at"),
            func.max(Observation.started_at).label("last_started_at"),
            func.count(distinct(local_day)).label("local_days_with_records"),
        )
        .where(Observation.user_id == user_id)
        .group_by(
            Observation.provider, Observation.metric, Observation.aggregation, Observation.unit
        )
        .order_by(Observation.provider, Observation.metric, Observation.aggregation)
    )
    if start_utc is not None:
        statement = statement.where(Observation.started_at >= start_utc)
    if end_utc is not None:
        statement = statement.where(Observation.started_at < end_utc)
    try:
        return list(session.execute(statement).all())
    except DataError as exc:
        session.rollback()
        raise ToolInputError("timezone is not recognized by the database.") from exc
