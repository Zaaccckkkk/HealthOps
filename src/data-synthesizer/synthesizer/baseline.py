"""Cohort assignment and stable per-user baselines.

Population distributions come from calibration (real data) except HRV, which is a prior.
Synthea clinical flags then shift each user's baseline (configs/baseline.yaml).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

COHORT_PROVIDERS = {
    "apple_watch_only": ["healthkit"],
    "fitbit_only": ["google_health"],
    "paired": ["healthkit", "google_health"],
}


def assign_cohorts(rng: np.random.Generator, n: int, composition: dict) -> np.ndarray:
    names = list(composition)
    counts = np.floor(np.array([composition[k] for k in names]) * n).astype(int)
    counts[0] += n - counts.sum()  # rounding remainder
    cohorts = np.repeat(names, counts)
    rng.shuffle(cohorts)
    return cohorts


def build_users(
    rng: np.random.Generator, profiles: pd.DataFrame, calibration: dict, cfg: dict
) -> pd.DataFrame:
    cohort_cfg, fit, aw = cfg["cohort"], calibration["fitbit"], calibration["apple_watch"]
    n = cohort_cfg["n_users"]
    reused = len(profiles) < n
    picked = profiles.iloc[rng.choice(len(profiles), size=n, replace=reused)].reset_index(drop=True)

    end = pd.Timestamp(cohort_cfg["end_date"]) - pd.to_timedelta(
        rng.integers(0, cohort_cfg["end_date_spread_days"] + 1, n), unit="D"
    )
    n_days = rng.integers(cohort_cfg["days_min"], cohort_cfg["days_max"] + 1, n)

    effects = cfg["clinical_effects"]
    decades = np.clip((picked["age"].to_numpy() - effects["reference_age"]) / 10, 0, None)
    steps_mult = effects["steps_multiplier_per_decade"] ** decades
    rhr_delta = np.zeros(n)
    hrv_mult = np.ones(n)
    for flag, rule in effects.items():
        if isinstance(rule, dict) and flag in picked:
            on = picked[flag].to_numpy()
            steps_mult = np.where(on, steps_mult * rule.get("steps_multiplier", 1.0), steps_mult)
            rhr_delta = np.where(on, rhr_delta + rule.get("resting_hr_delta", 0.0), rhr_delta)
            hrv_mult = np.where(on, hrv_mult * rule.get("hrv_multiplier", 1.0), hrv_mult)

    rhr = aw["resting_hr_proxy"]
    hrv = cfg["baselines"]["hrv_rmssd_ms"]
    sleep = fit["sleep_minutes"]
    ex = fit["exercise"]
    log_hrv = hrv["log_mean"] + hrv["age_slope_per_year"] * (
        picked["age"] - effects["reference_age"]
    )
    users = pd.DataFrame(
        {
            "user_id": [f"synthetic-{i:05d}" for i in range(n)],
            "cohort": assign_cohorts(rng, n, cohort_cfg["composition"]),
            "synthea_patient_id": picked["synthea_patient_id"],
            "synthea_profile_reused": reused,
            "age": picked["age"].astype(int),
            "sex": picked["sex"],
            "conditions": picked["conditions"],
            "medications": picked["medications"],
            **{flag: picked[flag] for flag in effects if isinstance(effects[flag], dict)},
            "timezone": cohort_cfg["timezone"],
            "start_date": end - pd.to_timedelta(n_days - 1, unit="D"),
            "n_days": n_days,
            "base_resting_hr": rng.normal(rhr["between_mean"], rhr["between_sd"], n) + rhr_delta,
            "base_hrv_rmssd": np.exp(rng.normal(log_hrv, hrv["log_sd"])) * hrv_mult,
            "base_sleep_minutes": np.clip(
                rng.normal(sleep["between_mean"], sleep["between_sd"], n),
                *cfg["baselines"]["sleep_minutes"]["clip"],
            ),
            "base_steps": np.exp(
                rng.normal(fit["steps_log"]["between_mean"], fit["steps_log"]["between_sd"], n)
            )
            * steps_mult,
            "exercise_probability": rng.beta(
                ex["frequency_beta"]["a"], ex["frequency_beta"]["b"], n
            ),
            "exercise_minutes_on": np.exp(
                rng.normal(ex["on_minutes_log_mean"], ex["on_minutes_log_sd"], n)
            ),
            "exercise_minutes_off": np.exp(
                rng.normal(ex["off_minutes_log_mean"], ex["off_minutes_log_sd"], n)
            ),
            "daily_mean_hr_offset": rng.normal(
                fit["daily_mean_hr"]["between_mean"] - rhr["between_mean"],
                fit["daily_mean_hr"]["between_sd"],
                n,
            ),
            "bedtime_hour": rng.normal(
                fit["bedtime_hour"]["between_mean"], fit["bedtime_hour"]["between_sd"], n
            ),
            "sleep_efficiency": fit["sleep_efficiency"],
        }
    )
    users["providers"] = users["cohort"].map(lambda c: ",".join(COHORT_PROVIDERS[c]))
    return users
