"""The training package on a tiny generated cohort: data assembly, every model, the evaluation."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from training import data, evaluate
from training.diffusion_model import DiffusionModel
from training.lightgbm_model import LightGBMModel
from training.transformer_model import TransformerModel
from training.windows import WINDOW, Scaler, make_windows

TZ = "America/New_York"


def _observations(user_id, provider, dates, latent, rng, bias):
    """Canonical-schema rows for one user, in the aggregation each device uses."""
    rows = []
    midnight = pd.DatetimeIndex(dates).tz_localize(TZ)
    for d, day in enumerate(midnight):
        if rng.random() < 0.1:
            continue  # a day not worn
        start = day.tz_convert("UTC")
        if provider == "google_health":
            rows.append(
                (
                    "steps",
                    "count",
                    "daily_total",
                    start,
                    start + pd.Timedelta(days=1),
                    round(latent["steps"][d] * np.exp(bias["steps"] + rng.normal(0, 0.05))),
                )
            )
            rows.append(
                (
                    "resting_heart_rate",
                    "bpm",
                    "daily_summary",
                    start,
                    start + pd.Timedelta(days=1),
                    latent["rhr"][d] + bias["rhr"] + rng.normal(0, 1),
                )
            )
            bed = start + pd.Timedelta(hours=23)
            rows.append(
                (
                    "sleep_duration",
                    "s",
                    "interval_total",
                    bed,
                    bed + pd.Timedelta(hours=8),
                    (latent["sleep"][d] + bias["sleep"] + rng.normal(0, 15)) * 60,
                )
            )
        else:
            at = start + pd.Timedelta(hours=8)
            rows.append(
                (
                    "resting_heart_rate",
                    "bpm",
                    "sample",
                    at,
                    at,
                    latent["rhr"][d] + bias["rhr"] + rng.normal(0, 1),
                )
            )
            total = latent["steps"][d] * np.exp(bias["steps"] + rng.normal(0, 0.05))
            for hour in range(8, 22):
                t = start + pd.Timedelta(hours=hour)
                rows.append(
                    (
                        "steps",
                        "count",
                        "interval_total",
                        t,
                        t + pd.Timedelta(hours=1),
                        round(total / 14),
                    )
                )
            bed = start + pd.Timedelta(hours=23, minutes=10)
            half = (latent["sleep"][d] + bias["sleep"] + rng.normal(0, 15)) * 30
            rows.append(
                (
                    "sleep_duration",
                    "s",
                    "interval_total",
                    bed,
                    bed + pd.Timedelta(seconds=half),
                    half,
                )
            )
            second = bed + pd.Timedelta(seconds=half + 600)
            rows.append(
                (
                    "sleep_duration",
                    "s",
                    "interval_total",
                    second,
                    second + pd.Timedelta(seconds=half),
                    half,
                )
            )
    frame = pd.DataFrame(
        rows, columns=["metric", "unit", "aggregation", "started_at", "ended_at", "value"]
    )
    return frame.assign(
        user_id=user_id,
        provider=provider,
        source_timezone=TZ,
        source_device="test",
        source_record_id=[f"{user_id}:{i}" for i in range(len(frame))],
        provenance_dataset="synthetic",
        provenance_version="synthetic-v0.9",
        provenance_source="test",
    )


def make_cohort(root: Path, n_users: int = 12, n_days: int = 40, seed: int = 0) -> str:
    """A synthetic version with the tables the training package reads (DATA_ROOT layout)."""
    rng = np.random.default_rng(seed)
    version = "synthetic-v0.9"
    folder = root / "synthetic" / version
    folder.mkdir(parents=True)
    cohorts = ["paired", "apple_watch_only", "fitbit_only"]
    users, latent, bias_rows, obs = [], [], [], {"healthkit": [], "google_health": []}
    bias_cfg = {
        "healthkit": {"sleep": 20.0, "steps": -0.02, "rhr": -1.0},
        "google_health": {"sleep": 10.0, "steps": 0.06, "rhr": -2.0},
    }
    for i in range(n_users):
        uid, cohort = f"synthetic-{i:05d}", cohorts[i % 3]
        dates = pd.date_range("2026-06-01", periods=n_days, freq="D")
        truth = {
            "sleep": np.clip(rng.normal(430, 40, n_days), 240, 600),
            "steps": np.exp(rng.normal(np.log(8000), 0.3, n_days)),
            "rhr": rng.normal(60, 3, n_days),
        }
        providers = {
            "paired": ["healthkit", "google_health"],
            "apple_watch_only": ["healthkit"],
            "fitbit_only": ["google_health"],
        }[cohort]
        users.append(
            {
                "user_id": uid,
                "cohort": cohort,
                "timezone": TZ,
                "providers": ",".join(providers),
                "n_days": n_days,
            }
        )
        for d in range(n_days):
            latent.append(
                {
                    "user_id": uid,
                    "date": dates[d],
                    "day_index": d,
                    "context_stress": "low",
                    "context_workout": "none",
                    "context_travel": False,
                    "context_illness": d % 17 == 0,
                    "context_subjective_fatigue": 3,
                    "event": None,
                    "resting_hr": truth["rhr"][d],
                    "steps": truth["steps"][d],
                    "sleep_minutes": truth["sleep"][d],
                    "bedtime_hour": 23.0 if True else 0,
                    "time_in_bed_minutes": truth["sleep"][d] / 0.9,
                }
            )
        for provider in providers:
            user_offset = {
                m: rng.normal(0, {"sleep": 15, "steps": 0.05, "rhr": 1}[m]) for m in data.METRICS
            }
            total = {m: bias_cfg[provider][m] + user_offset[m] for m in data.METRICS}
            obs[provider].append(_observations(uid, provider, dates, truth, rng, total))
            for d in range(n_days):
                for m, canonical in (
                    ("sleep", "sleep_duration"),
                    ("steps", "steps"),
                    ("rhr", "resting_heart_rate"),
                ):
                    bias_rows.append(
                        {
                            "user_id": uid,
                            "provider": provider,
                            "date": dates[d],
                            "metric": canonical,
                            "unit": "x",
                            "bias_constant": bias_cfg[provider][m],
                            "bias_user": user_offset[m],
                            "bias_context": 0.0,
                            "bias_total": total[m],
                        }
                    )
    pd.DataFrame(users).to_parquet(folder / "users.parquet", index=False)
    pd.DataFrame(latent).to_parquet(folder / "latent_daily.parquet", index=False)
    pd.DataFrame(bias_rows).to_parquet(folder / "device_bias_daily.parquet", index=False)
    for provider, parts in obs.items():
        pd.concat(parts, ignore_index=True).to_parquet(
            folder / f"observations_{provider}.parquet", index=False
        )
    (root / "manifests").mkdir()
    (root / "manifests" / f"{version}.yaml").write_text(f"version: {version}\n")
    return version


@pytest.fixture(scope="module")
def cohort(tmp_path_factory):
    root = tmp_path_factory.mktemp("data")
    version = make_cohort(root)
    return root, version


@pytest.fixture(scope="module")
def daily(cohort):
    root, version = cohort
    return data.encode_context(data.add_history(data.build_daily(root, version)))


def test_daily_table_matches_devices_and_truth(daily):
    assert len(daily) == 12 * 40
    assert set(daily["split"]) <= {"train", "val", "test"}
    paired = daily[daily["cohort"] == "paired"]
    for metric in data.METRICS:
        for p in ("hk", "gh"):
            have = paired[f"obs_{p}_{metric}"].notna()
            assert 0.8 < have.mean() <= 1.0  # 10 % of days not worn
            obs, truth = paired.loc[have, f"obs_{p}_{metric}"], paired.loc[have, f"truth_{metric}"]
            err = (np.log1p(obs) - np.log1p(truth)) if metric == "steps" else (obs - truth)
            # reading - truth - recorded bias is the measurement noise: zero-mean
            residual = err - paired.loc[have, f"bias_{p}_{metric}"]
            tolerance = {"sleep": 4, "steps": 0.02, "rhr": 0.4}[metric]
            assert abs(residual.mean()) < tolerance, (metric, p, residual.mean())
    single = daily[daily["cohort"] == "apple_watch_only"]
    assert single["obs_gh_sleep"].isna().all() and single["bias_gh_sleep"].isna().all()
    assert (
        daily["hist7_hk_sleep"].notna().sum() > 0
        and daily.loc[daily["day_index"] == 0, "hist7_hk_sleep"].isna().all()
    )


def test_split_is_by_user(daily):
    per_user = daily.groupby("user_id")["split"].nunique()
    assert (per_user == 1).all()


def test_windows_shapes(daily):
    scaler = Scaler(daily)
    w = make_windows(daily, scaler, ("train",), stride=7)
    assert w["x"].shape[1:] == (WINDOW, 30) and w["y_last"].shape[1] == 3
    assert w["valid"][:, -1].min() == 1  # the last day of a window is always a real day
    assert len(w["meta"]) == len(w["x"])


@pytest.mark.parametrize(
    "factory",
    [
        lambda: LightGBMModel({"n_estimators": 40}),
        lambda: TransformerModel({"epochs": 2, "d_model": 16, "ff": 32, "heads": 2}),
        lambda: DiffusionModel(
            {"epochs": 2, "hidden": 16, "blocks": 1, "ddim_steps": 5, "samples": 4}
        ),
    ],
    ids=["lightgbm", "transformer", "diffusion"],
)
def test_models_fit_predict_and_evaluate(daily, factory, tmp_path):
    model = factory()
    summary = model.fit(daily)
    assert summary
    held_out = daily[daily["split"].isin(["val", "test"])]
    pred = model.predict(held_out)
    assert set(pred.columns) >= {"user_id", "date", "metric", "q05", "q50", "q95"}
    assert len(pred) == 3 * len(held_out)
    assert (pred["q05"] <= pred["q50"]).all() and (pred["q50"] <= pred["q95"]).all()
    truth = evaluate.long_truth(daily)
    result = evaluate.evaluate(pred, truth, "test")
    scores = result["scores"]["by_metric"]
    assert all(0 <= scores[m]["coverage90"] <= 1 for m in data.METRICS if "coverage90" in scores[m])
    rule = result["scores"]["rule"]
    assert all(rule[m]["no_estimate_when_no_reading"] in (None, 1.0) for m in data.METRICS)
    model.save(tmp_path / model.name)
    assert (tmp_path / model.name / "meta.json").exists()
    assert json.loads((tmp_path / model.name / "meta.json").read_text())["scaler"]


def test_baselines_and_structure(daily):
    truth = evaluate.long_truth(daily)
    base = evaluate.baselines(truth)
    assert set(base) == {"apple_only", "fitbit_only", "device_average", "global_correction"}
    scored = evaluate.evaluate(base["global_correction"], truth, "test")["scores"]["by_metric"]
    raw = evaluate.evaluate(base["fitbit_only"], truth, "test")["scores"]["by_metric"]
    assert scored["sleep"]["mae"] < raw["sleep"]["mae"]  # removing the known constant helps
    structure = evaluate.structure_recovery(
        evaluate.apply_rule(base["apple_only"], truth), truth, "test"
    )
    assert all(np.isnan(v["residual_on_bias_slope"]) for v in structure.values())  # nothing removed


def test_tracker_ignores_empty_wandb_variables(tmp_path, monkeypatch):
    """Compose passes unset WANDB_* variables as "": the tracker must still start (offline)."""
    from training.tracking import Tracker

    for key in ("WANDB_MODE", "WANDB_API_KEY", "WANDB_ENTITY"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("WANDB_DIR", str(tmp_path))
    tracker = Tracker("test", {"a": 1})
    assert tracker.run is not None and tracker.mode == "offline"
    tracker.log({"x": 1.0})
    tracker.finish()
    assert any(tmp_path.rglob("offline-run-*"))


def test_fitbit_sleep_logged_at_the_bedtime_is_matched():
    """The synthesizer starts a Fitbit sleep log at ``midnight + int(bedtime_hour * HOUR)`` in
    integer nanoseconds (observe.py ``_timeline``) and the parquet schema stores it in
    microseconds, so the stored start is up to a millisecond before the nanosecond bedtime
    recomputed from the latent table. The backward match from reading to night must still
    attach that reading to its night; before the fix nearly every Fitbit night was dropped."""
    hour_ns = 3_600_000_000_000
    rng = np.random.default_rng(7)
    rows, lat = [], []
    for d, date in enumerate(pd.date_range("2026-06-01", periods=5, freq="D")):
        bedtime_hour = float(rng.normal(23.4, 0.9))  # a drawn bedtime, as calibrated ones are
        midnight_ns = date.tz_localize(TZ).tz_convert("UTC").value
        bed = pd.Timestamp(midnight_ns + int(bedtime_hour * hour_ns), tz="UTC").floor("us")
        rows.append(("synthetic-00001", "sleep_duration", 420 * 60, bed, TZ))
        lat.append({"user_id": "synthetic-00001", "date": date, "bedtime_hour": bedtime_hour})
    obs = pd.DataFrame(rows, columns=data.OBSERVATION_COLUMNS)
    users = pd.DataFrame({"user_id": ["synthetic-00001"], "timezone": [TZ]})
    beds = data._bed_times(pd.DataFrame(lat), users)
    readings = data._daily_readings(obs, beds)
    nights = readings[readings["metric"] == "sleep"]
    assert len(nights) == 5, nights
    assert np.allclose(nights["value"], 420)
    assert list(nights["date"]) == list(pd.date_range("2026-06-01", periods=5, freq="D"))
    assert (beds["bed_at"].to_numpy() <= obs["started_at"].to_numpy()).all()


def test_transformer_window_is_configurable(daily):
    """A 30-day transformer differs from the default only in its window: the window builder
    pads a user's first days to the requested length and the position embedding follows it."""
    from training.windows import DAY_FEATURES

    sc = Scaler(daily)
    w14 = make_windows(daily, sc, ("train",), window=14)
    w30 = make_windows(daily, sc, ("train",), window=30)
    assert w14["x"].shape[1:] == (14, DAY_FEATURES)
    assert w30["x"].shape[1:] == (30, DAY_FEATURES)
    assert len(w14["x"]) == len(w30["x"])  # one window per user-day either way
    assert w30["valid"][0, :29].sum() == 0  # a user's first window is all padding but the last day
    model = TransformerModel({"window": 30, "epochs": 1, "d_model": 16, "ff": 32}, device="cpu")
    summary = model.fit(daily)
    assert model.net.position.shape == (1, 30, 16)
    assert summary["windows"] == len(w30["x"])
    pred = model.predict(daily[daily["split"] == "test"])
    assert set(pred["metric"]) == set(data.METRICS)
    assert TransformerModel(device="cpu").cfg["window"] == WINDOW == 14


