"""The diagnostic set and extra rubric reject omitted caveats and fabricated readings."""

import json
import sys
from pathlib import Path

from test_reconcile import DAY, card, result

from healthops.assistant.claims import FindingsParams, render_explanation
from healthops.assistant.contracts import AskResponse, ToolCallRecord, VerificationAttempt
from healthops.assistant.time_semantics import resolve

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from eval_reconcile import ACCOUNTS, cases_for, grade  # noqa: E402


def test_42_scenarios_45_turns_and_single_day_gold():
    rows = [r for source in ACCOUNTS for r in cases_for(source)]
    assert len(rows) == 42 and len({r["id"] for r in rows}) == 42
    assert sum(len(r["turns"]) for r in rows) == 45
    for row in rows:
        for turn in row["turns"]:
            e = turn["expected"]
            if e["route"] == "ready":
                assert e["metric"] in {"steps", "sleep_duration", "resting_heart_rate"}
                assert e["tool"] == "reconcile_devices"
                assert e["date"] in turn["question"] or row["family"] in {
                    "yesterday_steps",
                    "clarify_then_resume",
                }


def test_committed_scenarios_match_generator_and_have_defined_warning_expectations():
    from healthops.tools.reconcile import DEFINITIONS

    path = Path(__file__).resolve().parents[1] / "eval/assistant_reconcile.synthetic.jsonl"
    published = [json.loads(line) for line in path.read_text().splitlines()]
    for row in published:
        for turn in row["turns"]:
            e = turn["expected"]
            if e["route"] == "ready":
                codes = e.pop("warnings")
                assert codes == sorted(set(codes))
                assert all(f"warning:{code}" in DEFINITIONS for code in codes)
    assert published == [r for source in ACCOUNTS for r in cases_for(source)]


def response_for(r):
    c = card(r)
    finding = f"value_on_date e1 {DAY}" if r.q50 is not None else "missing_data e1"
    text, claims, _ = render_explanation(FindingsParams(findings=[finding]), [c])
    return AskResponse(
        status="answered" if r.q50 is not None else "insufficient_data",
        answer=text,
        time_semantics=resolve("America/New_York", DAY),
        cards=[c],
        claims=claims,
        explanation_status="verified",
        completion_status="complete",
        routing_status="ready",
        numeric_consistency={"status": "not_applicable", "numbers_in_answer": [], "unmatched": []},
        model={
            "name": "fixture",
            "endpoint_host": None,
            "prompt_version": "fixture",
            "max_output_tokens": 800,
            "temperature": 0,
        },
        limits={
            "max_tool_calls": 4,
            "max_model_turns": 6,
            "deadline_seconds": 120,
            "model_context_budget_chars": 24000,
            "evidence_budget_bytes": 262144,
        },
        disclaimer="Synthetic fixture, not medical advice",
        verification_attempts=[
            VerificationAttempt(valid_claims=1, invalid_reasons=[], missing_targets=[])
        ],
        tool_calls=[
            ToolCallRecord(
                name="reconcile_devices",
                status="ok",
                arguments={"metric": "steps", "date": str(DAY), "timezone": "America/New_York"},
            )
        ],
        evidence=[{"tool": "reconcile_devices", "result": r.model_dump(mode="json")}],
    )


def test_rubric_fails_missing_warning_interval_or_tampered_attribution():
    r = result(first_day=DAY)
    expected = {
        "route": "ready",
        "metric": "steps",
        "date": str(DAY),
        "result": r.model_dump(mode="json"),
    }
    response = response_for(r)
    grade_ok = grade(expected, response, "America/New_York")
    assert grade_ok["passed"], grade_ok
    for text in (
        "Data may not be sufficient; the result may not be reliable.",
        "nominal 90% prediction interval",
    ):
        changed = response.model_copy(deep=True)
        changed.answer = changed.answer.replace(text, "")
        assert not grade(expected, changed, "America/New_York")["passed"]
    changed = response.model_copy(deep=True)
    changed.evidence[0]["result"]["attribution"] = "healthkit"
    assert not grade(expected, changed, "America/New_York")["passed"]


def test_no_reading_rubric_does_not_accept_fabricated_number():
    r = result([], [])
    expected = {
        "route": "ready",
        "metric": "steps",
        "date": str(DAY),
        "result": r.model_dump(mode="json"),
    }
    response = response_for(r)
    assert (
        grade(expected, response, "America/New_York")["rubric"]["no_number_when_no_reading"]
        == "yes"
    )
    response.answer += " Your steps were 5000."
    assert (
        grade(expected, response, "America/New_York")["rubric"]["no_number_when_no_reading"] == "no"
    )
