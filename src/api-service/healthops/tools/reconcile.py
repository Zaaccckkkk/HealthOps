"""User-scoped reconciliation: daily aggregates -> pinned CPU model -> qualified estimate."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from healthops.models import Observation
from healthops.reconciliation.model import METRICS, PROVIDERS
from healthops.schemas import UNITS, Metric
from healthops.tools.contracts import (
    DailySummaryParams,
    ReconcileParams,
    ReconcileReading,
    ReconcileResult,
    ToolWindow,
)
from healthops.tools.daily_summary import DEFINITIONS as DAILY_DEFINITIONS
from healthops.tools.daily_summary import daily_summary_tool
from healthops.tools.local_days import LocalDayWindow, ToolInputError

RED_FLAG = "Data may not be sufficient; the result may not be reliable."
WARNING_TEXT = {
    "history_short": RED_FLAG + " The available history is shorter than the model window.",
    "sparse_window": RED_FLAG + " Fewer than half of the window days have usable target readings.",
    "single_device": RED_FLAG
    + " Only one channel has a usable reading on this date; no device attribution "
    "is possible. This is not a comparison of device accuracy.",
    "wide_interval": RED_FLAG + " The model interval is wide; the estimate is uncertain.",
    "context_defaulted": "Stress, workout, travel, illness and fatigue were not collected; "
    "generator defaults were used, not inferred facts about you.",
    "synthetic_model": "This model was trained on synthetic data and is not clinically validated. "
    "Its nominal 90% interval is not a validated guarantee for real users.",
    "sleep_alignment_differs": "Training groups sleep by latent bedtime; this API groups by "
    "local start date. Sleep features can differ across midnight, affecting all model outputs.",
    "no_reading_on_date": "No usable target reading on this date; no estimate or attribution "
    "is produced, even if earlier history exists.",
    "multiple_devices_in_channel": "Several physical devices occur within one data channel; "
    "this model compares channels, not individual devices.",
}
DEFINITIONS = {
    **DAILY_DEFINITIONS,
    **{f"warning:{k}": v for k, v in WARNING_TEXT.items()},
    "estimate": "q50 is a synthetic-trained estimate for the requested day, not a measurement.",
    "interval": "q05 to q95: a nominal 90% model prediction interval in the metric's API unit.",
    "attribution": "Channel with larger absolute deviation from q50, not proven less accurate. "
    "Null for a tie or when fewer than two usable readings exist.",
    "history_days": "Calendar span from the first accessible supported observation through the "
    "query date, inclusive, capped at window_days. Does not imply daily coverage.",
    "coverage": "Window counts use usable readings of the requested metric, not other metrics.",
    "deviation": "Recorded channel value minus q50, in the same unit.",
}


def decimal_value(value):
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def reconcile_from_summaries(params, predictor, summaries, first_day, extra_warnings=()):
    """Pure adapter, independently testable with summaries and a fake quantile predictor."""
    length = predictor.window_days
    window = LocalDayWindow.from_dates(
        params.timezone, params.date - timedelta(days=length - 1), params.date, length
    )
    days = list(window.dates())
    readings = {}
    warnings = set(extra_warnings) | {"context_defaulted", "synthetic_model"}
    for summary in summaries:
        warnings.update(w for w in summary.warnings if w != "no_records_in_window")
        for provider in summary.providers:
            for day in provider.days:
                if summary.metric == Metric.RESTING_HEART_RATE:
                    value = day.sample_mean if day.sample_mean is not None else day.daily_summary
                else:
                    # Ambiguous totals are not silently chosen for model input.
                    value = (
                        None
                        if "mixed_total_kinds" in day.warnings
                        else (
                            day.daily_total
                            if day.daily_total is not None
                            else day.interval_total_sum
                        )
                    )
                if day.date in days:
                    readings[(day.date, summary.metric.value, provider.provider)] = value
    if any(v is not None and m == "sleep_duration" for (_, m, _), v in readings.items()):
        warnings.add("sleep_alignment_differs")
    pairs = [[readings.get((d, params.metric, p)) for p in PROVIDERS] for d in days]
    any_days = sum(any(v is not None for v in pair) for pair in pairs)
    both_days = sum(all(v is not None for v in pair) for pair in pairs)
    history_days = min(length, max(0, (params.date - first_day).days + 1)) if first_day else 0
    if history_days < length:
        warnings.add("history_short")
    if any_days * 2 < length:
        warnings.add("sparse_window")
    current = pairs[-1]
    present = sum(v is not None for v in current)
    q05 = q50 = q95 = attribution = None
    if not present:
        warnings.add("no_reading_on_date")
        # Preserve the standard missing-record code, qualified by the requested date.
        warnings.add("no_records_in_window")
    else:
        if present == 1:
            warnings.add("single_device")
        try:
            q05, q50, q95 = map(
                decimal_value, predictor.predict(days, readings, first_day, params.metric)
            )
            if not all(v.is_finite() for v in (q05, q50, q95)) or not 0 <= q05 <= q50 <= q95:
                raise ValueError("Invalid prediction")
        except (ValueError, ArithmeticError, RuntimeError) as exc:
            raise ToolInputError(
                "Reconciliation model could not produce a reliable estimate."
            ) from exc
        threshold = {"sleep_duration": 90 * 60, "steps": 5000, "resting_heart_rate": 8}
        if q95 - q05 > threshold[params.metric]:
            warnings.add("wide_interval")
        if present == 2:
            distances = [abs(v - q50) for v in current]
            if distances[0] != distances[1]:
                attribution = PROVIDERS[distances.index(max(distances))]
    return ReconcileResult(
        metric=params.metric,
        unit=UNITS[Metric(params.metric)],
        date=params.date,
        window=ToolWindow(
            timezone=params.timezone,
            start_date=window.start_date,
            end_date=window.end_date,
            start_utc=window.start_utc,
            end_utc=window.end_utc,
        ),
        q05=q05,
        q50=q50,
        q95=q95,
        providers=[
            ReconcileReading(
                provider=p,
                value=v,
                deviation=v - q50 if v is not None and q50 is not None else None,
            )
            for p, v in zip(PROVIDERS, current)
        ],
        attribution=attribution,
        window_days=length,
        history_days=history_days,
        window_days_with_any=any_days,
        window_days_with_both=both_days,
        model_version=predictor.version,
        data_version=predictor.data_version,
        artifact_sha256=predictor.artifact_sha256,
        warnings=sorted(warnings),
        definitions=DEFINITIONS
        | {
            "warning:no_records_in_window": "No usable target reading on the requested date. "
            "This says nothing about activity, device wear or authorization."
        },
    )


def reconcile_tool(session: Session, user_id: UUID, params: ReconcileParams, predictor):
    if predictor is None:
        raise ToolInputError("Reconciliation model is not configured on this deployment.")
    # Validate before subtraction (calendar boundaries and timezone).
    LocalDayWindow.from_dates(params.timezone, params.date, params.date, 1)
    start = params.date - timedelta(days=predictor.window_days - 1)
    window = LocalDayWindow.from_dates(params.timezone, start, params.date, predictor.window_days)
    summaries = [
        daily_summary_tool(
            session,
            user_id,
            DailySummaryParams(
                metric=m, timezone=params.timezone, start_date=start, end_date=params.date
            ),
        )
        for m in METRICS
    ]
    first = session.scalar(
        select(func.min(Observation.started_at)).where(
            Observation.user_id == user_id,
            Observation.metric.in_(METRICS),
            Observation.started_at < window.end_utc,
        )
    )
    # Do not expose physical device IDs, only detect ambiguous channel grouping.
    devices = session.execute(
        select(Observation.provider, func.count(func.distinct(Observation.source_device)))
        .where(
            Observation.user_id == user_id,
            Observation.metric.in_(METRICS),
            Observation.started_at >= window.start_utc,
            Observation.started_at < window.end_utc,
        )
        .group_by(Observation.provider)
    ).all()
    extra = ["multiple_devices_in_channel"] if any(n > 1 for _, n in devices) else []
    return reconcile_from_summaries(
        params, predictor, summaries, window.local_date(first) if first else None, extra
    )
