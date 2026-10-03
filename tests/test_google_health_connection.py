"""Connection routes against PostgreSQL with a fake Google (OAuth + Health API)."""

import pytest
from fake_google_health import API, AUTHORIZE_URL, REVOKE_URL, TOKEN_URL, FakeGoogle
from fastapi.testclient import TestClient

from healthops.config import Settings
from healthops.connectors.crypto import TokenCipher
from healthops.main import create_app

pytestmark = pytest.mark.integration
NY = "America/New_York"
BASE = "/v1/connections/google-health"


@pytest.fixture
def fake():
    return FakeGoogle()


@pytest.fixture
def connected_client(migrated_database, fake):
    settings = Settings(
        database_url=migrated_database,
        environment="local",
        google_oauth_client_id="client",
        google_oauth_client_secret="secret",
        google_oauth_redirect_uri="https://example.test/cb",
        connection_key=TokenCipher.generate_key(),
        google_oauth_authorize_url=AUTHORIZE_URL,
        google_oauth_token_url=TOKEN_URL,
        google_oauth_revoke_url=REVOKE_URL,
        google_health_base_url=API,
    )
    with TestClient(
        create_app(settings, google_transport=fake.transport()), follow_redirects=False
    ) as c:
        yield c


def connect(client, headers, fake, code="good-code"):
    session = client.post(f"{BASE}/session", headers=headers, json={"timezone": NY})
    assert session.status_code == 200
    url = session.json()["authorization_url"]
    state = url.split("state=")[1].split("&")[0]
    callback = client.get(f"{BASE}/callback", params={"state": state, "code": code})
    assert callback.status_code == 303
    return callback.headers["location"], state


def test_connector_is_disabled_without_configuration(client, credentials):
    response = client.post(f"{BASE}/session", headers=credentials[0], json={"timezone": NY})
    assert response.status_code == 503


def test_full_connect_sync_and_disconnect_flow(connected_client, credentials, fake):
    alice, bob = credentials
    location, _ = connect(connected_client, alice, fake)
    assert location == "healthops://google-health?status=connected"
    status = connected_client.get(BASE, headers=alice).json()
    assert (
        status["connected"]
        and status["linked_account"]
        and status["last_session_outcome"] == "connected"
    )
    assert connected_client.get(BASE, headers=bob).json()["connected"] is False  # per user

    report = connected_client.post(
        f"{BASE}/sync", headers=alice, json={"timezone": NY, "days": 3}
    ).json()
    by_type = {t["data_type"]: t for t in report["types"]}
    assert by_type["steps"]["inserted"] == 2 and by_type["sleep"]["inserted"] == 2
    assert by_type["daily-heart-rate-variability"]["inserted"] == 1
    assert all(t["error"] is None for t in report["types"])

    # The records are ordinary observations under provider google_health, visible to the tools.
    availability = connected_client.get(
        "/v1/tools/availability",
        headers=alice,
        params={"start_date": "2026-09-25", "end_date": "2026-10-05", "timezone": NY},
    ).json()
    providers = {(s["provider"], s["metric"]) for s in availability["series"]}
    assert ("google_health", "steps") in providers and ("google_health", "hrv_rmssd") in providers
    assert all(s["provider"] == "google_health" for s in availability["series"])

    # Replay: nothing new, everything duplicate, no conflicts.
    again = connected_client.post(
        f"{BASE}/sync", headers=alice, json={"timezone": NY, "days": 3}
    ).json()
    steps = next(t for t in again["types"] if t["data_type"] == "steps")
    assert steps["inserted"] == 0 and steps["duplicates"] == 2 and steps["conflicts"] == []

    # Upstream revision: the stored value is kept and the identity is reported as a conflict.
    fake.data["steps"][0]["steps"]["count"] = "65"
    revised = connected_client.post(
        f"{BASE}/sync", headers=alice, json={"timezone": NY, "days": 3}
    ).json()
    steps = next(t for t in revised["types"] if t["data_type"] == "steps")
    assert steps["conflicts"] == [
        "steps:2026-10-01T13:00:00+00:00:2026-10-01T13:01:00+00:00:FITBIT"
    ]
    assert steps["inserted"] == 0
    resting = next(t for t in revised["types"] if t["data_type"] == "daily-resting-heart-rate")
    assert resting["skipped_other_platform"] == 1
    summary = connected_client.get(
        "/v1/tools/daily-summary",
        headers=alice,
        params={
            "metric": "steps",
            "start_date": "2026-10-01",
            "end_date": "2026-10-01",
            "timezone": NY,
        },
    ).json()
    assert summary["providers"][0]["days"][0]["interval_total_sum"] == "64.000000"

    # Disconnect revokes at the provider and removes the credential; data stays until asked.
    result = connected_client.delete(BASE, headers=alice).json()
    assert result == {"disconnected": True, "revoked_at_provider": True} and fake.revoked == [
        "refresh-1"
    ]
    assert connected_client.get(BASE, headers=alice).json()["connected"] is False
    assert (
        connected_client.post(f"{BASE}/sync", headers=alice, json={"timezone": NY}).status_code
        == 404
    )
    deleted = connected_client.delete(f"{BASE}/data", headers=alice).json()
    assert deleted["deleted"] == 7  # 2 steps + 2 resting + 1 hrv + 2 sleep
    assert connected_client.delete(f"{BASE}/data", headers=alice).json()["deleted"] == 0


