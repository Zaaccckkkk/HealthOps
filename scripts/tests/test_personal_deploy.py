"""A personal pilot cannot accidentally publish the synthetic service or reuse its identity."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

FOLDER = Path(__file__).resolve().parents[2] / "src/deployment/cloud-run"
sys.path.insert(0, str(FOLDER))
spec = importlib.util.spec_from_file_location(
    "personal_deploy", FOLDER / "deploy_personal.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def config():
    c = json.loads((FOLDER / "config.example.json").read_text())
    return c | {
        "service": "healthops-personal-api",
        "database_secret": "healthops-personal-database-url",
        "service_account": f"healthops-personal-run@{c['project']}.iam.gserviceaccount.com",
    }


def test_personal_pilot_retains_application_auth_and_disables_test_dates():
    command = module.command(config())
    assert "--allow-unauthenticated" in command
    assert "--max=1" in command
    assert any("HEALTHOPS_DATA_MODE=personal" in arg for arg in command)
    assert any(
        "HEALTHOPS_ASSISTANT_ALLOW_TODAY_OVERRIDE=false" in arg for arg in command
    )
    assert any(
        "HEALTHOPS_DATABASE_URL=healthops-personal-database-url:" in arg
        for arg in command
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("service", "healthops-ms3-api"),
        ("database_secret", "healthops-ms3-database-url"),
        ("service_account", "shared@example.test"),
    ],
)
def test_reject_shared_resources(key, value):
    with pytest.raises(ValueError):
        module.command(config() | {key: value})


def oauth_config():
    return config() | {
        "url": "https://healthops-personal-api-123.us-east4.run.app",
        "google_oauth": {
            "client_id": "123-fixture.apps.googleusercontent.com",
            "client_secret_version": "2",
            "connection_key_version": "1",
        },
    }


def test_google_connector_keeps_database_and_pins_both_secret_references():
    command = module.command(oauth_config())
    env = next(arg for arg in command if arg.startswith("--set-env-vars="))
    secrets = next(arg for arg in command if arg.startswith("--set-secrets="))
    assert "HEALTHOPS_DATA_MODE=personal" in env
    assert (
        "HEALTHOPS_GOOGLE_OAUTH_REDIRECT_URI=https://healthops-personal-api-123.us-east4.run.app/v1/connections/google-health/callback"
        in env
    )
    assert "HEALTHOPS_DATABASE_URL=healthops-personal-database-url:" in secrets
    assert (
        "HEALTHOPS_GOOGLE_OAUTH_CLIENT_SECRET=healthops-personal-google-client-secret:2"
        in secrets
    )
    assert "HEALTHOPS_CONNECTION_KEY=healthops-personal-connection-key:1" in secrets


@pytest.mark.parametrize("version", ["latest", "", "0", "1,INJECTED=value"])
def test_google_connector_rejects_unpinned_or_injected_secret_version(version):
    c = oauth_config()
    c["google_oauth"]["client_secret_version"] = version
    with pytest.raises(ValueError):
        module.command(c)


@pytest.mark.parametrize(
    "url",
    [
        "http://healthops-personal-api-123.run.app",
        "https://foreign.example",
        "https://healthops-personal-api-123.run.app?token=x",
        "https://healthops-personal-api-123.run.app/unexpected",
    ],
)
def test_google_connector_rejects_wrong_callback_origin(url):
    with pytest.raises(ValueError):
        module.command(oauth_config() | {"url": url})


def test_google_connector_rejects_raw_secret_in_configuration():
    c = oauth_config()
    c["google_oauth"]["client_secret"] = "not-a-real-secret"
    with pytest.raises(ValueError):
        module.command(c)


def test_narrative_is_opt_in_and_does_not_change_database_or_oauth_secrets():
    baseline = module.command(oauth_config())
    enabled = module.command(oauth_config() | {"narrative_enabled": True})
    assert not any("NARRATIVE_ENABLED" in arg for arg in baseline)
    assert any("HEALTHOPS_ASSISTANT_NARRATIVE_ENABLED=true" in arg for arg in enabled)
    env = next(arg for arg in enabled if arg.startswith("--set-env-vars="))
    assert env.count("HEALTHOPS_ASSISTANT_NARRATIVE_ENABLED=true") == 1
    assert [a for a in baseline if a.startswith("--set-secrets=")] == [
        a for a in enabled if a.startswith("--set-secrets=")
    ]
    with pytest.raises(ValueError):
        module.command(oauth_config() | {"narrative_enabled": "false"})
