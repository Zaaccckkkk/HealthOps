"""From a synthetic version to one row per user-day: device readings, truth, context, history.

Input (DATA_ROOT/synthetic/<version>/):
    latent_daily.parquet            truth per user-day (sleep_minutes, steps, resting_hr, context)
    observations_healthkit.parquet  Apple Watch rows (canonical schema)
    observations_google_health.parquet  Fitbit rows
    users.parquet                   cohort, timezone
    device_bias_daily.parquet       the bias the synthesizer applied (evaluation only)

Output: a daily table with, per metric m in METRICS and provider p in PROVIDERS,
    obs_<p>_<m>      the device's reading for that day (NaN when the device recorded nothing)
    truth_<m>        the latent value
    bias_<p>_<m>     the applied bias total (NaN when the user has no such device)
plus context flags, the day of week, the cohort and the split. History features (rolling means
of past readings and of the past gap between devices) are added by `add_history`.

For the synthetic observation contract, steps are summed by source-local day and resting
heart rate is averaged by source-local day. Sleep segments are summed per night, each night
attached to the latent day whose bedtime it belongs to. This is not a general online adapter:
the API additionally distinguishes aggregation kinds and uses the requested time zone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

METRICS = ("sleep", "steps", "rhr")
PROVIDERS = ("healthkit", "google_health")
SHORT = {"healthkit": "hk", "google_health": "gh"}
# metric -> (canonical metric name, latent column, device_bias_daily metric name, scale)
SOURCE = {
    "sleep": ("sleep_duration", "sleep_minutes", "sleep_duration", 1 / 60),
    "steps": ("steps", "steps", "steps", 1.0),
    "rhr": ("resting_heart_rate", "resting_hr", "resting_heart_rate", 1.0),
}
STRESS = ("low", "medium", "high")
WORKOUT = ("none", "rest", "moderate", "hard")
HISTORY_WINDOWS = (7, 14, 30)


def version_dir(data_root: Path, version: str) -> Path:
    return Path(data_root) / "synthetic" / version


def manifest_sha256(data_root: Path, version: str) -> str | None:
    path = Path(data_root) / "manifests" / f"{version}.yaml"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def split_of(user_id: str, train: float = 0.70, val: float = 0.15) -> str:
    """Deterministic split by user id hash, so users never leak across splits."""
    bucket = int(hashlib.sha256(user_id.encode()).hexdigest(), 16) % 10_000 / 10_000
    return "train" if bucket < train else "val" if bucket < train + val else "test"


def split_hash(daily: pd.DataFrame) -> str:
    parts = {
        s: sorted(daily.loc[daily.split == s, "user_id"].unique()) for s in ("train", "val", "test")
    }
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _local_dates(frame: pd.DataFrame) -> pd.Series:
    """Local calendar date of started_at for rows that may carry several time zones."""
    out = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    for tz, part in frame.groupby("source_timezone"):
        out.loc[part.index] = (
            part["started_at"].dt.tz_convert(tz).dt.normalize().dt.tz_localize(None)
        )
    return out


def _bed_times(latent: pd.DataFrame, users: pd.DataFrame) -> pd.DataFrame:
    """UTC bedtime of every latent night, from the local midnight and the bedtime hour."""
    tz = users.set_index("user_id")["timezone"]
    parts = []
    for zone, part in latent.assign(tz=latent["user_id"].map(tz)).groupby("tz"):
        midnight = part["date"].dt.tz_localize(zone).dt.tz_convert("UTC")
        parts.append(
            pd.DataFrame(
                {
                    "user_id": part["user_id"].to_numpy(),
                    "date": part["date"].to_numpy(),
                    "bed_at": (
                        midnight + pd.to_timedelta(part["bedtime_hour"], unit="h")
                    ).to_numpy(),
                }
            )
        )
    beds = pd.concat(parts, ignore_index=True)
    beds["bed_at"] = pd.to_datetime(beds["bed_at"], utc=True).astype("datetime64[ns, UTC]")
    # The synthesizer starts a night at this instant in integer nanoseconds and the parquet
    # schema stores it in microseconds, so a reading can sit a fraction of a millisecond before
    # the nanosecond value recomputed here. A night begins one minute early so that a reading at
    # the bedtime itself still matches it; the previous night's wake is hours earlier.
    beds["bed_at"] = beds["bed_at"].dt.floor("min") - pd.Timedelta(minutes=1)
    return beds.sort_values("bed_at")


def _daily_readings(obs: pd.DataFrame, beds: pd.DataFrame) -> pd.DataFrame:
    """user_id, date, metric (short name), value: one reading per user-day-metric."""
    obs = obs[obs["metric"].isin(m[0] for m in SOURCE.values())].copy()
    short = {canonical: name for name, (canonical, *_) in SOURCE.items()}
    obs["metric"] = obs["metric"].map(short)
    obs["started_at"] = pd.to_datetime(obs["started_at"], utc=True).astype("datetime64[ns, UTC]")
    day = obs[obs["metric"] != "sleep"].copy()
    day["date"] = _local_dates(day)
    keys = ["user_id", "date", "metric"]
    steps = day[day["metric"] == "steps"].groupby(keys, as_index=False)["value"].sum()
    # Resting heart rate is a point measurement, not an additive quantity like steps.
    rhr = day[day["metric"] == "rhr"].groupby(keys, as_index=False)["value"].mean()
    daily = pd.concat([steps, rhr], ignore_index=True)

    sleep = obs[obs["metric"] == "sleep"].sort_values("started_at")
    if len(sleep):
        matched = pd.merge_asof(
            sleep,
            beds.rename(columns={"bed_at": "started_at"}),
            on="started_at",
            by="user_id",
            direction="backward",
            tolerance=pd.Timedelta(hours=12),
        )
        matched = matched.dropna(subset=["date"])
        nights = matched.groupby(["user_id", "date", "metric"], as_index=False)["value"].sum()
        nights["value"] *= SOURCE["sleep"][3]
        daily = pd.concat([daily, nights], ignore_index=True)
    return daily


OBSERVATION_COLUMNS = ["user_id", "metric", "value", "started_at", "source_timezone"]


def _read_observations(path: Path, users: set[str]) -> pd.DataFrame:
    """Only the users, metrics and columns the daily table needs. The Apple Watch file is
    mostly heart-rate samples (about 85 % of its rows), which are never read into memory."""
    metrics = [canonical for canonical, *_ in SOURCE.values()]
    table = pq.read_table(
        path,
        columns=OBSERVATION_COLUMNS,
        filters=(pc.field("user_id").isin(list(users))) & (pc.field("metric").isin(metrics)),
    )
    return table.to_pandas()


def build_daily(
    data_root: Path, version: str, users_limit: int | None = None, seed: int = 0
) -> pd.DataFrame:
    """One row per user-day with readings, truth, bias and context. `users_limit` keeps a random
    subset of users (all cohorts) for smoke tests."""
    folder = version_dir(data_root, version)
    users = pd.read_parquet(folder / "users.parquet")
    if users_limit and users_limit < len(users):
        users = users.sample(users_limit, random_state=seed).sort_values("user_id")
    keep = set(users["user_id"])
    latent = pq.read_table(
        folder / "latent_daily.parquet", filters=pc.field("user_id").isin(list(keep))
    ).to_pandas()
    latent["date"] = pd.to_datetime(latent["date"])
    beds = _bed_times(latent, users)

    daily = latent[["user_id", "date", "day_index"]].copy()
    for name, (_, column, _, _) in SOURCE.items():
        daily[f"truth_{name}"] = latent[column].to_numpy()
    for key in ("stress", "workout", "travel", "illness", "subjective_fatigue"):
        daily[f"context_{key}"] = latent[f"context_{key}"].to_numpy()
    daily["day_of_week"] = daily["date"].dt.dayofweek
    daily = daily.merge(users[["user_id", "cohort", "timezone"]], on="user_id")

    for provider in PROVIDERS:
        readings = _daily_readings(
            _read_observations(folder / f"observations_{provider}.parquet", keep), beds
        )
        for name in METRICS:
            part = readings[readings["metric"] == name][["user_id", "date", "value"]]
            part = part.rename(columns={"value": f"obs_{SHORT[provider]}_{name}"})
            daily = daily.merge(part, on=["user_id", "date"], how="left")

    bias = pq.read_table(
        folder / "device_bias_daily.parquet", filters=pc.field("user_id").isin(list(keep))
    ).to_pandas()
    bias["date"] = pd.to_datetime(bias["date"])
    long_to_short = {canonical: name for name, (_, _, canonical, _) in SOURCE.items()}
    bias["metric"] = bias["metric"].map(long_to_short)
    for provider in PROVIDERS:
        for name in METRICS:
            part = bias[(bias["provider"] == provider) & (bias["metric"] == name)]
            part = part[["user_id", "date", "bias_total", "bias_user"]].rename(
                columns={
                    "bias_total": f"bias_{SHORT[provider]}_{name}",
                    "bias_user": f"biasuser_{SHORT[provider]}_{name}",
                }
            )
            daily = daily.merge(part, on=["user_id", "date"], how="left")

    daily["split"] = daily["user_id"].map(split_of)
    return daily.sort_values(["user_id", "date"]).reset_index(drop=True)


def add_history(daily: pd.DataFrame) -> pd.DataFrame:
    """Rolling means of past readings (today excluded) per provider and metric, the count of past
    days with a reading, and the past gap between the two devices (paired users only)."""
    daily = daily.copy()
    grouped = daily.groupby("user_id", sort=False)
    for name in METRICS:
        for provider in PROVIDERS:
            col = f"obs_{SHORT[provider]}_{name}"
            series = np.log1p(daily[col]) if name == "steps" else daily[col]
            shifted = series.groupby(daily["user_id"]).shift(1)
            for window in HISTORY_WINDOWS:
                roll = shifted.groupby(daily["user_id"]).rolling(window, min_periods=1)
                daily[f"hist{window}_{SHORT[provider]}_{name}"] = roll.mean().reset_index(
                    level=0, drop=True
                )
            daily[f"histn_{SHORT[provider]}_{name}"] = (
                shifted.notna().groupby(daily["user_id"]).cumsum().astype(float)
            )
        hk, gh = daily[f"obs_hk_{name}"], daily[f"obs_gh_{name}"]
        gap = (np.log1p(hk) - np.log1p(gh)) if name == "steps" else (hk - gh)
        shifted_gap = gap.groupby(daily["user_id"]).shift(1)
        for window in (14, 30):
            roll = shifted_gap.groupby(daily["user_id"]).rolling(window, min_periods=1)
            daily[f"gap{window}_{name}"] = roll.mean().reset_index(level=0, drop=True)
    _ = grouped
    return daily


def feature_columns(daily: pd.DataFrame) -> list[str]:
    """Columns a tabular model may use: readings, masks, history, context, day of week.
    Nothing derived from truth or bias."""
    cols = []
    for name in METRICS:
        for provider in PROVIDERS:
            cols.append(f"obs_{SHORT[provider]}_{name}")
            cols += [f"hist{w}_{SHORT[provider]}_{name}" for w in HISTORY_WINDOWS]
            cols.append(f"histn_{SHORT[provider]}_{name}")
        cols += [f"gap{w}_{name}" for w in (14, 30)]
    cols += ["ctx_stress", "ctx_workout", "ctx_travel", "ctx_illness", "ctx_fatigue", "day_of_week"]
    return [c for c in cols if c in daily.columns]


def encode_context(daily: pd.DataFrame) -> pd.DataFrame:
    """Ordinal context codes for the tabular model; one-hot encoding is done by the NN loader."""
    daily = daily.copy()
    daily["ctx_stress"] = (
        daily["context_stress"].map({s: i for i, s in enumerate(STRESS)}).astype(float)
    )
    daily["ctx_workout"] = (
        daily["context_workout"].map({w: i for i, w in enumerate(WORKOUT)}).astype(float)
    )
    daily["ctx_travel"] = daily["context_travel"].astype(bool).astype(float)
    daily["ctx_illness"] = daily["context_illness"].astype(bool).astype(float)
    daily["ctx_fatigue"] = daily["context_subjective_fatigue"].astype(float)
    return daily


def has_reading(daily: pd.DataFrame, name: str) -> pd.Series:
    """True when at least one device recorded the metric that day (the estimate rule)."""
    return daily[f"obs_hk_{name}"].notna() | daily[f"obs_gh_{name}"].notna()


def prepare(
    data_root: Path, version: str, out: Path, users_limit: int | None = None, seed: int = 0
) -> dict:
    """Build and save the daily table; return a description for the run record."""
    daily = encode_context(add_history(build_daily(data_root, version, users_limit, seed)))
    out.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(out / "daily.parquet", index=False)
    info = {
        "version": version,
        "manifest_sha256": manifest_sha256(data_root, version),
        "users": int(daily["user_id"].nunique()),
        "user_days": int(len(daily)),
        "users_by_split": daily.groupby("split")["user_id"].nunique().to_dict(),
        "users_by_cohort": daily.drop_duplicates("user_id")["cohort"].value_counts().to_dict(),
        "split_sha256": split_hash(daily),
        "features": feature_columns(daily),
    }
    (out / "prepared.json").write_text(json.dumps(info, indent=2))
    return info
