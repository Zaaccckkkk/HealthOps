"""Provider-specific missing-data plans (configs/missingness.yaml).

A plan says, for each day of one user and one provider, whether the day is missing, whether
the night starting that day is missing, when a battery ran out, and which metrics dropped.
Every decision is also returned as an event row so evaluations know the ground truth.
"""

from __future__ import annotations

import numpy as np


def resolve(cfg: dict, provider: str, calibration: dict) -> dict:
    """missingness.yaml section for a provider with `calibrated` entries filled in."""
    section = {k: (dict(v) if isinstance(v, dict) else v) for k, v in cfg[provider].items()}
    if provider == "google_health":
        fitted = calibration["fitbit"]["missingness"]
        lookup = {
            ("not_worn_day", "probability"): fitted["not_worn_day_probability"],
            ("multi_day_gap", "start_probability"): fitted["multi_day_gap_start_probability"],
            ("multi_day_gap", "mean_length_days"): fitted["multi_day_gap_mean_length_days"],
            ("overnight_not_worn", "probability"): fitted["overnight_not_worn_probability"],
        }
    else:
        lookup = {
            ("sample_dropout", "probability"): calibration["apple_watch"]["hr_sample_dropout"]
        }
    for (mechanism, key), value in lookup.items():
        if section.get(mechanism, {}).get(key) == "calibrated":
            section[mechanism][key] = value
    return section


def plan(rng: np.random.Generator, n_days: int, cfg: dict) -> dict:
    day_missing = np.zeros(n_days, dtype=bool)
    night_missing = np.zeros(n_days, dtype=bool)
    battery_hour = np.full(n_days, np.nan)
    events: list[dict] = []

    def mark(day: int, mechanism: str, **detail) -> None:
        events.append({"day_index": day, "mechanism": mechanism, **detail})

    for day in np.flatnonzero(rng.random(n_days) < cfg["not_worn_day"]["probability"]):
        day_missing[day] = True
        mark(int(day), "not_worn_day")

    gap = cfg["multi_day_gap"]
    day = 0
    while day < n_days:
        if rng.random() < gap["start_probability"]:
            length = 1 + int(rng.geometric(1 / max(gap["mean_length_days"] - 1, 1)))
            for d in range(day, min(n_days, day + length)):
                day_missing[d] = True
                mark(d, "multi_day_gap", gap_length_days=length)
            day += length
        else:
            day += 1

    sync = cfg["sync_failure"]
    for start in np.flatnonzero(rng.random(n_days) < sync["probability"]):
        length = int(rng.integers(sync["length_days"][0], sync["length_days"][1] + 1))
        for d in range(int(start), min(n_days, int(start) + length)):
            day_missing[d] = True
            mark(d, "sync_failure", window_days=length)

    battery = cfg["battery_depletion"]
    for d in np.flatnonzero(rng.random(n_days) < battery["probability"]):
        battery_hour[d] = rng.uniform(battery["earliest_hour"], battery["latest_hour"])
        night_missing[d] = True
        mark(int(d), "battery_depletion", local_hour=round(float(battery_hour[d]), 2))

    for d in np.flatnonzero(rng.random(n_days) < cfg["overnight_not_worn"]["probability"]):
        night_missing[d] = True
        mark(int(d), "overnight_not_worn")

    night_missing |= day_missing
    dropout = {}
    for metric, probability in (cfg.get("metric_dropout") or {}).items():
        dropout[metric] = rng.random(n_days) < probability
        for d in np.flatnonzero(dropout[metric] & ~day_missing):
            mark(int(d), "metric_dropout", metric=metric)
    return {
        "day_missing": day_missing,
        "night_missing": night_missing,
        "battery_hour": battery_hour,
        "metric_dropout": dropout,
        "sample_dropout": (cfg.get("sample_dropout") or {}).get("probability", 0.0),
        "events": events,
    }
