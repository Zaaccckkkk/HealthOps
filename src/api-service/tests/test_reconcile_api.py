"""Real PostgreSQL, real auth and graph; scripted LLM/quantiles isolate wiring from accuracy."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_assistant import call
from test_reconcile import Predictor

from healthops.assistant.model import ModelTurn, ScriptedChatModel
from healthops.config import Settings
from healthops.main import create_app

pytestmark = pytest.mark.integration


def insert(client, headers, days=14, providers=("healthkit", "google_health")):
    observations = []
    for i in range(days):
        start = datetime(2026, 8, 20, tzinfo=UTC) - timedelta(days=i)
        for provider in providers:
            observations.append(
                dict(
                    provider=provider,
                    source_record_id=str(uuid4()),
                    source_device="synthetic-device",
                    metric="steps",
                    value=6000 if provider == "healthkit" else 10000,
                    unit="count",
                    started_at=start.isoformat(),
                    ended_at=(start + timedelta(days=1)).isoformat(),
                    source_timezone="UTC",
                    aggregation="daily_total",
                )
            )
    response = client.post(
        "/v1/observations/batch", headers=headers, json={"observations": observations}
    )
    assert response.status_code == 200, response.text


def configure(app, predictor):
    from healthops.assistant.toolbox import build_registry

    app.state.reconcile_predictor = predictor
    if app.state.assistant:
        app.state.assistant.toolbox.registry = build_registry(predictor=predictor)


def test_endpoint_owner_isolation_no_reading_and_validation(migrated_database, credentials):
    app = create_app(Settings(database_url=migrated_database))
    predictor = Predictor()
    configure(app, predictor)
    query = {"metric": "steps", "date": "2026-08-20", "timezone": "UTC"}
    with TestClient(app) as client:
        insert(client, credentials[0], days=3)
        assert client.get("/v1/tools/reconcile", params=query).status_code == 401
        own = client.get("/v1/tools/reconcile", params=query, headers=credentials[0])
        assert own.status_code == 200, own.text
        data = own.json()
        assert data["history_days"] == 3 and data["attribution"] == "google_health"
        assert "history_short" in data["warnings"]
        other = client.get("/v1/tools/reconcile", params=query, headers=credentials[1]).json()
        assert other["q50"] is None and other["window_days_with_any"] == 0
        assert len(predictor.calls) == 1  # another user never enters the model with Alice's data
        for bad in ({"metric": "hrv_sdnn"}, {"timezone": "bad/zone"}, {"date": "2000-01-01"}):
            assert (
                client.get(
                    "/v1/tools/reconcile", params=query | bad, headers=credentials[0]
                ).status_code
                == 422
            )
        empty = client.get(
            "/v1/tools/reconcile", params=query | {"date": "2026-08-21"}, headers=credentials[0]
        ).json()
        assert empty["q50"] is None and len(predictor.calls) == 1


@pytest.mark.parametrize(
    "days,providers,first_claim",
    [
        (3, ("healthkit", "google_health"), "value_on_date e1 2026-08-20"),
        (14, ("healthkit",), "device_attribution e1"),
    ],
)
def test_routed_graph_clarifies_resumes_and_repairs_attribution(
    migrated_database, credentials, days, providers, first_claim
):
    from test_assistant_routing import decision

    model = ScriptedChatModel(
        [
            decision("needs_clarification", intent="device_reconciliation"),
            decision(
                intent="device_reconciliation", metrics=["steps"], window={"phrase": "2026-08-20"}
            ),
            ModelTurn(tool_calls=[call("reconcile_devices", metric="steps", date="2026-08-20")]),
            ModelTurn(tool_calls=[call("state_findings", findings=[first_claim])]),
            ModelTurn(
                tool_calls=[call("state_findings", findings=["value_on_date e1 2026-08-20"])]
            ),
        ]
    )
    app = create_app(
        Settings(database_url=migrated_database, assistant_allow_today_override=True),
        chat_model=model,
    )
    configure(app, Predictor())
    with TestClient(app) as client:
        insert(client, credentials[0], days, providers)
        first = client.post(
            "/v1/assistant/ask",
            headers=credentials[0],
            json={"question": "Which device is closer?", "timezone": "UTC", "today": "2026-08-21"},
        )
        assert first.status_code == 200, first.text
        pending = first.json()
        assert pending["status"] == "needs_clarification" and not pending["tool_calls"]
        second = client.post(
            "/v1/assistant/ask",
            headers=credentials[0],
            json={
                "question": "steps on 2026-08-20",
                "timezone": "UTC",
                "conversation_id": pending["conversation_id"],
                "conversation_version": pending["conversation_version"],
            },
        )
        assert second.status_code == 200, second.text
        result = second.json()
        assert result["status"] == "answered", result
        assert result["explanation_status"] == "verified"
        assert "nominal 90% prediction interval" in result["answer"]
        if days == 3:
            assert "Data may not be sufficient; the result may not be reliable." in result["answer"]
        else:
            assert len(result["verification_attempts"]) == 2
            assert result["cards"][0]["reconciliation"]["attribution"] is None
            assert "no device attribution" in result["answer"]


def test_disabled_model_returns_unavailable_not_fake_data(client, credentials):
    response = client.get(
        "/v1/tools/reconcile",
        headers=credentials[0],
        params={"metric": "steps", "date": "2026-08-20", "timezone": "UTC"},
    )
    assert response.status_code == 503


def test_month_reconciliation_refusal_explains_supported_single_day(migrated_database, credentials):
    from test_assistant_routing import decision

    model = ScriptedChatModel(
        [
            decision(
                intent="device_reconciliation", metrics=["steps"], window={"phrase": "past 30 days"}
            )
        ]
    )
    app = create_app(
        Settings(database_url=migrated_database, assistant_allow_today_override=True),
        chat_model=model,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/assistant/ask",
            headers=credentials[0],
            json={
                "question": "Reconcile steps past 30 days",
                "timezone": "UTC",
                "today": "2026-08-20",
            },
        ).json()
    assert response["status"] == "refused"
    assert "one date" in response["answer"]
    assert not response["tool_calls"]
