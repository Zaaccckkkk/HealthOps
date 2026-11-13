"""Latent daily health state of one user (the "true" values before any device sees them).

Each series is the user's baseline plus an AR(1) deviation plus effects of sleep debt,
training load and the day's context, so consecutive days are correlated: several short
nights raise resting HR and lower HRV gradually instead of independently each day.
AR(1) innovations use sd * sqrt(1 - ar1^2), which keeps the marginal day-to-day SD equal to
the calibrated within-user SD.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _resolve(value, calibrated: float) -> float:
    return calibrated if value == "calibrated" else float(value)


def parameters(cfg: dict, calibration: dict) -> dict:
    """trajectory.yaml with every `calibrated` entry replaced by its estimate."""
    fit, aw = calibration["fitbit"], calibration["apple_watch"]
    return {
        "sleep_ar1": _resolve(cfg["sleep"]["ar1"], fit["sleep_minutes"]["ar1"]),
        "sleep_sd": _resolve(cfg["sleep"]["noise_sd_minutes"], fit["sleep_minutes"]["within_sd"]),
        "debt_decay": cfg["sleep"]["debt_decay"],
        "steps_ar1": _resolve(cfg["steps"]["ar1"], fit["steps_log"]["ar1"]),
        "steps_sd": _resolve(cfg["steps"]["noise_log_sd"], fit["steps_log"]["within_sd"]),
        "day_of_week": (
            fit["day_of_week_multipliers"]
            if cfg["steps"]["day_of_week"] == "calibrated"
            else cfg["steps"]["day_of_week"]
        ),
        "rhr_ar1": cfg["resting_hr"]["ar1"],
        "rhr_sd": _resolve(cfg["resting_hr"]["noise_sd_bpm"], aw["resting_hr_proxy"]["within_sd"]),
        "rhr_debt": cfg["resting_hr"]["per_hour_sleep_debt"],
        "rhr_load": cfg["resting_hr"]["per_unit_training_load"],
        "hrv_ar1": cfg["hrv"]["ar1"],
        "hrv_sd": cfg["hrv"]["noise_log_sd"],
        "hrv_debt": cfg["hrv"]["per_hour_sleep_debt"],
        "hrv_load": cfg["hrv"]["per_unit_training_load"],
        "mhr_sd": _resolve(cfg["daily_mean_hr"]["noise_sd_bpm"], fit["daily_mean_hr"]["within_sd"]),
        "mhr_slope": _resolve(
            cfg["daily_mean_hr"]["per_log_steps"], fit["daily_mean_hr"]["per_log_steps"]
        ),
        "bed_sd": _resolve(cfg["bedtime"]["noise_sd_hours"], fit["bedtime_hour"]["within_sd"]),
        "efficiency": _resolve(cfg["bedtime"]["sleep_efficiency"], fit["sleep_efficiency"]),
        "intensity": cfg["exercise"]["intensity"],
        "load_decay": cfg["exercise"]["load_decay"],
        "ex_noise": cfg["exercise"]["minutes_noise_log_sd"],
        "ex_hours": cfg["exercise"]["start_hour_range"],
        "effects": cfg["context_effects"],
    }


def _effects(context: dict, effects: dict) -> dict:
    total = {
        "resting_hr": 0.0,
        "hrv_log": 0.0,
        "sleep_minutes": 0.0,
        "steps_log": 0.0,
        "bedtime_hours": 0.0,
    }
    chosen = [
        effects["stress"].get(context["stress"]) or {},
        effects["workout"].get(context["workout"]) or {},
        (effects["travel"].get("true") or {}) if context["travel"] else {},
        (effects["illness"].get("true") or {}) if context["illness"] else {},
    ]
    above = max(0, context["subjective_fatigue"] - 5)
    chosen.append(
        {k: v * above for k, v in effects["subjective_fatigue"]["per_point_above_5"].items()}
    )
    for effect in chosen:
        for key, value in effect.items():
            total[key] += value
    return total


def simulate(
    user: pd.Series, contexts: list[dict], p: dict, rng: np.random.Generator
) -> pd.DataFrame:
    n = int(user["n_days"])
    dates = pd.date_range(user["start_date"], periods=n, freq="D")
    rows = []
    sleep_dev = steps_dev = rhr_dev = hrv_dev = debt = load = 0.0
    previous_wake = -np.inf  # hours since the first local midnight
    for t in range(n):
        context, effect = contexts[t], _effects(contexts[t], p["effects"])
        # Morning physiology reacts to last night's sleep debt and yesterday's training load.
        rhr_dev = p["rhr_ar1"] * rhr_dev + rng.normal(
            0, p["rhr_sd"] * np.sqrt(1 - p["rhr_ar1"] ** 2)
        )
        resting_hr = (
            user["base_resting_hr"]
            + rhr_dev
            + p["rhr_debt"] * debt
            + p["rhr_load"] * load
            + effect["resting_hr"]
        )
        hrv_dev = p["hrv_ar1"] * hrv_dev + rng.normal(
            0, p["hrv_sd"] * np.sqrt(1 - p["hrv_ar1"] ** 2)
        )
        hrv = user["base_hrv_rmssd"] * np.exp(
            hrv_dev + p["hrv_debt"] * debt + p["hrv_load"] * load + effect["hrv_log"]
        )

        # Activity: explicit workouts from context, otherwise the user's usual frequency.
        workout = "rest" if context["illness"] else context["workout"]
        steps_log = effect["steps_log"]
        if workout in ("moderate", "hard"):
            intensity = workout
        elif workout == "none" and rng.random() < user["exercise_probability"]:
            intensity = "moderate"
            steps_log += (p["effects"]["workout"].get("moderate") or {}).get("steps_log", 0.0)
        else:
            intensity = None
        minutes = user["exercise_minutes_on"] if intensity else user["exercise_minutes_off"]
        exercise_minutes = minutes * np.exp(rng.normal(0, p["ex_noise"]))
        load = p["load_decay"] * load + (p["intensity"][intensity] if intensity else 0.0)
        steps_dev = p["steps_ar1"] * steps_dev + rng.normal(
            0, p["steps_sd"] * np.sqrt(1 - p["steps_ar1"] ** 2)
        )
        steps = (
            user["base_steps"]
            * p["day_of_week"][dates[t].dayofweek]
            * np.exp(steps_dev + steps_log)
        )
        daily_mean_hr = (
            resting_hr
            + user["daily_mean_hr_offset"]
            + p["mhr_slope"] * np.log(steps / user["base_steps"])
            + rng.normal(0, p["mhr_sd"])
        )

        # The night that starts this evening.
        sleep_dev = p["sleep_ar1"] * sleep_dev + rng.normal(
            0, p["sleep_sd"] * np.sqrt(1 - p["sleep_ar1"] ** 2)
        )
        sleep = float(
            np.clip(user["base_sleep_minutes"] + sleep_dev + effect["sleep_minutes"], 120, 900)
        )
        debt = max(0.0, p["debt_decay"] * debt + (user["base_sleep_minutes"] - sleep) / 60)
        time_in_bed = sleep / p["efficiency"]
        bedtime = user["bedtime_hour"] + effect["bedtime_hours"] + rng.normal(0, p["bed_sd"])
        bedtime = max(bedtime, previous_wake + 1 - 24 * t)  # nights never overlap
        previous_wake = 24 * t + bedtime + time_in_bed / 60
        rows.append(
            {
                "user_id": user["user_id"],
                "date": dates[t],
                "day_index": t,
                **{
                    f"context_{k}": context[k]
                    for k in ("stress", "workout", "travel", "illness", "subjective_fatigue")
                },
                "event": context["event"],
                "resting_hr": resting_hr,
                "hrv_rmssd": hrv,
                "steps": steps,
                "exercise_intensity": intensity,
                "exercise_minutes": exercise_minutes,
                "exercise_start_hour": rng.uniform(*p["ex_hours"]),
                "training_load": load,
                "daily_mean_hr": max(daily_mean_hr, resting_hr),
                "bedtime_hour": bedtime,
                "sleep_minutes": sleep,
                "time_in_bed_minutes": time_in_bed,
                "sleep_debt_hours": debt,
            }
        )
    return pd.DataFrame(rows)
