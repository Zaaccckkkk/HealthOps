"""The deployment plan retains IAM and does not embed DB credentials."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "src/deployment/cloud-run/deploy.py"
spec = importlib.util.spec_from_file_location("cloud_run_deploy", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_cloud_run_command_preserves_security_and_resource_limits():
    config = json.loads((PATH.parent / "config.example.json").read_text())
    cmd = module.command(config)
    assert "--no-allow-unauthenticated" in cmd
    assert "--max=1" in cmd and "--min=0" in cmd
    assert "--port=8000" in cmd
    assert "--vpc-egress=private-ranges-only" in cmd
    assert any(x.startswith("--set-secrets=HEALTHOPS_DATABASE_URL=") for x in cmd)
    assert not any("postgresql://" in x or "postgresql+psycopg://" in x for x in cmd)


@pytest.mark.parametrize(
    "change",
    [
        {"model_url": "http://public.example:11434"},
        {"database_secret_version": "latest"},
        {"service_account": ""},
    ],
)
def test_reject_unpinned_or_unsafe_deployment_config(change):
    config = json.loads((PATH.parent / "config.example.json").read_text()) | change
    with pytest.raises(ValueError):
        module.command(config)
