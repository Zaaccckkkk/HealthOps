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


def router_call(metric="heart_rate", phrase="2026-10-01", kind="explicit"):
    return ModelTurn(
        tool_calls=[
            ToolCallRequest(
                id="router",
                name="route_request",
                arguments_json=json.dumps(
                    {
                        "decision": "ready",
                        "task": "summary",
                        "metrics": [metric],
                        "time_phrase": phrase,
                        "baseline_phrase": "",
                        "language": "en",
                        "reason": "none",
                    }
                ),
            )
        ]
    )


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
        router_call(),
        ModelTurn(
            tool_calls=[
                summary_call(
                    metric="heart_rate", timezone=NY, start_date="2026-10-01", end_date="2026-10-01"
                )
            ]
        ),
        ModelTurn(text="On 2026-10-01 your heart rate sample mean was 72 bpm (healthkit)."),
        router_call(),
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
            json={"question": "How was my heart rate on 2026-10-01?", "timezone": NY},
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
            json={"question": "How was my heart rate on 2026-10-01?", "timezone": NY},
        ).json()
        assert theirs["status"] == "insufficient_data"
        assert theirs["evidence"][0]["result"]["record_count"] == 0


def test_confirmed_scope_blocks_widened_tool_dates_before_execution(migrated_database, credentials):
    turns = [
        router_call("steps", "yesterday", "yesterday"),
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
            json={"question": "Steps yesterday", "timezone": NY},
        ).json()
        assert body["tool_calls"][0]["status"] == "rejected"
        assert "Dates differ" in body["tool_calls"][0]["error"]
        assert body["tool_calls"][0]["warnings"] == ["scope_guard_rejected"]
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


def test_clarification_http_resume_and_owner_scope(migrated_database, credentials):
    from test_assistant_routing import decision

    alice, bob = credentials
    first_model = [decision("needs_clarification", metrics=["steps"])]
    with scripted_client(migrated_database, first_model) as client:
        pending = client.post(
            "/v1/assistant/ask", headers=alice, json={"question": "Steps recently?", "timezone": NY}
        ).json()
    assert pending["status"] == "needs_clarification"
    assert pending["tool_calls"] == []
    reply = {
        "question": "yesterday",
        "timezone": NY,
        "conversation_id": pending["conversation_id"],
        "conversation_version": pending["conversation_version"],
    }
    # New application instance, same PostgreSQL store.
    turns = [router_call("steps", "yesterday", "yesterday")]
    turns.extend(
        [
            ModelTurn(
                tool_calls=[
                    summary_call(
                        metric="steps",
                        start_date=pending["time_semantics"]["yesterday"],
                        end_date=pending["time_semantics"]["yesterday"],
                    )
                ]
            ),
            ModelTurn(
                tool_calls=[
                    ToolCallRequest(
                        id="finish",
                        name="state_findings",
                        arguments_json=json.dumps(
                            {
                                "findings": [
                                    "missing_data e1 " + pending["time_semantics"]["yesterday"]
                                ]
                            }
                        ),
                    )
                ]
            ),
        ]
    )
    with scripted_client(migrated_database, turns) as client:
        assert client.post("/v1/assistant/ask", headers=bob, json=reply).status_code == 409
        result = client.post("/v1/assistant/ask", headers=alice, json=reply)
        assert result.status_code == 200
        assert result.json()["explanation_status"] == "verified"
        assert result.json()["request_spec"]["metrics"] == ["steps"]
        assert result.json()["conversation_id"] is None
        assert client.post("/v1/assistant/ask", headers=alice, json=reply).status_code == 409


def test_http_verified_repair_preserves_scope_and_empty_data(migrated_database, credentials):
    turns = [
        router_call("steps"),
        ModelTurn(
            tool_calls=[
                summary_call(metric="steps", start_date="2026-10-01", end_date="2026-10-01")
            ]
        ),
    ]
    for evidence_id in ("e1.healthkit", "e1"):
        turns.append(
            ModelTurn(
                tool_calls=[
                    ToolCallRequest(
                        id="finish-" + evidence_id,
                        name="state_findings",
                        arguments_json=json.dumps({"findings": ["missing_data " + evidence_id]}),
                    )
                ]
            )
        )
    with scripted_client(migrated_database, turns) as client:
        response = client.post(
            "/v1/assistant/ask",
            headers=credentials[0],
            json={"question": "My steps on 2026-10-01", "timezone": NY},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["completion_status"] == "complete"
        assert result["explanation_status"] == "verified"
        assert result["coverage"][0]["query_status"] == "no_records"
        assert len(result["verification_attempts"]) == 2
        assert result["verification_attempts"][0]["invalid_reasons"] == ["unknown evidence id"]
        assert [r["name"] for r in result["tool_calls"]].count("daily_summary") == 1
