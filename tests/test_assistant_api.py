"""Assistant endpoint against real PostgreSQL with a scripted model: real tools, real identity.

The scripted model fixes which tool is called; the tool runs for real in a worker thread with
its own short-lived session, and the response carries real evidence. Model quality is not
measured here.
"""

import json

import pytest
from fastapi.testclient import TestClient

from healthops.assistant.model import ModelTurn, ScriptedChatModel, ToolCallRequest
from healthops.config import Settings
from healthops.main import create_app

pytestmark = pytest.mark.integration

NY = "America/New_York"


def scripted_client(migrated_database, turns, **overrides):
    settings = Settings(database_url=migrated_database, environment="local", **overrides)
    return TestClient(create_app(settings, chat_model=ScriptedChatModel(turns)))


def summary_call(**arguments):
    return ToolCallRequest(id="c1", name="daily_summary", arguments_json=json.dumps(arguments))


def test_assistant_is_disabled_without_a_model_endpoint(client, credentials):
    response = client.post(
        "/v1/assistant/ask", headers=credentials[0], json={"question": "hi", "timezone": NY}
    )
    assert response.status_code == 503 and "not configured" in response.json()["detail"]


def test_assistant_runs_real_tools_under_the_token_identity(migrated_database, credentials, record):
    alice, bob = credentials
    turns = [
        ModelTurn(
            tool_calls=[
                summary_call(
                    metric="heart_rate", timezone=NY, start_date="2026-10-01", end_date="2026-10-01"
                )
            ]
        ),
        ModelTurn(text="On 2026-10-01 your heart rate sample mean was 72 bpm (healthkit)."),
        ModelTurn(
            tool_calls=[
                summary_call(
                    metric="heart_rate", timezone=NY, start_date="2026-10-01", end_date="2026-10-01"
                )
            ]
        ),
        ModelTurn(text="There are no heart rate records on 2026-10-01."),
    ]
    with scripted_client(migrated_database, turns) as client:
        assert (
            client.post(
                "/v1/observations/batch", headers=alice, json={"observations": [record]}
            ).status_code
            == 200
        )
        assert (
            client.post("/v1/assistant/ask", json={"question": "x", "timezone": NY}).status_code
            == 401
        )
        mine = client.post(
            "/v1/assistant/ask",
            headers=alice,
            json={"question": "How was my heart rate?", "timezone": NY},
        )
        assert mine.status_code == 200, mine.text
        body = mine.json()
        assert body["status"] == "answered"
        assert body["explanation_status"] == "unverified"  # prose, not structured claims
        assert body["tool_calls"][0]["status"] == "ok"
        assert body["evidence"][0]["result"]["record_count"] == 1
        assert body["cards"][0]["rows"][0]["display"] == "72 bpm"
        assert body["model_text"].startswith("On 2026-10-01")
        assert body["time_semantics"]["timezone"] == NY
        # Same scripted call for Bob reads Bob's (empty) history: identity comes from the token.
        theirs = client.post(
            "/v1/assistant/ask",
            headers=bob,
            json={"question": "How was my heart rate?", "timezone": NY},
        ).json()
        assert theirs["status"] == "insufficient_data"
        assert theirs["evidence"][0]["result"]["record_count"] == 0


def test_tool_limits_still_apply_after_schema_validation(migrated_database, credentials):
    turns = [
        ModelTurn(
            tool_calls=[
                summary_call(
                    metric="steps", timezone=NY, start_date="2026-01-01", end_date="2026-12-31"
                )
            ]
        ),
        ModelTurn(text="I could not read that window."),
    ]
    with scripted_client(migrated_database, turns) as client:
        body = client.post(
            "/v1/assistant/ask",
            headers=credentials[0],
            json={"question": "all year", "timezone": NY},
        ).json()
        assert body["tool_calls"][0]["status"] == "rejected"
        assert "at most 93" in body["tool_calls"][0]["error"]
        assert body["evidence"] == []


def test_today_override_is_refused_outside_evaluation_mode(migrated_database, credentials):
    with scripted_client(migrated_database, [ModelTurn(text="x")]) as client:
        response = client.post(
            "/v1/assistant/ask",
            headers=credentials[0],
            json={"question": "x", "timezone": NY, "today": "2026-09-01"},
        )
        assert response.status_code == 422 and "evaluation mode" in response.json()["detail"]
    with scripted_client(
        migrated_database, [ModelTurn(text="x")], assistant_allow_today_override=True
    ) as client:
        body = client.post(
            "/v1/assistant/ask",
            headers=credentials[0],
            json={"question": "x", "timezone": NY, "today": "2026-09-01"},
        ).json()
        assert body["time_semantics"]["today"] == "2026-09-01"


def test_debug_page_is_served_locally_with_a_strict_csp(client):
    response = client.get("/debug/assistant")
    assert response.status_code == 200
    assert "local debug page" in response.text
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_assistant_rate_limit_is_per_user_and_returns_retry_after(migrated_database, credentials):
    alice, bob = credentials
    turns = [ModelTurn(text="no tool") for _ in range(3)]
    body = {"question": "hi", "timezone": NY}
    with scripted_client(migrated_database, turns, assistant_rate_limit_per_minute=1) as client:
        assert client.post("/v1/assistant/ask", headers=alice, json=body).status_code == 200
        limited = client.post("/v1/assistant/ask", headers=alice, json=body)
        assert limited.status_code == 429 and "Retry-After" in limited.headers
        # Another user still gets through; anonymous callers never reach the limiter.
        assert client.post("/v1/assistant/ask", headers=bob, json=body).status_code == 200
        assert client.post("/v1/assistant/ask", json=body).status_code in (401, 403)
