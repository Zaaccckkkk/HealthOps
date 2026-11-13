"""Estimate generator parameters from the canonical real data.

Fitbit statistics come from NetHealth (google_health rows), Apple Watch statistics from
BIDSleep (healthkit rows). Every number in the returned dict is an estimate from data; the
priors live in configs/ and are never mixed in here.

Terms used below:
  between_*  spread of user-level means across users (who is different from whom)
  within_*   spread of a user's days around their own mean (day-to-day variability)
  ar1        lag-1 autocorrelation of within-user deviations on consecutive days
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _local_date(frame: pd.DataFrame) -> pd.Series:
    tz = frame["source_timezone"].iloc[0]
    return frame["started_at"].dt.tz_convert(tz).dt.normalize().dt.tz_localize(None)


def _between_within(frame: pd.DataFrame, column: str, min_count: int) -> tuple[dict, pd.DataFrame]:
    """User-level spread of `column` for users with at least min_count rows."""
    frame = frame[frame.groupby("user_id")[column].transform("size") >= min_count].copy()
    frame["resid"] = frame[column] - frame.groupby("user_id")[column].transform("mean")
    means = frame.groupby("user_id")[column].mean()
    stats = {
        "users": int(means.size),
        "rows": int(len(frame)),
        "between_mean": float(means.mean()),
        "between_sd": float(means.std()),
        "within_sd": float(frame["resid"].std()),
    }
    return stats, frame


def _user_stats(daily: pd.DataFrame, column: str, min_days: int) -> tuple[dict, pd.DataFrame]:
    """_between_within plus ar1 over consecutive calendar days (one row per user-day)."""
    stats, daily = _between_within(daily.sort_values(["user_id", "date"]), column, min_days)
    previous = daily.groupby("user_id")["resid"].shift()
    consecutive = daily.groupby("user_id")["date"].diff() == pd.Timedelta(days=1)
    x, y = daily.loc[consecutive, "resid"], previous[consecutive]
    stats["ar1"] = float(np.corrcoef(x, y)[0, 1]) if len(x) > 2 else 0.0
    return stats, daily


def _beta_moments(p: pd.Series) -> dict:
    mean, var = float(p.mean()), float(p.var())
    common = mean * (1 - mean) / var - 1 if var > 0 else 50.0
    return {"mean": mean, "a": max(mean * common, 0.05), "b": max((1 - mean) * common, 0.05)}


def fitbit(frame: pd.DataFrame, cfg: dict) -> dict:
    c = frame.assign(date=_local_date(frame))
    min_days = cfg["min_days_per_user"]

    steps = c[(c.metric == "steps") & (c.value >= cfg["min_daily_steps"])]
    steps = steps.groupby(["user_id", "date"], as_index=False)["value"].sum()
    steps["log_steps"] = np.log(steps["value"])
    step_stats, resid = _user_stats(steps, "log_steps", min_days)
    dow = resid.groupby(resid["date"].dt.dayofweek)["resid"].mean()
    dow_mult = np.exp(dow - dow.mean())

    sleep_rows = c[c.metric == "sleep_duration"].copy()
    sleep = sleep_rows.groupby(["user_id", "date"], as_index=False)["value"].sum()
    sleep["minutes"] = sleep["value"] / 60
    low, high = cfg["sleep_minutes_range"]
    sleep_stats, _ = _user_stats(sleep[sleep.minutes.between(low, high)], "minutes", min_days)

    in_bed = (sleep_rows.ended_at - sleep_rows.started_at).dt.total_seconds()
    efficiency = (sleep_rows.value / in_bed)[in_bed > 0].clip(upper=1)
    main = (
        sleep_rows.assign(in_bed=in_bed).sort_values("in_bed").groupby(["user_id", "date"]).tail(1)
    )
    main = main[main.in_bed >= 3 * 3600]
    start = main.started_at.dt.tz_convert(main.source_timezone.iloc[0])
    hour = start.dt.hour + start.dt.minute / 60
    main = main.assign(bedtime=np.where(hour < 12, hour + 24, hour))
    bed_stats, _ = _user_stats(main[["user_id", "date", "bedtime"]], "bedtime", min_days)

    hr = c[c.metric == "heart_rate"][["user_id", "date", "value"]].rename(columns={"value": "hr"})
    hr_stats, hr_resid = _user_stats(hr, "hr", min_days)
    joined = hr_resid.merge(
        resid[["user_id", "date", "resid"]], on=["user_id", "date"], suffixes=("_hr", "_steps")
    )
    slope = float((joined.resid_hr * joined.resid_steps).sum() / (joined.resid_steps**2).sum())

    exercise = c[c.metric == "exercise_duration"][["user_id", "date", "value"]]
    exercise = exercise.merge(steps[["user_id", "date"]], on=["user_id", "date"])  # worn days
    exercise["minutes"] = exercise["value"] / 60
    exercise = exercise[exercise.groupby("user_id")["date"].transform("size") >= min_days]
    on = exercise["minutes"] >= cfg["exercise_day_threshold_minutes"]
    per_user = exercise.assign(on=on).groupby("user_id")
    frequency = per_user["on"].mean()
    on_minutes = exercise[on].groupby("user_id")["minutes"].mean()
    off_minutes = exercise[~on].groupby("user_id")["minutes"].mean().clip(lower=1)

    return {
        "dataset": cfg["dataset"],
        "steps_log": step_stats,
        "day_of_week_multipliers": [float(dow_mult.get(d, 1.0)) for d in range(7)],
        "sleep_minutes": sleep_stats,
        "sleep_efficiency": float(efficiency.mean()),
        "bedtime_hour": bed_stats,
        "daily_mean_hr": {**hr_stats, "per_log_steps": slope},
        "exercise": {
            "threshold_minutes": cfg["exercise_day_threshold_minutes"],
            "frequency_beta": _beta_moments(frequency),
            "on_minutes_log_mean": float(np.log(on_minutes).mean()),
            "on_minutes_log_sd": float(np.log(on_minutes).std()),
            "off_minutes_log_mean": float(np.log(off_minutes).mean()),
            "off_minutes_log_sd": float(np.log(off_minutes).std()),
        },
        "missingness": fitbit_missingness(c, cfg),
    }


def fitbit_missingness(c: pd.DataFrame, cfg: dict) -> dict:
    """Gaps between a user's first and last day with a steps record."""
    worn = c[c.metric == "steps"].groupby("user_id")["date"].unique()
    slept = set(map(tuple, c[c.metric == "sleep_duration"][["user_id", "date"]].to_numpy()))
    single = multi = days = observed = without_sleep = 0
    lengths = []
    for user, dates in worn.items():
        dates = np.sort(pd.to_datetime(dates))
        if len(dates) < cfg["min_days_per_user"]:
            continue
        gaps = (np.diff(dates) / np.timedelta64(1, "D")).astype(int) - 1
        long = gaps[gaps > cfg["max_gap_days"]]
        days += int((dates[-1] - dates[0]) / np.timedelta64(1, "D")) + 1 - int(long.sum())
        observed += len(dates)
        single += int((gaps == 1).sum())
        mid = gaps[(gaps >= 2) & (gaps <= cfg["max_gap_days"])]
        multi += int(mid.size)
        lengths.extend(mid.tolist())
        without_sleep += sum((user, d) not in slept for d in dates)
    return {
        "user_days": days,
        "not_worn_day_probability": single / days,
        "multi_day_gap_start_probability": multi / days,
        "multi_day_gap_mean_length_days": float(np.mean(lengths)) if lengths else 2.0,
        "overnight_not_worn_probability": without_sleep / observed,
    }


