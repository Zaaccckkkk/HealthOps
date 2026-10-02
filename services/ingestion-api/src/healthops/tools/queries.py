"""Bounded, user-scoped reads. Every statement filters by the authenticated user_id."""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import Row, distinct, func, or_, select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from healthops.models import Observation
from healthops.schemas import Metric, Provider
from healthops.tools.local_days import LocalDayWindow, ToolInputError

MAX_SUMMARY_RECORDS = 10_000


class RecordLimitExceeded(ToolInputError):
    """Too many records for an in-memory summary; the caller must narrow the request."""


@dataclass
class MetricHistory:
    """Records starting inside the window, plus every outside interval that intersects them."""

    records: list[Observation]
    context_intervals: list[Observation] = field(default_factory=list)


def _limit_exceeded() -> RecordLimitExceeded:
    # Refuse rather than truncate: a partial sum would look like a complete statistic.
    return RecordLimitExceeded(
        f"More than {MAX_SUMMARY_RECORDS} records in the window; "
        "narrow the date range or filter by provider."
    )


def fetch_metric_history(
    session: Session,
    user_id: UUID,
    metric: Metric,
    window: LocalDayWindow,
    provider: Provider | None = None,
    include_overlap_context: bool = False,
) -> MetricHistory:
    scope = [Observation.user_id == user_id, Observation.metric == metric]
    if provider is not None:
        scope.append(Observation.provider == provider)
    in_window = (
        select(Observation)
        .where(
            *scope,
            Observation.started_at >= window.start_utc,
            Observation.started_at < window.end_utc,
        )
        .order_by(Observation.started_at, Observation.id)
        .limit(MAX_SUMMARY_RECORDS + 1)
    )
    records = list(session.scalars(in_window).all())
    if len(records) > MAX_SUMMARY_RECORDS:
        raise _limit_exceeded()
    history = MetricHistory(records=records)
    latest_end = max(
        (r.ended_at for r in records if r.aggregation == "interval_total"), default=None
    )
    if include_overlap_context and latest_end is not None:
        # Every interval_total outside the window that can intersect an in-window interval:
        # it must end after the window starts and start before the last in-window interval
        # ends. No lookback or lookahead bound, so the check is complete; the record budget is
        # shared with the summarized records and exceeding it is refused, not truncated.
        # ix_observations_user_metric_end bounds the scan by (user_id, metric, ended_at).
        remaining = MAX_SUMMARY_RECORDS - len(records)
        context = (
            select(Observation)
            .where(
                *scope,
                Observation.aggregation == "interval_total",
                Observation.started_at < Observation.ended_at,
                Observation.ended_at > window.start_utc,
                Observation.started_at < latest_end,
                or_(
                    Observation.started_at < window.start_utc,
                    Observation.started_at >= window.end_utc,
                ),
            )
            .order_by(Observation.started_at, Observation.id)
            .limit(remaining + 1)
        )
        history.context_intervals = list(session.scalars(context).all())
        if len(history.context_intervals) > remaining:
            raise _limit_exceeded()
    return history


def availability_rows(session: Session, user_id: UUID, window: LocalDayWindow) -> list[Row]:
    # PostgreSQL converts each start instant to the requested zone before counting distinct dates.
    # The user_id + started_at range bounds the scan through ix_observations_user_time.
    local_day = func.date(func.timezone(window.zone.key, Observation.started_at))
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
        .where(
            Observation.user_id == user_id,
            Observation.started_at >= window.start_utc,
            Observation.started_at < window.end_utc,
        )
        .group_by(
            Observation.provider, Observation.metric, Observation.aggregation, Observation.unit
        )
        .order_by(Observation.provider, Observation.metric, Observation.aggregation)
    )
    try:
        return list(session.execute(statement).all())
    except DataError as exc:
        session.rollback()
        raise ToolInputError("timezone is not recognized by the database.") from exc
