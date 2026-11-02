"""Baseline design checks: explicit dates, valid calls and immutable artifact detection."""

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from healthops.assistant.toolbox import build_registry

PATH = Path(__file__).resolve().parents[1] / "scripts/prepare_cohort_baseline.py"
spec = importlib.util.spec_from_file_location("cohort_baseline", PATH)
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


@pytest.mark.parametrize("cohort", ["apple_watch_only", "fitbit_only", "paired"])
def test_ten_cases_have_valid_gold_calls_and_date_semantics(cohort):
    user = {"last_date": "2026-08-18", "cohort": cohort}
    cases = baseline.questions(user)
    assert len(cases) == len({c[0] for c in cases}) == 10
    registry = build_registry()
    for category, question, tool, arguments in cases:
        assert question and tool in registry
        if tool in {"availability", "daily_summary", "period_comparison"}:
            registry[tool].params.model_validate({**arguments, "timezone": "America/New_York"})
        if category == "comparison":
            assert arguments["baseline_end"] < arguments["current_start"]
            assert date.fromisoformat(arguments["baseline_end"]) + timedelta(
                days=1
            ) == date.fromisoformat(arguments["current_start"])
        if category == "relative_date":
            today = date.fromisoformat(user["last_date"]) + timedelta(days=1)
            assert arguments["start_date"] == str(today - timedelta(days=1))
        if category == "provider":
            assert arguments["provider"] == (
                "google_health" if cohort == "fitbit_only" else "healthkit"
            )
        if category == "missing":
            assert arguments["start_date"] > user["last_date"]


def test_verification_rejects_model_or_artifact_drift(tmp_path):
    content = b"frozen gold"
    (tmp_path / "questions.jsonl").write_bytes(content)
    snapshot = {"digest": "original"}
    (tmp_path / "lock.json").write_text(
        json.dumps(
            {
                "snapshot": snapshot,
                "artifacts_sha256": {"questions.jsonl": baseline.sha(content)},
            }
        )
    )
    baseline.verify_artifacts(tmp_path, snapshot)
    with pytest.raises(ValueError, match="drift"):
        baseline.verify_artifacts(tmp_path, {"digest": "changed"})
    (tmp_path / "questions.jsonl").write_bytes(b"edited gold")
    with pytest.raises(ValueError, match="artifact changed"):
        baseline.verify_artifacts(tmp_path, snapshot)


def test_runner_does_not_credit_server_repair_as_raw_model_success():
    import sys
    from types import SimpleNamespace

    sys.path.insert(0, str(PATH.parent))
    from run_cohort_baseline import grade

    expected = {
        "tool": "daily_summary",
        "arguments": {"metric": "steps"},
        "model_arguments": {"metric": "steps"},
        "tool_result": {"record_count": 0},
    }
    call = SimpleNamespace(
        name="daily_summary",
        model_arguments={"metric": "steps", "provider": "healthkit"},
        arguments={"metric": "steps", "timezone": "America/New_York", "provider": None},
        warnings=["provider_filter_ignored"],
    )
    response = SimpleNamespace(
        tool_calls=[call],
        evidence=[{"tool": "daily_summary", "result": {"record_count": 0}}],
        status="insufficient_data",
        explanation_status="verified",
        claims=[],
        answer="Missing",
    )
    item = {"id": "test", "category": "missing", "cohort": "paired", "expected": expected}
    result = grade(item, response)
    assert result["tool_selected_ok"] is True
    assert result["raw_arguments_ok"] is False
    assert result["executed_arguments_ok"] is True
    assert result["gold_evidence_ok"] is True
    response.evidence[0]["result"] = {"record_count": 1}
    assert grade(item, response)["gold_evidence_ok"] is False
