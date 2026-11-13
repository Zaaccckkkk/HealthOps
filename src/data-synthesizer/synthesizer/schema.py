"""HealthOps canonical observation schema for parquet datasets.

The columns are the ingestion API's ``ObservationIn`` fields (src/api-service/healthops/
schemas.py) plus ``user_id`` and three provenance columns, so every row can be posted to the
API unchanged. ``started_at``/``ended_at`` are the plan's "timestamp": equal for samples, an
interval for totals. The data-processor and data-synthesizer keep identical copies.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

UNITS = {
    "heart_rate": "bpm",
    "resting_heart_rate": "bpm",
    "steps": "count",
    "hrv_sdnn": "ms",
    "hrv_rmssd": "ms",
    "sleep_duration": "s",
    "active_energy": "kcal",
    "exercise_duration": "s",
}
PROVIDERS = {"healthkit", "google_health", "manual"}
AGGREGATIONS = {"sample", "interval_total", "daily_total", "daily_summary"}

ARROW_SCHEMA = pa.schema(
    [
        ("user_id", pa.string()),
        ("provider", pa.string()),
        ("source_record_id", pa.string()),
        ("source_device", pa.string()),
        ("metric", pa.string()),
        ("value", pa.float64()),
        ("unit", pa.string()),
        ("started_at", pa.timestamp("us", tz="UTC")),
        ("ended_at", pa.timestamp("us", tz="UTC")),
        ("source_timezone", pa.string()),
        ("aggregation", pa.string()),
        ("provenance_dataset", pa.string()),  # nethealth | bidsleep | synthetic
        ("provenance_version", pa.string()),  # data version the row was derived from
        ("provenance_source", pa.string()),  # source file or generator component
    ]
)
COLUMNS = ARROW_SCHEMA.names


def validate(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Drop rows the ingestion API would reject; return the kept rows and drop counts."""
    frame = frame[COLUMNS].copy()
    frame["value"] = frame["value"].astype(float).round(6)
    for column in ("started_at", "ended_at"):  # the schema stores microseconds
        frame[column] = pd.to_datetime(frame[column], utc=True).dt.floor("us")
    checks = {
        "unknown_provider": ~frame["provider"].isin(PROVIDERS),
        "unknown_aggregation": ~frame["aggregation"].isin(AGGREGATIONS),
        "unit_mismatch": frame["unit"] != frame["metric"].map(UNITS),
        "invalid_value": ~np.isfinite(frame["value"]) | (frame["value"] < 0),
        "end_before_start": frame["ended_at"] < frame["started_at"],
        "non_integer_steps": (frame["metric"] == "steps")
        & (frame["value"] != frame["value"].round()),
        "zero_heart_rate": frame["metric"].isin(["heart_rate", "resting_heart_rate"])
        & (frame["value"] == 0),
    }
    dropped, bad = {}, pd.Series(False, index=frame.index)
    for reason, mask in checks.items():
        mask = mask & ~bad
        if mask.any():
            dropped[reason] = int(mask.sum())
        bad |= mask
    frame = frame[~bad]
    duplicate = frame.duplicated(["user_id", "provider", "source_record_id", "metric"])
    if duplicate.any():
        dropped["duplicate_identity"] = int(duplicate.sum())
    return frame[~duplicate].reset_index(drop=True), dropped


def to_table(frame: pd.DataFrame) -> pa.Table:
    return pa.Table.from_pandas(frame[COLUMNS], schema=ARROW_SCHEMA, preserve_index=False)


def write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(to_table(frame), path)


def iso_utc(series: pd.Series) -> pd.Series:
    return series.dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
