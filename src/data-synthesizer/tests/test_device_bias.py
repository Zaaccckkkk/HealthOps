"""Device-bias layer of synthetic-v0.2: off by default, recorded exactly, applied as configured."""

import numpy as np
import pandas as pd
import pytest
from fakes import edit_config, prepare_data_root, set_env

from synthesizer import cli, device_bias

PROVIDERS = ("healthkit", "google_health")
DEVICE_CONFIGS = {"healthkit": "apple_watch.yaml", "google_health": "fitbit.yaml"}


def _drop_bias(cfg):
    cfg.pop("bias", None)


def _zero_bias(cfg):
    for metric, block in cfg["bias"].items():
        for key, value in block.items():
            if isinstance(value, (int, float)) and key != "low_activity_steps":
                block[key] = 0


def _run(tmp_path, monkeypatch, version, configure):
    data, configs = prepare_data_root(tmp_path / version, n_users=12)
    for provider, name in DEVICE_CONFIGS.items():
        edit_config(configs, name, configure)
    set_env(monkeypatch, data, configs, version)
    assert cli.main() == 0
    return data / "synthetic" / version


def _read(out):
    return {
        name: pd.read_parquet(out / f"{name}.parquet")
        for name in (
            "observations_healthkit",
            "observations_google_health",
            "latent_daily",
            "missingness_events",
        )
    }


def test_missing_bias_block_equals_zero_bias(tmp_path, monkeypatch):
    """Configs without a bias block and configs with every bias at 0 produce identical data,
    including the random noise: the layer adds no random draws of its own (v0.1 regression)."""
    without = _read(_run(tmp_path, monkeypatch, "synthetic-v0.0", _drop_bias))
    zeros = _read(_run(tmp_path, monkeypatch, "synthetic-v0.1", _zero_bias))
    for name, frame in without.items():
        pd.testing.assert_frame_equal(
            frame.drop(columns=["provenance_version"], errors="ignore"),
            zeros[name].drop(columns=["provenance_version"], errors="ignore"),
        )


def test_bias_leaves_latent_and_missingness_unchanged(tmp_path, monkeypatch):
    """Switching the bias on changes observations only: truth and gaps are the same."""
    without = _read(_run(tmp_path, monkeypatch, "synthetic-v0.0", _drop_bias))
    with_bias = _read(_run(tmp_path, monkeypatch, "synthetic-v0.2", lambda cfg: None))
    for name in ("latent_daily", "missingness_events"):
        pd.testing.assert_frame_equal(without[name], with_bias[name])
    assert not without["observations_google_health"]["value"].equals(
        with_bias["observations_google_health"]["value"]
    )


def test_recorded_bias_matches_observations(tmp_path, monkeypatch):
    """observation - latent - recorded bias is zero-mean noise for a metric without clipping."""
    out = _run(tmp_path, monkeypatch, "synthetic-v0.2", lambda cfg: None)
    latent = pd.read_parquet(out / "latent_daily.parquet")
    bias = pd.read_parquet(out / "device_bias_daily.parquet")
    rows = pd.read_parquet(out / "observations_google_health.parquet")
    rhr = rows[rows.metric == "resting_heart_rate"].copy()
    rhr["date"] = rhr["started_at"].dt.tz_convert(rhr["source_timezone"].iloc[0]).dt.normalize()
    rhr["date"] = rhr["date"].dt.tz_localize(None)
    truth = latent[["user_id", "date", "resting_hr"]]
    applied = bias[(bias.provider == "google_health") & (bias.metric == "resting_heart_rate")]
    merged = rhr.merge(truth, on=["user_id", "date"]).merge(
        applied[["user_id", "date", "bias_total"]], on=["user_id", "date"]
    )
    assert len(merged) > 100
    residual = merged["value"] - merged["resting_hr"] - merged["bias_total"]
    assert abs(residual.mean()) < 0.3  # noise_sd_bpm is 1.0
    assert abs(merged["bias_total"].mean() - (-2.0)) < 1.0  # fitbit.yaml mean_bpm


def test_user_offsets_follow_config_and_own_stream():
    cfgs = {
        "healthkit": {"bias": {"sleep_duration": {"user_sd_minutes": 15}}},
        "google_health": {},
    }
    offsets = device_bias.user_offsets(np.random.default_rng(1), 500, cfgs)
    assert offsets["bias_google_health_sleep_minutes"].tolist() == [0.0] * 500
    assert offsets["bias_healthkit_steps_log"].tolist() == [0.0] * 500
    drawn = offsets["bias_healthkit_sleep_minutes"]
    assert 10 < drawn.std() < 20 and abs(drawn.mean()) < 3


@pytest.mark.parametrize("workout,fragmented", [("hard", False), ("none", True)])
def test_daily_context_terms(workout, fragmented):
    cfg = {
        "bias": {
            "sleep_duration": {"mean_minutes": 10, "fragmented_night_extra_minutes": 15},
            "steps": {"log_mean": 0.05, "hard_workout_log_extra": 0.05, "low_activity_steps": 0},
        }
    }
    latent = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01"]),
            "context_illness": [fragmented],
            "context_travel": [False],
            "context_workout": [workout],
            "steps": [8000.0],
        }
    )
    user = pd.Series({"user_id": "u", "bias_google_health_sleep_minutes": 3.0})
    frame = device_bias.daily(user, latent, cfg, "google_health")
    sleep = frame[frame.metric == "sleep_duration"].iloc[0]
    steps = frame[frame.metric == "steps"].iloc[0]
    assert sleep.bias_total == 10 + 3 + (15 if fragmented else 0)
    assert steps.bias_total == pytest.approx(0.05 + (0.05 if workout == "hard" else 0))
    assert device_bias.arrays(frame)["resting_heart_rate"].tolist() == [0.0]
