"""Cloud import refuses accidental personal-database or untrusted API targets."""

import importlib.util
import io
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "src/deployment/cloud-run/seed_synthetic.py"
spec = importlib.util.spec_from_file_location("cloud_seed", PATH)
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)

URL = "postgresql+psycopg://operator:fixture@127.0.0.1:15433/healthops_synthetic?sslmode=require"
API = "https://synthetic-fixture.us-east4.run.app"


def test_explicit_synthetic_tunnel_and_cloud_run_target():
    seed.validate_target(URL, API)


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("healthops_synthetic", "healthops"),
        URL.replace("127.0.0.1", "10.150.0.2"),
        URL.replace("15433", "5432"),
        URL.replace("require", "disable"),
    ],
)
def test_reject_wrong_database_or_tunnel(url):
    with pytest.raises(ValueError):
        seed.validate_target(url, API)


@pytest.mark.parametrize(
    "url",
    [
        "http://fixture.run.app",
        "https://fixture.example",
        API + "/redirect",
        API + "?token=x",
    ],
)
def test_reject_untrusted_api(url):
    with pytest.raises(ValueError):
        seed.validate_target(URL, url)


def test_upload_batches_by_account_and_preserves_two_auth_layers(tmp_path, monkeypatch):
    calls = []
    users = []
    for uid in ("synthetic-a", "synthetic-b"):
        filename = uid + ".json"
        (tmp_path / filename).write_text(json.dumps({"token": uid + "-fixture"}))
        users.append({"user_id": uid, "credentials_file": filename})
    report = {"users": users, "inserted": 0, "duplicates": 0}
    spool = io.StringIO(
        "".join(
            json.dumps({"user_id": uid, "observation": {"source_record_id": str(i)}}) + "\n"
            for uid, count in (("synthetic-a", 501), ("synthetic-b", 1))
            for i in range(count)
        )
    )

    class Response:
        def __init__(self, count):
            self.count = count

        def raise_for_status(self):
            pass

        def json(self):
            return {"inserted": self.count, "duplicates": 0}

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, headers, json):
            calls.append((url, headers, list(json["observations"])))
            return Response(len(json["observations"]))

    monkeypatch.setattr(seed.httpx, "Client", Client)
    monkeypatch.setattr(seed.subprocess, "check_output", lambda *a, **k: "fixture-identity")
    seed.upload(report, spool, tmp_path, API)
    assert [len(c[2]) for c in calls] == [500, 1, 1]
    assert report["inserted"] == 502
    assert calls[0][1]["Authorization"] == "Bearer synthetic-a-fixture"
    assert calls[2][1]["Authorization"] == "Bearer synthetic-b-fixture"
    assert all(c[1]["X-Serverless-Authorization"] == "Bearer fixture-identity" for c in calls)
    assert all("user_id" not in row for c in calls for row in c[2])
