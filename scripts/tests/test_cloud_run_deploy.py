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


def test_reconciliation_directory_is_image_bundled_and_optional():
    config = json.loads((PATH.parent / "config.example.json").read_text())
    cmd = module.command(config | {"reconcile_model_dir": "/app/models/reconcile"})
    assert any("HEALTHOPS_RECONCILE_MODEL_DIR=/app/models/reconcile" in item for item in cmd)
    with pytest.raises(ValueError):
        module.command(config | {"reconcile_model_dir": "gs://arbitrary-model"})


def test_synthetic_writer_can_be_enabled_without_changing_security_or_secrets():
    config = json.loads((PATH.parent / "config.example.json").read_text())
    baseline = module.command(config)
    enabled = module.command(config | {"narrative_enabled": True})
    assert not any("NARRATIVE_ENABLED" in arg for arg in baseline)
    env = next(arg for arg in enabled if arg.startswith("--set-env-vars="))
    assert env.count("HEALTHOPS_ASSISTANT_NARRATIVE_ENABLED=true") == 1
    assert "--no-allow-unauthenticated" in enabled
    assert [arg for arg in baseline if not arg.startswith("--set-env-vars=")] == [
        arg for arg in enabled if not arg.startswith("--set-env-vars=")
    ]
    with pytest.raises(ValueError):
        module.command(config | {"narrative_enabled": "false"})


def test_writer_model_override_does_not_replace_router():
    config = json.loads((PATH.parent / "config.example.json").read_text())
    cmd = module.command(config | {"narrative_enabled": True, "writer_model_name": "writer-step32"})
    env = next(arg for arg in cmd if arg.startswith("--set-env-vars="))
    assert "HEALTHOPS_ASSISTANT_MODEL_NAME=qwen3:8b" in env
    assert "HEALTHOPS_ASSISTANT_WRITER_MODEL_NAME=writer-step32" in env
    for name in ["", "bad,OTHER=value", "bad\nname"]:
        with pytest.raises(ValueError):
            module.command(config | {"narrative_enabled": True, "writer_model_name": name})
