"""Tiny synthetic stand-ins for the canonical and Synthea inputs, shared by the tests."""

import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from synthesizer.schema import UNITS, validate, write_parquet

CONFIGS = Path(__file__).resolve().parents[3] / "configs"


def canonical_rows(provider, dataset, tz, records):
    frame = pd.DataFrame(records)
    return frame.assign(
        provider=provider,
        source_device="test",
        unit=frame.metric.map(UNITS),
        source_timezone=tz,
        provenance_dataset=dataset,
        provenance_version="raw-v1",
    )


def fake_fitbit(rng):
    tz, records = "America/Indiana/Indianapolis", []
    for user in range(4):
        for day in pd.date_range("2016-01-01", periods=45, tz=tz):
            start, end = day.tz_convert("UTC"), (day + pd.Timedelta(days=1)).tz_convert("UTC")
            common = {"user_id": f"u{user}", "provenance_source": "activity"}
            for metric, value, agg in (
                ("steps", float(rng.integers(3000, 15000)), "daily_total"),
                ("heart_rate", rng.normal(75, 5), "daily_summary"),
                ("exercise_duration", float(rng.integers(0, 90) * 60), "daily_total"),
            ):
                records.append(
                    {
                        **common,
                        "source_record_id": f"{metric}:{day.date()}",
                        "metric": metric,
                        "value": value,
                        "started_at": start,
                        "ended_at": end,
                        "aggregation": agg,
                    }
                )
            bed = (day + pd.Timedelta(hours=23)).tz_convert("UTC")
            records.append(
                {
                    **common,
                    "source_record_id": f"sleep:{day.date()}",
                    "metric": "sleep_duration",
                    "value": float(rng.integers(380, 480) * 60),
                    "started_at": bed,
                    "ended_at": bed + pd.Timedelta(hours=8),
                    "aggregation": "interval_total",
                }
            )
    return canonical_rows("google_health", "nethealth", tz, records)


def fake_apple_watch(rng):
    records = []
    for user in range(3):
        for night in range(3):
            start = pd.Timestamp("2022-01-01T04:00Z") + pd.Timedelta(days=night)
            source = f"S{user}/{night}/hr.csv"
            for k in range(600):
                t = start + pd.Timedelta(seconds=5 * k)
                records.append(
                    {
                        "user_id": f"a{user}",
                        "provenance_source": source,
                        "metric": "heart_rate",
                        "source_record_id": f"hr:{source}:{k}",
                        "value": rng.normal(60, 3),
                        "started_at": t,
                        "ended_at": t,
                        "aggregation": "sample",
                    }
                )
            records.append(
                {
                    "user_id": f"a{user}",
                    "provenance_source": source,
                    "metric": "sleep_duration",
                    "source_record_id": f"sleep:{source}",
                    "value": 7 * 3600.0,
                    "started_at": start,
                    "ended_at": start + pd.Timedelta(hours=8),
                    "aggregation": "interval_total",
                }
            )
    return canonical_rows("healthkit", "bidsleep", "America/New_York", records)


def fake_synthea(path):
    path.mkdir(parents=True)
    pd.DataFrame(
        {
            "Id": ["p1", "p2", "p3"],
            "BIRTHDATE": ["1980-05-01", "1995-02-02", "1960-07-07"],
            "DEATHDATE": ["", "", ""],
            "GENDER": ["F", "M", "F"],
        }
    ).to_csv(path / "patients.csv", index=False)
    pd.DataFrame(
        {
            "START": ["2010-01-01"],
            "STOP": [""],
            "PATIENT": ["p3"],
            "DESCRIPTION": ["Essential hypertension (disorder)"],
        }
    ).to_csv(path / "conditions.csv", index=False)
    pd.DataFrame(
        {
            "START": ["2015-01-01T00:00:00Z"],
            "STOP": [""],
            "PATIENT": ["p3"],
            "DESCRIPTION": ["24 HR metoprolol succinate 100 MG Extended Release Oral Tablet"],
        }
    ).to_csv(path / "medications.csv", index=False)


def prepare_data_root(root: Path, n_users: int = 10, seed: int = 0) -> tuple[Path, Path]:
    """A DATA_ROOT with canonical-v0.0 + raw-v1 stand-ins and a copy of the real configs
    shrunk to `n_users` users of 30-31 days. Returns (data, configs)."""
    rng = np.random.default_rng(seed)
    data, configs = root / "data", root / "configs"
    shutil.copytree(CONFIGS, configs)
    baseline = yaml.safe_load((configs / "baseline.yaml").read_text())
    baseline["cohort"].update(n_users=n_users, days_min=30, days_max=31)
    (configs / "baseline.yaml").write_text(yaml.safe_dump(baseline))
    fitbit = yaml.safe_load((configs / "fitbit.yaml").read_text())
    fitbit["calibration"].update({"min_days_per_user": 30})
    (configs / "fitbit.yaml").write_text(yaml.safe_dump(fitbit))

    canonical = data / "processed/canonical/canonical-v0.0"
    write_parquet(validate(fake_fitbit(rng))[0], canonical / "nethealth.parquet")
    write_parquet(validate(fake_apple_watch(rng))[0], canonical / "bidsleep.parquet")
    fake_synthea(data / "raw/raw-v1/synthea/csv")
    (data / "manifests").mkdir(parents=True)
    (data / "manifests/raw-v1.yaml").write_text("version: raw-v1\n")
    (data / "manifests/canonical-v0.0.yaml").write_text(
        "version: canonical-v0.0\ninput: {version: raw-v1}\n"
    )
    return data, configs


def set_env(monkeypatch, data: Path, configs: Path, output_version: str) -> None:
    for key, value in {
        "DATA_ROOT": data,
        "CONFIG_DIR": configs,
        "INPUT_DATA_VERSION": "canonical-v0.0",
        "OUTPUT_DATA_VERSION": output_version,
    }.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("CONTEXT_LLM_BASE_URL", raising=False)


def edit_config(configs: Path, name: str, update) -> None:
    """Load configs/<name>, apply update(cfg) in place, write it back."""
    path = configs / name
    cfg = yaml.safe_load(path.read_text())
    update(cfg)
    path.write_text(yaml.safe_dump(cfg))
