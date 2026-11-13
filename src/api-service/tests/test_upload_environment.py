"""The phone's data declaration cannot accidentally cross a deployment boundary."""

import pytest
from fastapi.testclient import TestClient

from healthops.config import Settings
from healthops.main import create_app


@pytest.mark.parametrize(
    ("mode", "kind", "allowed"),
    [
        ("synthetic", "personal", False),
        ("synthetic", None, False),
        ("synthetic", "synthetic", True),
        ("personal", "synthetic", False),
        ("personal", None, False),
        ("personal", "personal", True),
        ("development", "personal", False),
        ("development", None, True),
    ],
)
def test_upload_environment_boundary(migrated_database, credentials, record, mode, kind, allowed):
    # All rows are invented fixtures in healthops_test, including the 'personal' mode test.
    settings = Settings(database_url=migrated_database, environment="local", data_mode=mode)
    headers = dict(credentials[0])
    if kind:
        headers["X-HealthOps-Data-Kind"] = kind
    with TestClient(create_app(settings)) as client:
        me = client.get("/v1/me", headers=headers)
        assert me.json()["data_mode"] == mode
        response = client.post(
            "/v1/observations/batch", headers=headers, json={"observations": [record]}
        )
        assert response.status_code == (200 if allowed else 403)
        history = client.get("/v1/observations", headers=headers).json()["items"]
        assert len(history) == (1 if allowed else 0)
        assert client.get("/v1/observations", headers=credentials[1]).json()["items"] == []


def test_environment_metadata_does_not_bypass_auth(client):
    assert client.get("/v1/me").status_code == 401
