"""Systematic device error (synthetic-v0.2).

The observation models (observe.py) report `latent + bias + noise`. This module computes the
bias from the `bias:` block of apple_watch.yaml / fitbit.yaml (docs/device-bias.md):

    constant   the same for every user of the device
    user       a stable per-user offset, drawn once per provider (how the device is worn)
    context    day-dependent: fragmented nights (illness or travel), hard workouts,
               low-activity days

Every applied value is written to device_bias_daily.parquet so that a model trained on the
synthetic data can be checked against the error it was supposed to recover. A config without a
`bias:` block applies zero everywhere and reproduces synthetic-v0.1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

METRICS = ("sleep_duration", "steps", "resting_heart_rate")
UNITS = {"sleep_duration": "min", "steps": "log", "resting_heart_rate": "bpm"}
# (per-user column suffix, config key of the per-user SD)
USER_OFFSETS = {
    "sleep_duration": ("sleep_minutes", "user_sd_minutes"),
    "steps": ("steps_log", "user_log_sd"),
    "resting_heart_rate": ("resting_hr_bpm", "user_sd_bpm"),
}


def _block(cfg: dict, metric: str) -> dict:
    return (cfg.get("bias") or {}).get(metric) or {}


def user_column(provider: str, metric: str) -> str:
    return f"bias_{provider}_{USER_OFFSETS[metric][0]}"


def user_offsets(rng: np.random.Generator, n: int, cfgs: dict[str, dict]) -> dict[str, np.ndarray]:
    """One per-user offset column per provider and metric (zeros without a bias block).

    `rng` must be a stream of its own so that adding these draws leaves every other column of
    users.parquet unchanged.
    """
    columns = {}
    for provider, cfg in cfgs.items():
        for metric in METRICS:
            sd = float(_block(cfg, metric).get(USER_OFFSETS[metric][1], 0.0))
            columns[user_column(provider, metric)] = (
                rng.normal(0.0, sd, n) if sd > 0 else np.zeros(n)
            )
    return columns


def daily(user: pd.Series, latent: pd.DataFrame, cfg: dict, provider: str) -> pd.DataFrame:
    """Constant, user, context and total bias for every day and metric of one user/provider."""
    n = len(latent)
    fragmented = (
        latent["context_illness"].astype(bool) | latent["context_travel"].astype(bool)
    ).to_numpy()
    hard = (latent["context_workout"] == "hard").to_numpy()
    steps = latent["steps"].to_numpy(dtype=float)
    parts = []
    for metric in METRICS:
        b = _block(cfg, metric)
        user_offset = float(user.get(user_column(provider, metric), 0.0))
        if metric == "sleep_duration":
            constant = float(b.get("mean_minutes", 0.0))
            context = np.where(fragmented, float(b.get("fragmented_night_extra_minutes", 0.0)), 0.0)
        elif metric == "steps":
            constant = float(b.get("log_mean", 0.0))
            context = np.where(hard, float(b.get("hard_workout_log_extra", 0.0)), 0.0) + np.where(
                steps < float(b.get("low_activity_steps", 0.0)),
                float(b.get("low_activity_log_extra", 0.0)),
                0.0,
            )
        else:
            constant = float(b.get("mean_bpm", 0.0))
            context = np.zeros(n)
        parts.append(
            pd.DataFrame(
                {
                    "user_id": user["user_id"],
                    "provider": provider,
                    "date": latent["date"].to_numpy(),
                    "metric": metric,
                    "unit": UNITS[metric],
                    "bias_constant": constant,
                    "bias_user": user_offset,
                    "bias_context": context,
                    "bias_total": constant + user_offset + context,
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def arrays(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """metric -> total bias per day (in day order), as observe.py consumes it."""
    return {
        metric: frame.loc[frame["metric"] == metric, "bias_total"].to_numpy(dtype=float)
        for metric in METRICS
    }
