"""Synthetic parquet -> shared ingestion -> authenticated API, with no real datasets."""

import io
import json
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
yaml = pytest.importorskip("yaml")

from healthops.models import Observation, User  # noqa: E402
from healthops.repository import SourceConflict  # noqa: E402
from healthops.synthetic_import import (  # noqa: E402
    FILES,
    ImportFailure,
    apply_import,
    digest,
    local_database,
    main,
    prepare,
)


@pytest.fixture
def cohort(tmp_path, record):
    version = f"synthetic-test-{uuid4().hex[:8]}"
    root = tmp_path / version
    root.mkdir()
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"user_id": "apple", "cohort": "apple_watch_only"},
                {"user_id": "fitbit", "cohort": "fitbit_only"},
                {"user_id": "paired", "cohort": "paired"},
            ]
        ),
        root / "users.parquet",
    )
    base = record | {"provenance_dataset": "synthetic", "provenance_version": version}
    # Same source identity is valid across different accounts; values must not leak.
    pq.write_table(
        pa.Table.from_pylist(
            [
                base | {"user_id": "apple", "value": 60},
                base | {"user_id": "paired", "value": 90},
            ]
        ),
        root / "observations_healthkit.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                base | {"user_id": "fitbit", "provider": "google_health", "value": 65},
                base | {"user_id": "paired", "provider": "google_health", "value": 85},
            ]
        ),
        root / "observations_google_health.parquet",
    )
    manifest = tmp_path / f"{version}.yaml"

    def refresh():
        manifest.write_text(
            yaml.safe_dump(
                {
                    "version": version,
                    "stage": "data-synthesizer",
                    "files": [
                        {"path": n, "bytes": (root / n).stat().st_size, "sha256": digest(root / n)}
                        for n in FILES
                    ],
                }
            )
        )

    refresh()
    return root, manifest, refresh


def plan(cohort, **kwargs):
    spool = io.StringIO()
    report = prepare(
        cohort[0],
        cohort[1],
        spool,
        **({"user_ids": [], "per_cohort": 1, "max_records": 100} | kwargs),
    )
    return report, spool


def test_conversion_and_stable_mapping(cohort):
    report, spool = plan(cohort)
    assert report["records"] == 4 and len(report["users"]) == 3
    repeat, _ = plan(cohort)
    assert [u["api_user_id"] for u in report["users"]] == [
        u["api_user_id"] for u in repeat["users"]
    ]
    rows = [json.loads(line) for line in spool]
    assert {r["user_id"] for r in rows} == {"apple", "fitbit", "paired"}
    assert "provenance_dataset" not in rows[0]["observation"]
    assert "user_id" not in rows[0]["observation"]
    assert rows[0]["observation"]["started_at"].startswith("2026-10-01T12:00:00")


def test_dry_run_does_not_connect_or_issue_tokens(cohort, tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTHOPS_DATABASE_URL", "not-a-db")
    monkeypatch.setenv("HEALTHOPS_SYNTHETIC_DATABASE_URL", "not-a-db")
    out = tmp_path / "out"
    assert (
        main(["--dataset", str(cohort[0]), "--manifest", str(cohort[1]), "--output-dir", str(out)])
        == 0
    )
    assert [p.name for p in out.iterdir()] == ["dry-run.json"]


def test_checksum_failure(cohort):
    with (cohort[0] / "users.parquet").open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ImportFailure, match="checksum"):
        plan(cohort)


@pytest.mark.parametrize(
    "changes",
    [
        {"provenance_dataset": "nethealth"},
        {"provenance_version": "synthetic-other"},
        {"provider": "manual"},
        {"unit": "wrong"},
        {"value": -1},
    ],
)
def test_reject_invalid_selected_data(cohort, changes):
    path = cohort[0] / "observations_healthkit.parquet"
    rows = pq.read_table(path).to_pylist()
    rows[0].update(changes)
    pq.write_table(pa.Table.from_pylist(rows), path)
    cohort[2]()
    with pytest.raises(ImportFailure):
        plan(cohort)


def test_selection_limits_and_local_dates(cohort):
    with pytest.raises(ImportFailure, match="max-records"):
        plan(cohort, max_records=3)
    with pytest.raises(ImportFailure, match="Requested user"):
        plan(cohort, user_ids=["not-present"])
    path = cohort[0] / "observations_healthkit.parquet"
    rows = pq.read_table(path).to_pylist()
    rows[0].update(started_at="2026-10-01T03:00:00Z", ended_at="2026-10-01T03:00:00Z")
    pq.write_table(pa.Table.from_pylist(rows), path)
    cohort[2]()
    report, _ = plan(cohort, user_ids=["apple"], start=date(2026, 9, 30), end=date(2026, 9, 30))
    assert report["records"] == 1  # September 30 in New York, not October 1.
    with pytest.raises(ImportFailure, match="no observations"):
        plan(cohort, user_ids=["apple"], start=date(2026, 10, 1))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:password@cloud.example/healthops",
        "postgresql://user:password@localhost/personal",
        "postgresql://user:password@localhost/healthops?host=cloud.example",
        "sqlite:///healthops",
    ],
)
def test_remote_database_is_rejected(url):
    with pytest.raises(ImportFailure, match="limited to the local"):
        local_database(url)