def test_prepare_only_then_train_from_prepared_and_compare(cohort, tmp_path, monkeypatch):
    """The pipeline stages: prepare writes the table; two train steps reuse it; compare merges
    their metrics into one table keyed by run label."""
    from training import compare, train

    monkeypatch.setenv("WANDB_MODE", "disabled")
    root, version = cohort
    prepared = tmp_path / "prep"
    assert (
        train.main(
            [
                "--data-root",
                str(root),
                "--version",
                version,
                "--out",
                str(prepared),
                "--prepare-only",
            ]
        )
        == 0
    )
    assert (prepared / "prepared" / "daily.parquet").exists()
    runs = {}
    for label, window in (("w14", 14), ("w30", 30)):
        out = tmp_path / label
        code = train.main(
            [
                "--data-root",
                str(root),
                "--version",
                version,
                "--out",
                str(out),
                "--prepared",
                str(prepared / "prepared"),
                "--models",
                "transformer",
                "--smoke",
                "--run-suffix",
                label,
                "--group",
                "test-group",
                "--config",
                json.dumps({"transformer": {"window": window}}),
            ]
        )
        assert code == 0
        record = json.loads((out / "run.json").read_text())
        assert record["group"] == "test-group"
        assert record["models"]["transformer"]["config"]["window"] == window
        assert not (out / "prepared").exists()  # reused, not rebuilt
        runs[label] = out
    cmp_out = tmp_path / "cmp"
    code = compare.main(
        ["--run", f"w14={runs['w14']}", "--run", f"w30={runs['w30']}", "--out", str(cmp_out)]
    )
    assert code == 0
    result = json.loads((cmp_out / "comparison.json").read_text())
    labels = {r["run"] for r in result["overall"]}
    assert {"w14", "w30", "device_average", "global_correction"} <= labels
    cohorts = {r["cohort"] for r in result["by_cohort"]}
    assert cohorts and cohorts <= {"paired", "apple_watch_only", "fitbit_only"}
    assert result["runs"]["w30"]["config"]["window"] == 30
    text = (cmp_out / "COMPARISON.md").read_text()
    assert "transformer: w14 vs w30" in text and "| w30 | sleep" in text
