"""Standardisation and 14-day windows for the neural models.

Per day and metric the window carries the standardised reading of each device (0 when missing)
and its mask, the context flags, the day of week and a validity flag (0 for the padding before
a user's first day). Targets are the standardised latent values. Steps are handled in
log1p space everywhere; `Scaler` converts back.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from training.data import METRICS, PROVIDERS, SHORT, STRESS, WORKOUT

WINDOW = 14
DAY_FEATURES = (
    4 * len(METRICS) + 3 + 4 + 3 + 7 + 1
)  # readings/masks, stress, workout, flags, dow, valid


def to_model_space(name: str, values):
    return np.log1p(values) if name == "steps" else values


def from_model_space(name: str, values):
    return np.expm1(values) if name == "steps" else values


class Scaler:
    """Per-metric mean/sd of the truth on the training split (model space)."""

    def __init__(self, daily: pd.DataFrame | None = None, stats: dict | None = None):
        if stats is not None:
            self.stats = stats
            return
        train = daily[daily["split"] == "train"]
        self.stats = {}
        for name in METRICS:
            values = to_model_space(name, train[f"truth_{name}"].to_numpy(dtype=float))
            self.stats[name] = {
                "mean": float(np.nanmean(values)),
                "sd": float(np.nanstd(values) + 1e-6),
            }

    def encode(self, name: str, values):
        s = self.stats[name]
        return (to_model_space(name, np.asarray(values, dtype=float)) - s["mean"]) / s["sd"]

    def decode(self, name: str, values):
        s = self.stats[name]
        return from_model_space(name, np.asarray(values, dtype=float) * s["sd"] + s["mean"])


def day_matrix(
    daily: pd.DataFrame, scaler: Scaler
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-day arrays for every row of `daily` (any order): features [n, F], targets [n, 3],
    observed channels [n, 6] (standardised, 0 when missing), masks [n, 6]."""
    n = len(daily)
    obs = np.zeros((n, 2 * len(METRICS)))
    mask = np.zeros((n, 2 * len(METRICS)))
    for i, name in enumerate(METRICS):
        for j, provider in enumerate(PROVIDERS):
            values = daily[f"obs_{SHORT[provider]}_{name}"].to_numpy(dtype=float)
            present = ~np.isnan(values)
            col = 2 * i + j
            mask[:, col] = present
            obs[present, col] = scaler.encode(name, values[present])
    context = np.zeros((n, 3 + 4 + 3))
    stress = daily["context_stress"].map({s: k for k, s in enumerate(STRESS)}).to_numpy()
    workout = daily["context_workout"].map({w: k for k, w in enumerate(WORKOUT)}).to_numpy()
    context[np.arange(n), stress] = 1
    context[np.arange(n), 3 + workout] = 1
    context[:, 7] = daily["context_travel"].astype(bool).to_numpy()
    context[:, 8] = daily["context_illness"].astype(bool).to_numpy()
    context[:, 9] = daily["context_subjective_fatigue"].to_numpy(dtype=float) / 10
    dow = np.zeros((n, 7))
    dow[np.arange(n), daily["day_of_week"].to_numpy()] = 1
    features = np.concatenate([obs, mask, context, dow, np.ones((n, 1))], axis=1)
    assert features.shape[1] == DAY_FEATURES
    targets = np.stack(
        [scaler.encode(name, daily[f"truth_{name}"].to_numpy(dtype=float)) for name in METRICS],
        axis=1,
    )
    return features, targets, obs, mask


def make_windows(
    daily: pd.DataFrame,
    scaler: Scaler,
    splits: tuple[str, ...],
    stride: int = 1,
    window: int = WINDOW,
) -> dict:
    """Windows of `window` days (default WINDOW) ending on every `stride`-th day of each user
    in `splits`. A user with fewer days than the window gets zero padding and valid = 0 there.

    Returns arrays: x [N, L, F], y_last [N, 3], y_seq [N, L, 3], obs_seq [N, L, 6],
    mask_seq [N, L, 6], valid [N, L] and a meta frame (user_id, date, cohort)."""
    frame = (
        daily[daily["split"].isin(splits)].sort_values(["user_id", "date"]).reset_index(drop=True)
    )
    features, targets, obs, mask = day_matrix(frame, scaler)
    xs, y_last, y_seq, obs_seq, mask_seq, valid, meta = [], [], [], [], [], [], []
    for user_id, idx in frame.groupby("user_id", sort=False).indices.items():
        idx = np.sort(idx)
        n = len(idx)
        pad = window - 1
        f = np.concatenate([np.zeros((pad, features.shape[1])), features[idx]])
        t = np.concatenate([np.zeros((pad, targets.shape[1])), targets[idx]])
        o = np.concatenate([np.zeros((pad, obs.shape[1])), obs[idx]])
        m = np.concatenate([np.zeros((pad, mask.shape[1])), mask[idx]])
        v = np.concatenate([np.zeros(pad), np.ones(n)])
        ends = range(n - 1, -1, -stride) if stride > 1 else range(n)
        for end in sorted(ends):
            start = end  # window covers padded positions start .. start + window - 1
            xs.append(f[start : start + window])
            y_seq.append(t[start : start + window])
            obs_seq.append(o[start : start + window])
            mask_seq.append(m[start : start + window])
            valid.append(v[start : start + window])
            y_last.append(targets[idx[end]])
            meta.append((user_id, frame.loc[idx[end], "date"], frame.loc[idx[end], "cohort"]))
    return {
        "x": np.asarray(xs, dtype=np.float32),
        "y_last": np.asarray(y_last, dtype=np.float32),
        "y_seq": np.asarray(y_seq, dtype=np.float32),
        "obs_seq": np.asarray(obs_seq, dtype=np.float32),
        "mask_seq": np.asarray(mask_seq, dtype=np.float32),
        "valid": np.asarray(valid, dtype=np.float32),
        "meta": pd.DataFrame(meta, columns=["user_id", "date", "cohort"]),
    }


def quantile_frame(meta: pd.DataFrame, q: np.ndarray, scaler: Scaler) -> pd.DataFrame:
    """q [N, 3 metrics, 3 quantiles] in model space -> long frame with q05/q50/q95 in data units."""
    q = np.sort(q, axis=2)  # separately trained quantiles may cross; sorting is the standard fix
    q = np.clip(q, -8, 8)  # an untrained sampler cannot overflow expm1 for steps
    parts = []
    for i, name in enumerate(METRICS):
        parts.append(
            pd.DataFrame(
                {
                    "user_id": meta["user_id"].to_numpy(),
                    "date": meta["date"].to_numpy(),
                    "metric": name,
                    "q05": scaler.decode(name, q[:, i, 0]),
                    "q50": scaler.decode(name, q[:, i, 1]),
                    "q95": scaler.decode(name, q[:, i, 2]),
                }
            )
        )
    return pd.concat(parts, ignore_index=True)