def apple_watch(frame: pd.DataFrame, cfg: dict) -> dict:
    hr = frame[frame.metric == "heart_rate"].sort_values(["provenance_source", "started_at"])
    night = hr.groupby("provenance_source")
    smooth = night["value"].transform(lambda s: s.rolling(5, center=True, min_periods=1).median())
    spacing = night["started_at"].diff().dt.total_seconds()
    gap_seconds = (
        spacing.where(spacing > cfg["hr_gap_seconds"], 0).groupby(hr.provenance_source).sum()
    )
    duration = night["started_at"].agg(lambda s: (s.max() - s.min()).total_seconds())

    nightly = night["value"].quantile(cfg["rhr_percentile"] / 100).rename("rhr").reset_index()
    nightly["user_id"] = nightly["provenance_source"].map(night["user_id"].first())
    rhr_stats, _ = _between_within(nightly, "rhr", cfg["min_nights_per_user"])
    sleep = frame[frame.metric == "sleep_duration"].assign(minutes=lambda f: f.value / 60)
    sleep_stats, _ = _between_within(sleep, "minutes", cfg["min_nights_per_user"])
    return {
        "dataset": cfg["dataset"],
        "resting_hr_proxy": {"percentile": cfg["rhr_percentile"], **rhr_stats},
        "hr_noise_sd_bpm": float((hr["value"] - smooth).std()),
        "hr_median_spacing_seconds": float(spacing.median()),
        "hr_sample_dropout": float((gap_seconds / duration.replace(0, np.nan)).mean()),
        "sleep_minutes": sleep_stats,
    }
