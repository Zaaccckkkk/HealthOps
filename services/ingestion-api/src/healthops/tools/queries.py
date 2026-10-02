"""Bounded, user-scoped reads. Every statement filters by the authenticated user_id."""

from collections.abc import Sequence
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


def _limit_exceeded(scope: str) -> RecordLimitExceeded:
    # Refuse rather than truncate: a partial sum would look like a complete statistic.
    return RecordLimitExceeded(
        f"More than {MAX_SUMMARY_RECORDS} records {scope}; "
        "narrow the date range or filter by provider."
    )


def _in_window(record: Observation, window: LocalDayWindow) -> bool:
    return window.start_utc <= record.started_at < window.end_utc


def _is_context(record: Observation, window: LocalDayWindow, latest_end) -> bool:
    return (
        record.aggregation == "interval_total"
        and record.started_at < record.ended_at
        and record.ended_at > window.start_utc
        and record.started_at < latest_end
        and not _in_window(record, window)
    )


def fetch_metric_history(
    session: Session,
    user_id: UUID,
    metric: Metric,
    window: LocalDayWindow,
    provider: Provider | None = None,
    include_overlap_context: bool = False,
    budget: int | None = None,
    seen: Sequence[Observation] = (),
    budget_scope: str = "in the window",
) -> MetricHistory:
    """Read one window within ``budget`` new records.

    ``seen`` holds records already loaded for the same user, metric and provider filter
    (for example by another window). They are reused rather than read again, excluded from
    the SQL by id, and do not count against ``budget``. Each query reads at most one record
    more than the remaining budget, so memory stays bounded and excess is refused, never cut.
    """
    if budget is None:
        budget = MAX_SUMMARY_RECORDS  # resolved at call time so tests can lower the limit
    scope = [Observation.user_id == user_id, Observation.metric == metric]
    if provider is not None:
        scope.append(Observation.provider == provider)
    reused_records = [r for r in seen if _in_window(r, window)]
    in_window = select(Observation).where(
        *scope,
        Observation.started_at >= window.start_utc,
        Observation.started_at < window.end_utc,
    )
    if reused_records:
        in_window = in_window.where(Observation.id.not_in([r.id for r in reused_records]))
    fetched = list(
        session.scalars(
            in_window.order_by(Observation.started_at, Observation.id).limit(budget + 1)
        ).all()
    )
    if len(fetched) > budget:
        raise _limit_exceeded(budget_scope)
    records = sorted([*reused_records, *fetched], key=lambda r: (r.started_at, r.id))
    history = MetricHistory(records=records)
    remaining = budget - len(fetched)
    latest_end = max(
        (r.ended_at for r in records if r.aggregation == "interval_total"), default=None
    )
    if include_overlap_context and latest_end is not None:
        # Every interval_total outside the window that can intersect an in-window interval:
        # it must end after the window starts and start before the last in-window interval
        # ends. No lookback or lookahead bound, so the check is complete.
        # ix_observations_user_metric_end bounds the scan by (user_id, metric, ended_at).
        reused_context = [r for r in seen if _is_context(r, window, latest_end)]
        context = select(Observation).where(
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
        if reused_context:
            context = context.where(Observation.id.not_in([r.id for r in reused_context]))
        fetched_context = list(
            session.scalars(
                context.order_by(Observation.started_at, Observation.id).limit(remaining + 1)
            ).all()
        )
        if len(fetched_context) > remaining:
            raise _limit_exceeded(budget_scope)
        history.context_intervals = [*reused_context, *fetched_context]
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
