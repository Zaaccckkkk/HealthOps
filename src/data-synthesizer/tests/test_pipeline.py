"""End-to-end run on tiny synthetic stand-ins for the canonical and Synthea inputs (no network)."""

import pandas as pd
import yaml
from fakes import prepare_data_root, set_env

from synthesizer import cli
from synthesizer.schema import validate


def test_pipeline_writes_valid_versioned_outputs(tmp_path, monkeypatch):
    data, configs = prepare_data_root(tmp_path)
    set_env(monkeypatch, data, configs, "synthetic-v0.0")
    assert cli.main() == 0

    out = data / "synthetic/synthetic-v0.0"
    users = pd.read_parquet(out / "users.parquet")
    assert len(users) == 10 and set(users.context_source) == {"seed_library"}
    for provider in ("healthkit", "google_health"):
        rows = pd.read_parquet(out / f"observations_{provider}.parquet")
        assert set(rows.provider) == {provider}
        assert len(validate(rows)[0]) == len(rows)  # nothing the API would reject
        owners = set(users[users.providers.str.contains(provider)].user_id)
        assert set(rows.user_id) <= owners
    bias = pd.read_parquet(out / "device_bias_daily.parquet")
    assert set(bias.metric) == {"sleep_duration", "steps", "resting_heart_rate"}
    assert set(bias.user_id) == set(users.user_id)
    manifest = yaml.safe_load((data / "manifests/synthetic-v0.0.yaml").read_text())
    assert manifest["input"]["version"] == "canonical-v0.0" and len(manifest["configs"]) == 6
    assert "mean_device_bias" in manifest["stats"]
    assert cli.main() == 0  # same configuration: the finished version is left untouched