@pytest.mark.integration
def test_import_replay_and_authenticated_tools(cohort, tmp_path, engine, client, credentials):
    report, spool = plan(cohort)
    out = tmp_path / "out"
    apply_import(engine, report, spool, out, batch_size=1)
    assert (report["inserted"], report["duplicates"], report["status"]) == (4, 0, "completed")
    users = {u["user_id"]: u for u in report["users"]}
    tokens = {}
    for uid, user in users.items():
        file = out / user["credentials_file"]
        assert file.stat().st_mode & 0o777 == 0o600
        tokens[uid] = json.loads(file.read_text())["token"]
        assert tokens[uid] not in (out / "report.json").read_text()

    def headers(uid):
        return {"Authorization": f"Bearer {tokens[uid]}"}

    params = {
        "start_date": "2026-10-01",
        "end_date": "2026-10-02",
        "timezone": "America/New_York",
        "metric": "heart_rate",
    }
    response = client.get("/v1/tools/daily-summary", headers=headers("apple"), params=params)
    assert response.status_code == 200
    result = response.json()
    assert result["record_count"] == 1
    assert result["providers"][0]["days"][0]["sample_mean"] == "60.000000"
    assert result["providers"][0]["days"][1]["sample_mean"] is None
    paired = client.get("/v1/tools/daily-summary", headers=headers("paired"), params=params).json()
    assert paired["record_count"] == 2 and len(paired["providers"]) == 2
    assert client.get("/v1/observations", headers=credentials[0]).json()["items"] == []
    original_tokens = tokens.copy()
    repeat, spool = plan(cohort)
    apply_import(engine, repeat, spool, out)
    assert (repeat["inserted"], repeat["duplicates"]) == (0, 4)
    for user in repeat["users"]:
        assert (
            json.loads((out / user["credentials_file"]).read_text())["token"]
            == original_tokens[user["user_id"]]
        )
    assert client.get("/v1/observations", headers=headers("apple")).json()["next_offset"] is None


@pytest.mark.integration
def test_identity_collision_never_reuses_existing_user(cohort, tmp_path, engine):
    report, spool = plan(cohort)
    with Session(engine) as session:
        session.add(
            User(id=UUID(report["users"][0]["api_user_id"]), display_name="Existing account")
        )
        session.commit()
    with pytest.raises(ImportFailure, match="identity collision"):
        apply_import(engine, report, spool, tmp_path / "out")
    assert report["status"] == "failed"


@pytest.mark.integration
def test_conflict_is_reported_and_never_overwrites(cohort, tmp_path, engine):
    report, spool = plan(cohort)
    out = tmp_path / "out"
    apply_import(engine, report, spool, out)
    # Emulate a conflicting payload at the shared ingestion boundary using only fixture data.
    repeat, spool = plan(cohort)
    rows = [json.loads(line) for line in spool]
    rows[0]["observation"]["value"] = "61"
    changed = io.StringIO("\n".join(json.dumps(r) for r in rows))
    with pytest.raises(SourceConflict):
        apply_import(engine, repeat, changed, out)
    saved = json.loads((out / "report.json").read_text())
    assert saved["status"] == "failed"
    with Session(engine) as session:
        value = session.scalar(
            select(Observation.value).where(
                Observation.user_id == UUID(report["users"][0]["api_user_id"])
            )
        )
    assert value == 60


@pytest.mark.integration
def test_resume_after_committed_batch(cohort, tmp_path, engine, monkeypatch):
    from healthops import synthetic_import

    report, spool = plan(cohort)
    out = tmp_path / "out"
    real_ingest = synthetic_import.ingest_batch
    calls = 0

    def interrupted(session, user_id, rows):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Simulated interruption")
        return real_ingest(session, user_id, rows)

    with monkeypatch.context() as patch:
        patch.setattr(synthetic_import, "ingest_batch", interrupted)
        with pytest.raises(RuntimeError, match="Simulated interruption"):
            apply_import(engine, report, spool, out, batch_size=1)
    saved = json.loads((out / "report.json").read_text())
    assert saved["status"] == "failed" and saved["inserted"] == 1
    repeat, spool = plan(cohort)
    apply_import(engine, repeat, spool, out, batch_size=1)
    assert (repeat["status"], repeat["inserted"], repeat["duplicates"]) == ("completed", 3, 1)
