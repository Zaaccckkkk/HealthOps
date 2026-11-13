"""Device observation models: one latent trajectory -> canonical rows per provider.

Apple Watch (healthkit) mirrors the iOS HealthKit adapter: heart_rate, resting_heart_rate and
hrv_sdnn are samples; steps (hourly), exercise and asleep segments are interval totals whose
value equals the segment length for sleep. Fitbit (google_health) mirrors the NetHealth export
and the Google Health connector: daily totals and daily summaries, one sleep log per night
spanning time in bed. Both apply their own noise and their own missingness plan, and, since
synthetic-v0.2, the systematic device bias computed by device_bias.py (`bias`: metric -> value
per day, added between the latent value and the noise; None or a missing metric means 0).

Times are handled as int64 nanoseconds since the epoch (UTC).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HOUR = 3_600_000_000_000
MINUTE = HOUR // 60


class Rows:
    """Column buffers for one user and provider."""

    def __init__(self, user: pd.Series, provider: str, device: str, version: str, source: str):
        self.fixed = {
            "user_id": user["user_id"],
            "provider": provider,
            "source_device": device,
            "source_timezone": user["timezone"],
            "provenance_dataset": "synthetic",
            "provenance_version": version,
            "provenance_source": source,
        }
        self.columns: dict[str, list] = {
            k: [] for k in ("id", "metric", "value", "unit", "start", "end", "aggregation")
        }

    def add(self, metric: str, unit: str, aggregation: str, start, end, value, ids=None) -> None:
        start = np.atleast_1d(np.asarray(start, dtype=np.int64))
        end = np.atleast_1d(np.asarray(end, dtype=np.int64))
        if start.size == 0:
            return
        if ids is None:
            ids = np.char.add(f"synthetic:{metric}:", (start // 1_000_000).astype(str))
        for key, part in (
            ("id", np.asarray(ids)),
            ("metric", np.full(start.size, metric)),
            ("value", np.atleast_1d(np.asarray(value, dtype=float))),
            ("unit", np.full(start.size, unit)),
            ("start", start),
            ("end", end),
            ("aggregation", np.full(start.size, aggregation)),
        ):
            self.columns[key].append(part)

    def frame(self) -> pd.DataFrame:
        if not self.columns["id"]:
            return pd.DataFrame()
        c = {key: np.concatenate(parts) for key, parts in self.columns.items()}
        return pd.DataFrame(
            {
                "source_record_id": c["id"],
                "metric": c["metric"],
                "value": c["value"],
                "unit": c["unit"],
                "started_at": pd.to_datetime(c["start"], utc=True),
                "ended_at": pd.to_datetime(c["end"], utc=True),
                "aggregation": c["aggregation"],
                **self.fixed,
            }
        )


def _timeline(user: pd.Series, latent: pd.DataFrame) -> dict:
    n = len(latent)
    days = pd.date_range(user["start_date"], periods=n + 1, freq="D").tz_localize(user["timezone"])
    midnight = days.tz_convert("UTC").asi8
    bed = midnight[:-1] + (latent["bedtime_hour"].to_numpy() * HOUR).astype(np.int64)
    wake = bed + (latent["time_in_bed_minutes"].to_numpy() * MINUTE).astype(np.int64)
    awake_start = np.concatenate([[midnight[0] + 7 * HOUR], wake[:-1]])
    exercise_start = midnight[:-1] + (latent["exercise_start_hour"].to_numpy() * HOUR).astype(
        np.int64
    )
    exercise_end = exercise_start + (latent["exercise_minutes"].to_numpy() * MINUTE).astype(
        np.int64
    )
    return {
        "midnight": midnight,
        "bed": bed,
        "wake": wake,
        "awake_start": awake_start,
        "exercise_start": exercise_start,
        "exercise_end": exercise_end,
        "dates": latent["date"].dt.strftime("%Y-%m-%d").to_numpy(),
    }


def _cut(plan: dict, tl: dict, d: int) -> int:
    """Battery cut-off time for day d, or the end of the awake period."""
    hour = plan["battery_hour"][d]
    return (
        tl["bed"][d] if np.isnan(hour) else min(tl["bed"][d], tl["midnight"][d] + int(hour * HOUR))
    )


def _bias(bias: dict | None, metric: str, d: int) -> float:
    return float(bias[metric][d]) if bias and metric in bias else 0.0


def _asleep_minutes(
    latent: pd.Series, noise_sd: float, rng: np.random.Generator, bias: float = 0.0
) -> float:
    return float(
        np.clip(
            latent["sleep_minutes"] + bias + rng.normal(0, noise_sd),
            60,
            latent["time_in_bed_minutes"],
        )
    )


def apple_watch(user, latent, plan, cfg, calibration, version, rng, bias=None) -> pd.DataFrame:
    m, rows, tl = (
        cfg["metrics"],
        Rows(user, cfg["provider"], cfg["source_device"], version, "apple_watch_model"),
        _timeline(user, latent),
    )
    hr_cfg = m["heart_rate"]
    noise = (
        calibration["apple_watch"]["hr_noise_sd_bpm"]
        if hr_cfg["noise_sd_bpm"] == "calibrated"
        else hr_cfg["noise_sd_bpm"]
    )
    drop = plan["metric_dropout"]
    for d, day in enumerate(latent.itertuples(index=False)):
        if not plan["day_missing"][d]:
            cut = _cut(plan, tl, d)
            times = np.arange(tl["awake_start"][d], cut, hr_cfg["awake_interval_minutes"] * MINUTE)
            values = day.daily_mean_hr + rng.normal(0, noise, times.size)
            if day.exercise_intensity:
                during = (times >= tl["exercise_start"][d]) & (times < tl["exercise_end"][d])
                values[during] += hr_cfg["exercise_increase_bpm"][day.exercise_intensity]
            keep = rng.random(times.size) >= plan["sample_dropout"]
            rows.add(
                "heart_rate",
                "bpm",
                "sample",
                times[keep],
                times[keep],
                np.clip(values[keep], 35, 220),
            )

            if not drop.get("resting_heart_rate", np.zeros(len(latent), bool))[d]:
                at = tl["midnight"][d] + m["resting_heart_rate"]["hour"] * HOUR
                value = (
                    day.resting_hr
                    + _bias(bias, "resting_heart_rate", d)
                    + rng.normal(0, m["resting_heart_rate"]["noise_sd_bpm"])
                )
                rows.add("resting_heart_rate", "bpm", "sample", at, at, value)

            step_cfg = m["steps"]
            width = step_cfg["interval_minutes"] * MINUTE
            starts = np.arange(tl["awake_start"][d] // width * width, cut, width)
            if starts.size:
                share = rng.dirichlet(np.full(starts.size, step_cfg["hourly_concentration"]))
                total = day.steps * np.exp(
                    _bias(bias, "steps", d) + rng.normal(0, step_cfg["noise_log_sd"])
                )
                counts = np.round(total * share)
                positive = counts > 0
                rows.add(
                    "steps",
                    "count",
                    "interval_total",
                    starts[positive],
                    starts[positive] + width,
                    counts[positive],
                )

            if day.exercise_intensity and tl["exercise_end"][d] <= cut:
                seconds = max(
                    60.0,
                    (
                        day.exercise_minutes
                        + rng.normal(0, m["exercise_duration"]["noise_sd_minutes"])
                    )
                    * 60,
                )
                start = tl["exercise_start"][d]
                rows.add(
                    "exercise_duration",
                    "s",
                    "interval_total",
                    start,
                    start + int(seconds * 1e9),
                    seconds,
                )

        if not plan["night_missing"][d]:
            bed, wake = tl["bed"][d], tl["wake"][d]
            times = np.arange(bed, wake, hr_cfg["asleep_interval_minutes"] * MINUTE)
            values = day.resting_hr + hr_cfg["asleep_offset_bpm"] + rng.normal(0, noise, times.size)
            keep = rng.random(times.size) >= plan["sample_dropout"]
            rows.add(
                "heart_rate",
                "bpm",
                "sample",
                times[keep],
                times[keep],
                np.clip(values[keep], 35, 220),
            )

            asleep = _asleep_minutes(
                latent.iloc[d],
                m["sleep_duration"]["noise_sd_minutes"],
                rng,
                _bias(bias, "sleep_duration", d),
            )
            awake = (day.time_in_bed_minutes - asleep) / 2  # sleep latency and one awakening
            half = int(asleep / 2 * MINUTE)
            first = bed + int(awake * MINUTE)
            second = first + half + int(awake * MINUTE)
            rows.add(
                "sleep_duration",
                "s",
                "interval_total",
                [first, second],
                [first + half, second + half],
                [half / 1e9, half / 1e9],
            )

            hrv_cfg = m["hrv_sdnn"]
            if not drop.get("hrv_sdnn", np.zeros(len(latent), bool))[d]:
                k = hrv_cfg["samples_per_night"]
                at = bed + ((wake - bed) * (np.arange(k) + 1) // (k + 1))
                values = (
                    day.hrv_rmssd
                    * hrv_cfg["sdnn_per_rmssd"]
                    * np.exp(rng.normal(0, hrv_cfg["noise_log_sd"], k))
                )
                rows.add("hrv_sdnn", "ms", "sample", at, at, values)
    return rows.frame()


def fitbit(user, latent, plan, cfg, calibration, version, rng, bias=None) -> pd.DataFrame:
    m, rows, tl = (
        cfg["metrics"],
        Rows(user, cfg["provider"], cfg["source_device"], version, "fitbit_model"),
        _timeline(user, latent),
    )
    drop = plan["metric_dropout"]
    never = np.zeros(len(latent), bool)
    for d, day in enumerate(latent.itertuples(index=False)):
        start, end, date = tl["midnight"][d], tl["midnight"][d + 1], tl["dates"][d]
        if not plan["day_missing"][d]:
            awake = tl["bed"][d] - tl["awake_start"][d]
            worn = (
                np.clip((_cut(plan, tl, d) - tl["awake_start"][d]) / awake, 0, 1)
                if awake > 0
                else 1.0
            )
            steps = round(
                day.steps
                * worn
                * np.exp(_bias(bias, "steps", d) + rng.normal(0, m["steps"]["noise_log_sd"]))
            )
            rows.add(
                "steps",
                "count",
                "daily_total",
                start,
                end,
                steps,
                [f"synthetic:daily-steps:{date}"],
            )
            if not drop.get("heart_rate", never)[d]:
                value = day.daily_mean_hr + rng.normal(0, m["heart_rate"]["noise_sd_bpm"])
                rows.add(
                    "heart_rate",
                    "bpm",
                    "daily_summary",
                    start,
                    end,
                    value,
                    [f"synthetic:daily-mean-heart-rate:{date}"],
                )
            if not drop.get("resting_heart_rate", never)[d]:
                value = (
                    day.resting_hr
                    + _bias(bias, "resting_heart_rate", d)
                    + rng.normal(0, m["resting_heart_rate"]["noise_sd_bpm"])
                )
                rows.add(
                    "resting_heart_rate",
                    "bpm",
                    "daily_summary",
                    start,
                    end,
                    value,
                    [f"synthetic:daily-resting-heart-rate:{date}"],
                )
            # Fitbit computes daily HRV from the previous night's sleep.
            slept = d > 0 and not plan["night_missing"][d - 1]
            if slept and not drop.get("hrv_rmssd", never)[d]:
                value = day.hrv_rmssd * np.exp(rng.normal(0, m["hrv_rmssd"]["noise_log_sd"]))
                rows.add(
                    "hrv_rmssd",
                    "ms",
                    "daily_summary",
                    start,
                    end,
                    value,
                    [f"synthetic:daily-hrv:{date}"],
                )
            minutes = max(
                0.0,
                day.exercise_minutes + rng.normal(0, m["exercise_duration"]["noise_sd_minutes"]),
            )
            rows.add(
                "exercise_duration",
                "s",
                "daily_total",
                start,
                end,
                round(minutes) * 60,
                [f"synthetic:daily-active-minutes:{date}"],
            )
        if not plan["night_missing"][d]:
            asleep = _asleep_minutes(
                latent.iloc[d],
                m["sleep_duration"]["noise_sd_minutes"],
                rng,
                _bias(bias, "sleep_duration", d),
            )
            rows.add(
                "sleep_duration",
                "s",
                "interval_total",
                tl["bed"][d],
                tl["wake"][d],
                round(asleep) * 60,
            )
    return rows.frame()


MODELS = {"healthkit": apple_watch, "google_health": fitbit}
