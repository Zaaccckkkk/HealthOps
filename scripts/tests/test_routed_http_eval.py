"""HTTP evaluation preserves authentication and resumable request identity."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from healthops.assistant.contracts import AskRequest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/api-service/scripts"))
sys.path.insert(0, str(ROOT / "src/deployment/cloud-run"))
spec = importlib.util.spec_from_file_location(
    "routed_http", ROOT / "src/deployment/cloud-run/evaluate_routed_http.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_continuation_and_two_auth_headers_reach_http(monkeypatch):
    uid, cid = uuid4(), uuid4()
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"fixture": True})

    monkeypatch.setattr(module.AskResponse, "model_validate", lambda x: x)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assistant = module.HTTPAssistant(
                client,
                "https://fixture.run.app",
                {
                    uid: {
                        "Authorization": "Bearer synthetic-fixture",
                        "X-Serverless-Authorization": "Bearer iam-fixture",
                    }
                },
            )
            return await assistant.ask(
                AskRequest(
                    question="Past 7 days",
                    timezone="America/New_York",
                    today="2026-08-10",
                    conversation_id=cid,
                    conversation_version=2,
                ),
                uid,
            )

    assert asyncio.run(scenario()) == {"fixture": True}
    body = json.loads(seen[0].content)
    assert body["conversation_id"] == str(cid) and body["conversation_version"] == 2
    assert seen[0].headers["authorization"] == "Bearer synthetic-fixture"
    assert seen[0].headers["x-serverless-authorization"] == "Bearer iam-fixture"


def test_timeout_is_not_replayed():
    requests = []

    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("synthetic timeout")

    async def scenario():
        uid = uuid4()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            assistant = module.HTTPAssistant(client, "https://fixture.run.app", {uid: {}})
            await assistant.ask(AskRequest(question="Steps yesterday", timezone="UTC"), uid)

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(scenario())
    assert len(requests) == 1


def test_foreign_credential_identity_is_rejected(tmp_path):
    uid = uuid4()
    (tmp_path / "report.json").write_text(
        json.dumps(
            {
                "users": [
                    {
                        "user_id": "synthetic-00000",
                        "api_user_id": str(uuid4()),
                        "credentials_file": "never-read.json",
                    }
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="identity"):
        module.credential_headers(tmp_path, {"synthetic-00000": uid}, "fixture")


def test_cloud_inspection_uses_operator_project_and_region(monkeypatch):
    from types import SimpleNamespace

    commands = []

    def capture(command, **kwargs):
        commands.append(command)
        return '{"status": {}}'

    monkeypatch.setattr(module, "command_output", capture)
    args = SimpleNamespace(project="portfolio-fixture", region="us-west1")
    assert module.describe_cloud_run(args, "revisions", "eval-fixture") == {"status": {}}
    assert commands == [
        [
            "gcloud",
            "run",
            "revisions",
            "describe",
            "eval-fixture",
            "--region=us-west1",
            "--project=portfolio-fixture",
            "--format=json",
        ]
    ]