def test_state_is_single_use_and_unknown_state_stores_nothing(connected_client, credentials, fake):
    alice, _ = credentials
    _, state = connect(connected_client, alice, fake)
    replay = connected_client.get(f"{BASE}/callback", params={"state": state, "code": "good-code"})
    assert replay.headers["location"].endswith("status=invalid_state")
    unknown = connected_client.get(
        f"{BASE}/callback", params={"state": "x" * 20, "code": "good-code"}
    )
    assert unknown.headers["location"].endswith("status=invalid_state")


def test_denied_consent_and_unlinked_account_are_reported_not_stored(
    connected_client, credentials, fake
):
    alice, _ = credentials
    session = connected_client.post(f"{BASE}/session", headers=alice, json={"timezone": NY}).json()
    state = session["authorization_url"].split("state=")[1].split("&")[0]
    denied = connected_client.get(
        f"{BASE}/callback", params={"state": state, "error": "access_denied"}
    )
    assert denied.headers["location"].endswith("status=denied")
    assert connected_client.get(BASE, headers=alice).json()["last_session_outcome"] == "denied"

    fake.account_linked = False
    location, _ = connect(connected_client, alice, fake)
    assert location.endswith("status=account_not_linked")
    assert fake.revoked == ["refresh-1"]  # the token obtained for an unlinked account is revoked
    assert connected_client.get(BASE, headers=alice).json()["connected"] is False


def test_bad_code_and_expired_refresh_token_map_to_clear_outcomes(
    connected_client, credentials, fake
):
    alice, _ = credentials
    location, _ = connect(connected_client, alice, fake, code="bad-code")
    assert location.endswith("status=token_exchange_failed")
    location, _ = connect(connected_client, alice, fake)
    fake.refresh_valid = False
    response = connected_client.post(f"{BASE}/sync", headers=alice, json={"timezone": NY})
    assert response.status_code == 409 and "reconnect" in response.json()["detail"]


def test_sync_limits_and_timezone_validation(connected_client, credentials, fake):
    alice, _ = credentials
    connect(connected_client, alice, fake)
    assert (
        connected_client.post(
            f"{BASE}/sync", headers=alice, json={"timezone": NY, "days": 400}
        ).status_code
        == 422
    )
    assert (
        connected_client.post(
            f"{BASE}/sync", headers=alice, json={"timezone": "Mars/Olympus"}
        ).status_code
        == 422
    )
