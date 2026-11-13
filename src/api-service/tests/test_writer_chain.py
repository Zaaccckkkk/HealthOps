"""Synthetic full-chain capture boundaries; no remote inference required."""

import asyncio
import copy

import pytest

from eval.writer_chain_cases import (
    ACCOUNTS,
    EDGE_ACCOUNT,
    cases,
    edge_diagnostics,
    edge_observations,
)
from healthops.assistant.model import ModelError, ModelTurn
from healthops.assistant.toolbox import build_registry
from healthops.schemas import ObservationBatch
from scripts.eval_writer_chain import (
    CaptureModel,
    expected_results_present,
    guard_database,
    writer_calls,
)


def test_multimetric_cases_use_cohort_records_and_preserve_both_hrv_definitions():
    from eval.writer_multimetric_cases import cases as multimetric_cases

    rows = multimetric_cases()
    assert len(rows) == len({r["id"] for r in rows}) == 18
    assert sum(len(r["questions"]) for r in rows) == 22
    assert {r["source_user_id"] for r in rows} == set(ACCOUNTS)
    for row in rows:
        assert row["split"] == "development" and row["data_kind"] == "synthetic"
        assert not ({"cards", "evidence", "reference_answer"} & row.keys())
        assert len(row["expected_queries"]) >= 2
        for query in row["expected_queries"]:
            build_registry()[query["tool"]].params.model_validate(query["arguments"])
            assert query["arguments"]["provider"] == row["provider"]
    both = [r for r in rows if r["family"] == "rhr_both_hrv"]
    assert len(both) == 3
    for row in both:
        assert {q["arguments"]["metric"] for q in row["expected_queries"]} == {
            "resting_heart_rate",
            "hrv_sdnn",
            "hrv_rmssd",
        }
    assert multimetric_cases() == rows


def test_multimetric_audit_rejects_missing_metric_or_wrong_source():
    first = {
        "tool": "daily_summary",
        "result": {"metric": "resting_heart_rate", "provider": "healthkit"},
    }
    second = {"tool": "daily_summary", "result": {"metric": "hrv_sdnn", "provider": "healthkit"}}
    assert expected_results_present([first, second], [first, second])
    assert expected_results_present([first], first)  # Existing single-query traces.
    assert not expected_results_present([first], [first, second])
    wrong = copy.deepcopy(second)
    wrong["result"]["provider"] = "google_health"
    assert not expected_results_present([first, wrong], [first, second])
    assert not expected_results_present([first], [])


def test_cases_use_records_and_tool_expectations_not_handwritten_cards():
    rows = cases()
    assert len(rows) == len({r["id"] for r in rows}) == 50
    assert sum(len(r["questions"]) for r in rows) == 56
    assert {r["source_user_id"] for r in rows} == set(ACCOUNTS) | {EDGE_ACCOUNT}
    for row in rows:
        assert row["split"] == "development" and row["data_kind"] == "synthetic"
        assert not ({"cards", "evidence", "reference_answer"} & row.keys())
        build_registry()[row["expected_tool"]].params.model_validate(row["expected_arguments"])
    assert cases() == rows  # Freeze question generation before inference.


def test_edge_uploads_are_valid_observations_with_deliberate_overlap_and_mixed_bases():
    batch = ObservationBatch(observations=edge_observations())
    assert len(batch.observations) == 34
    assert len({r.source_record_id for r in batch.observations}) == 34
    assert {r.aggregation for r in batch.observations} == {"daily_total", "interval_total"}
    assert {r.provider for r in batch.observations} == {"healthkit", "google_health"}


def test_followup_probes_are_separate_and_preserve_record_scope():
    original = {r["id"]: r for r in cases()}
    probes = edge_diagnostics()
    assert len(probes) == 8
    assert not set(original) & {r["id"] for r in probes}
    for row in probes:
        parent = original[row["diagnostic_of"]]
        assert row["expected_arguments"] == parent["expected_arguments"]
        assert row["source_user_id"] == parent["source_user_id"]
        assert row["questions"] != parent["questions"]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://localhost/healthops",
        "postgresql+psycopg://private-cloud/healthops_test",
        "postgresql+psycopg://localhost/healthops_test?host=private-cloud",
        "sqlite:///healthops_test",
    ],
)
def test_nonisolated_database_is_rejected(url):
    with pytest.raises(ValueError, match="isolated"):
        guard_database(url)


def test_loopback_test_database_is_allowed():
    guard_database("postgresql+psycopg://localhost:15436/healthops_test")


def test_recorder_preserves_actual_prompt_raw_completion_and_tool_arguments():
    class Model:
        info = "synthetic-info"

        async def complete(self, messages, tools):
            messages.append({"role": "assistant", "content": "internal mutation"})
            return ModelTurn(text='{"answer":"synthetic output"}')

    messages = [{"role": "user", "content": "synthetic input"}]
    original = copy.deepcopy(messages)
    model = CaptureModel(Model())
    turn = asyncio.run(model.complete(messages, []))
    assert model.calls[0]["messages"] == original
    assert model.calls[0]["raw_text"] == turn.text
    assert model.info == "synthetic-info"
    assert writer_calls(model.calls) == []


def test_recorder_preserves_failed_call_without_fabricating_completion():
    class Model:
        async def complete(self, messages, tools):
            raise ModelError("synthetic failure")

    model = CaptureModel(Model())
    with pytest.raises(ModelError):
        asyncio.run(model.complete([{"role": "user", "content": "test"}], []))
    assert model.calls[0]["error_type"] == "ModelError"
    assert "raw_text" not in model.calls[0]
