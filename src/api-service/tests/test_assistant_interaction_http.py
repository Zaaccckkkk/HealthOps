"""Real HTTP/auth/PostgreSQL lifecycle, scripted model; no accuracy claim."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_assistant_routing import decision

from healthops.assistant.model import ScriptedChatModel
from healthops.config import Settings
from healthops.main import create_app
from healthops.models import AssistantConversationState


def body(question, pending=None, **extra):
    result = {"question": question, "timezone": "UTC"} | extra
    if pending:
        result |= {k: pending[k] for k in ("conversation_id", "conversation_version")}
    return result


def test_http_partial_resume_owner_isolation_stale_and_cancel(migrated_database, credentials):
    model = ScriptedChatModel(
        [
            decision("needs_clarification"),
            decision("needs_clarification", window={"phrase": "past 7 days"}),
        ]
    )
    settings = Settings(database_url=migrated_database, environment="local")
    with TestClient(create_app(settings, chat_model=model)) as client:
        first = client.post(
            "/v1/assistant/ask", headers=credentials[0], json=body("How am I doing lately?")
        )
        assert first.status_code == 200
        pending = first.json()
        assert [f["id"] for f in pending["clarification"]["fields"]] == ["metrics", "time_range"]
        assert not pending["tool_calls"]
        reply = body("past 7 days", pending)
        assert (
            client.post("/v1/assistant/ask", headers=credentials[1], json=reply).status_code == 409
        )
        second = client.post("/v1/assistant/ask", headers=credentials[0], json=reply)
        assert second.status_code == 200
        updated = second.json()
        assert [f["id"] for f in updated["clarification"]["fields"]] == ["metrics"]
        assert updated["conversation_expires_at"] == pending["conversation_expires_at"]
        assert (
            client.post("/v1/assistant/ask", headers=credentials[0], json=reply).status_code == 409
        )
        cancelled = client.post(
            "/v1/assistant/ask", headers=credentials[0], json=body("Cancel", updated, cancel=True)
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["routing_status"] == "cancelled"
        assert cancelled.json()["clarification"] is None
        assert (
            client.post(
                "/v1/assistant/ask", headers=credentials[0], json=body("Steps", updated)
            ).status_code
            == 409
        )
        assert len(model.requests) == 2


def test_http_expired_checkpoint_never_reaches_model(migrated_database, credentials, engine):
    model = ScriptedChatModel([decision("needs_clarification", metrics=["steps"])])
    with TestClient(
        create_app(Settings(database_url=migrated_database, environment="local"), chat_model=model)
    ) as client:
        pending = client.post(
            "/v1/assistant/ask", headers=credentials[0], json=body("Steps lately?")
        ).json()
        with Session(engine) as session:
            row = session.get(AssistantConversationState, UUID(pending["conversation_id"]))
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        response = client.post(
            "/v1/assistant/ask", headers=credentials[0], json=body("past 7 days", pending)
        )
        assert response.status_code == 409
        assert len(model.requests) == 1


def test_http_recreated_app_resumes_and_returns_verified_missing_records(
    migrated_database, credentials
):
    from test_assistant import call

    from healthops.assistant.model import ModelTurn

    settings = Settings(
        database_url=migrated_database, environment="local", assistant_allow_today_override=True
    )
    with TestClient(
        create_app(
            settings,
            chat_model=ScriptedChatModel([decision("needs_clarification", metrics=["steps"])]),
        )
    ) as first:
        pending = first.post(
            "/v1/assistant/ask",
            headers=credentials[0],
            json=body("Steps lately?", today="2026-08-10"),
        ).json()
    model = ScriptedChatModel(
        [
            decision(metrics=["steps"], window={"phrase": "yesterday"}),
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="steps",
                        start_date="2026-08-09",
                        end_date="2026-08-09",
                    )
                ]
            ),
            ModelTurn(tool_calls=[call("state_findings", findings=["missing_data e1"])]),
        ]
    )
    with TestClient(create_app(settings, chat_model=model)) as second:
        response = second.post(
            "/v1/assistant/ask", headers=credentials[0], json=body("yesterday", pending)
        )
        assert response.status_code == 200
        data = response.json()
        assert data["routing_status"] == "ready"
        assert data["status"] == "insufficient_data"
        assert data["explanation_status"] == "verified"
        assert data["completion_status"] == "complete"
        assert data["conversation_id"] is None and data["clarification"] is None
        assert data["coverage"][0]["query_status"] == "no_records"
        assert all(c["valid"] for c in data["claims"])
