"""Regression grading contracts; scripted responses are not model accuracy evidence."""

from types import SimpleNamespace

from eval.pretraining_cases import cases, stage_checks
from eval.writer_chain_cases import ACCOUNTS
from healthops.assistant.toolbox import build_registry


def test_development_cases_have_valid_scopes_and_no_authored_evidence():
    rows = cases()
    assert len(rows) == len({r["id"] for r in rows}) == 30
    assert sum(len(r["questions"]) for r in rows) == 36
    assert {r["source_user_id"] for r in rows} == set(ACCOUNTS)
    for row in rows:
        assert row["split"] == "development"
        assert not {"evidence", "cards", "chosen", "reference_answer"} & row.keys()
        assert len(row["questions"]) == len(row["expected_routes"])
        assert bool(row["expected_queries"]) == (row["expected_routes"][-1] == "ready")
        for query in row["expected_queries"]:
            build_registry()[query["tool"]].params.model_validate(query["arguments"])


def test_clarification_must_be_resumable_without_data_or_writer_execution():
    response = SimpleNamespace(
        routing_status="needs_clarification",
        tool_calls=[],
        evidence=[],
        conversation_id="synthetic-id",
        conversation_version=1,
        clarification={"fields": ["time"]},
    )
    assert all(stage_checks(response, "needs_clarification", []).values())
    response.conversation_version = None
    assert not stage_checks(response, "needs_clarification", [])["resumable_clarification"]
    response.evidence = [{"synthetic": True}]
    assert not stage_checks(response, "needs_clarification", [])["no_data_execution"]


def test_ready_does_not_hide_incomplete_coverage_invalid_claims_or_fallback():
    response = SimpleNamespace(
        routing_status="ready",
        completion_status="incomplete",
        claims=[SimpleNamespace(valid=False)],
        narrative_status="fallback",
    )
    checks = stage_checks(response, "ready", [])
    assert checks["expected_route"]
    assert not checks["scope_complete"]
    assert not checks["claims_valid"]
    assert not checks["writer_generated"]


def test_unexpected_refusal_is_not_a_pass_for_a_supported_request():
    response = SimpleNamespace(
        routing_status="unsupported",
        completion_status="not_checked",
        claims=[],
        narrative_status="not_eligible",
    )
    assert not any(stage_checks(response, "ready", []).values())
