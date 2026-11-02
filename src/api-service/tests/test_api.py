from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from healthops.auth import hash_token
from healthops.models import ApiToken

pytestmark = pytest.mark.integration


def upload(client, headers, *records):
    return client.post("/v1/observations/batch", headers=headers, json={"observations": records})


def test_health_and_auth(client, credentials):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/v1/me", headers=credentials[0]).json()["display_name"] == "synthetic-alice"


def test_replay_and_history(client, credentials, record):
    alice = credentials[0]
    assert upload(client, alice, record).json() == {"received": 1, "inserted": 1, "duplicates": 0}
    assert upload(client, alice, record, record).json() == {
        "received": 2,
        "inserted": 0,
        "duplicates": 2,
    }
    items = client.get("/v1/observations", headers=alice).json()["items"]
    assert len(items) == 1
    assert items[0]["value"] == "72.000000"
    assert items[0]["started_at"].startswith("2026-10-01T12:00:00")
    assert "user_id" not in items[0] and "payload_hash" not in items[0]


def test_cross_user_isolation_and_delete(client, credentials, record):
    alice, bob = credentials
    assert upload(client, alice, record).status_code == 200
    row = client.get("/v1/observations", headers=alice).json()["items"][0]
    assert client.get("/v1/observations", headers=bob).json()["items"] == []
    assert client.delete(f"/v1/observations/{row['id']}", headers=bob).status_code == 404
    assert upload(client, bob, record).json()["inserted"] == 1
    assert client.delete(f"/v1/observations/{row['id']}", headers=alice).status_code == 204
    assert client.delete(f"/v1/observations/{uuid4()}", headers=alice).status_code == 404


def test_conflict_rolls_back_entire_batch(client, credentials, record):
    alice = credentials[0]
    upload(client, alice, record)
    # Sorted before the existing key so an INSERT occurs before the conflict.
    new = record | {"source_record_id": "000-new"}
    conflict = record | {"value": 99}
    assert upload(client, alice, new, conflict).status_code == 409
    assert len(client.get("/v1/observations", headers=alice).json()["items"]) == 1


def test_conflict_inside_new_batch_is_atomic(client, credentials, record):
    assert upload(client, credentials[0], record, record | {"value": 99}).status_code == 409
    assert client.get("/v1/observations", headers=credentials[0]).json()["items"] == []


def test_validation_redacts_private_input(client, credentials, record):
    secret_marker = "PRIVATE-INPUT-MUST-NOT-BE-ECHOED"
    response = upload(client, credentials[0], record | {"value": secret_marker})
    assert response.status_code == 422
    assert secret_marker not in response.text
    assert upload(client, credentials[0], record | {"user_id": str(uuid4())}).status_code == 422


def test_filters_and_pagination(client, credentials, record):
    alice = credentials[0]
    later = record | {
        "source_record_id": "later",
        "provider": "google_health",
        "started_at": "2026-10-02T12:00:00Z",
        "ended_at": "2026-10-02T12:00:00Z",
    }
    upload(client, alice, record, later)
    first = client.get("/v1/observations", headers=alice, params={"limit": 1}).json()
    assert len(first["items"]) == 1 and first["next_offset"] == 1
    second = client.get("/v1/observations", headers=alice, params={"limit": 1, "offset": 1}).json()
    assert second["next_offset"] is None
    response = client.get(
        "/v1/observations",
        headers=alice,
        params={"start": "2026-10-01T12:00:00Z", "end": "2026-10-02T12:00:00Z"},
    )
    assert len(response.json()["items"]) == 1
    assert (
        len(
            client.get(
                "/v1/observations", headers=alice, params={"provider": "google_health"}
            ).json()["items"]
        )
        == 1
    )
    assert (
        client.get("/v1/observations", headers=alice, params={"metric": "steps"}).json()["items"]
        == []
    )
    assert client.get("/v1/observations", headers=alice, params={"limit": 201}).status_code == 422
    assert (
        client.get(
            "/v1/observations", headers=alice, params={"start": "2026-10-01T12:00:00"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "change", [{"revoked": True}, {"expires_at": datetime.now(UTC) - timedelta(days=1)}]
)
def test_expired_and_revoked_tokens(client, credentials, engine, change):
    headers = credentials[0]
    digest = hash_token(headers["Authorization"].split()[1])
    with Session(engine) as session:
        session.execute(update(ApiToken).where(ApiToken.token_hash == digest).values(**change))
        session.commit()
    assert client.get("/v1/me", headers=headers).status_code == 401


def test_concurrent_replay(client, credentials, record):
    def send(_):
        return upload(client, credentials[0], record)

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(send, range(6)))
    assert all(r.status_code == 200 for r in responses)
    assert sum(r.json()["inserted"] for r in responses) == 1
    assert sum(r.json()["duplicates"] for r in responses) == 5


def test_concurrent_different_payload_same_identity(client, credentials, record):
    def send(value):
        return upload(client, credentials[0], record | {"value": value})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, [70, 80]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    items = client.get("/v1/observations", headers=credentials[0]).json()["items"]
    assert len(items) == 1
    assert items[0]["value"] in {"70.000000", "80.000000"}


def test_distinct_providers_and_metrics_do_not_collide(client, credentials, record):
    google = record | {"provider": "google_health"}
    sdnn = record | {"metric": "hrv_sdnn", "unit": "ms"}
    response = upload(client, credentials[0], record, google, sdnn)
    assert response.json() == {"received": 3, "inserted": 3, "duplicates": 0}


def test_batch_validation_is_atomic(client, credentials, record):
    response = upload(client, credentials[0], record, record | {"unit": "incorrect"})
    assert response.status_code == 422
    assert client.get("/v1/observations", headers=credentials[0]).json()["items"] == []


def test_schema_migration_matches_models(engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from healthops.models import Base

    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []


def test_cli_credentials_hash_and_permissions(engine, tmp_path):
    import json
    import stat

    from healthops.cli import create_user

    output = tmp_path / "credentials.json"
    with Session(engine) as session:
        token_id = create_user(session, "synthetic-cli-user", output)
    credentials = json.loads(output.read_text())
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with Session(engine) as session:
        token = session.scalar(select(ApiToken).where(ApiToken.id == token_id))
        assert token.token_hash == hash_token(credentials["token"])
        assert token.token_hash != credentials["token"]
        with pytest.raises(FileExistsError):
            create_user(session, "another-user", output)


def test_reissued_token_preserves_user_history(engine, client, tmp_path, record):
    import json
    from uuid import UUID, uuid4

    from healthops.cli import create_user, issue_token

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    with Session(engine) as session:
        create_user(session, "synthetic-renewal", first)
    original = json.loads(first.read_text())
    original_headers = {"Authorization": f"Bearer {original['token']}"}
    assert upload(client, original_headers, record).status_code == 200
    with Session(engine) as session:
        issue_token(session, UUID(original["user_id"]), second)
        with pytest.raises(ValueError, match="does not exist"):
            issue_token(session, uuid4(), tmp_path / "missing.json")
    renewed = json.loads(second.read_text())
    assert renewed["user_id"] == original["user_id"]
    assert renewed["token"] != original["token"]
    assert not (tmp_path / "missing.json").exists()
    headers = {"Authorization": f"Bearer {renewed['token']}"}
    assert len(client.get("/v1/observations", headers=headers).json()["items"]) == 1
    assert client.get("/v1/me", headers=original_headers).status_code == 200
