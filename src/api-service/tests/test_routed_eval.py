"""Evaluator regression tests use scripted responses, never claim real-model accuracy."""

import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest
from test_assistant import USER, call
from test_assistant_guards import END, START, data, findings, run, spec
from test_assistant_routing import app, decision

from healthops.assistant.model import ModelTurn
from healthops.assistant.toolbox import build_registry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "eval"))
import build_routed_set  # noqa: E402
import eval_routed  # noqa: E402


def example():
    response, _, _ = run(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary", metric="resting_heart_rate", start_date=START, end_date=END
                    )
                ]
            ),
            findings("window_summary e1.healthkit"),
        ]
    )
    request_spec = spec().model_dump(mode="json")
    response.routing_status = "ready"
    response.request_spec = request_spec
    case = {"timezone": "America/New_York", "provider": None, "grading_scope": "end_to_end"}
    turn = {"expected": {"route": "ready", "spec": request_spec}}
    gold = [{"tool": "daily_summary", "result": data()}]
    return case, turn, response, gold


def test_dataset_is_reproducible_and_gold_arguments_are_valid():
    rows = eval_routed.load_cases(ROOT / "eval/assistant_routed.synthetic.jsonl")
    assert rows == build_routed_set.build()
    assert len(rows) == 63
    assert {r["source_user_id"] for r in rows} == eval_routed.ACCOUNTS
    registry = build_registry()
    for case in rows:
        for turn in case["turns"]:
            for tool, arguments in eval_routed.gold_queries(case, turn):
                registry[tool].params.model_validate(arguments)
    assert (
        eval_routed.gold_queries(rows[1], rows[1]["turns"][0])[0][1]["start_date"] == "2026-08-09"
    )


def test_verified_complete_correct_evidence_passes():
    case, turn, response, gold = example()
    grade = eval_routed.grade_turn(case, turn, response, gold)
    assert grade["passed"], grade
    assert grade["first_verification_passed"]
    assert grade["repair_recovered"] is None


@pytest.mark.parametrize(
    "mutation,failed_check",
    [
        ("wrong_route", "route"),
        ("narrowed_spec", "request_spec"),
        ("omitted_metric", "coverage"),
        ("wrong_gold", "gold_evidence"),
        ("no_verification", "verification_recorded"),
        ("invented_answer", "claims_rechecked"),
        ("wrong_card", "cards_from_evidence"),
        ("outside_scope", "executed_scope"),
        ("wrong_tool", "primary_tool"),
    ],
)
def test_false_pass_regressions(mutation, failed_check):
    case, turn, response, gold = example()
    turn = copy.deepcopy(turn)
    if mutation == "wrong_route":
        response.routing_status = "unsupported"
    elif mutation == "narrowed_spec":
        response.request_spec = {**response.request_spec, "metrics": ["steps"]}
    elif mutation == "omitted_metric":
        turn["expected"]["spec"]["metrics"].append("sleep_duration")
    elif mutation == "wrong_gold":
        gold[0]["result"]["record_count"] = 123456789
    elif mutation == "no_verification":
        response.verification_attempts = []
    elif mutation == "invented_answer":
        response.answer = "You are healthy."
    elif mutation == "wrong_card":
        response.cards[0].rows[0].value = "123456789"
    elif mutation == "outside_scope":
        response.tool_calls[0].arguments["start_date"] = "2000-01-01"
    elif mutation == "wrong_tool":
        response.tool_calls[0].name = "availability"
    grade = eval_routed.grade_turn(case, turn, response, gold)
    assert not grade["passed"]
    assert not grade["checks"][failed_check]


def test_runner_uses_real_continuation_pair_for_cancellation():
    case = next(c for c in build_routed_set.build() if c["family"] == "cancel")
    assistant, model, tools = app([decision("needs_clarification", metrics=["steps"])])
    result = asyncio.run(eval_routed.evaluate_case(case, USER, assistant, [[], []]))
    assert result["passed"], result
    assert len(model.requests) == 1  # Cancellation never calls the model.
    assert not tools.calls
    assert result["turns"][1]["response"]["routing_status"] == "cancelled"


def test_failed_initial_route_blocks_reply_and_stays_in_denominator():
    case = next(c for c in build_routed_set.build() if c["family"] == "cancel")
    assistant, _, _ = app([decision("unsupported")])
    result = asyncio.run(eval_routed.evaluate_case(case, USER, assistant, [[], []]))
    assert not result["passed"] and result["blocked_turns"] == 1
    summary = eval_routed.summarize([result], 1)
    assert summary["planned_scenarios"] == 1 and summary["passed_scenarios"] == 0
    assert summary["repair_recovery"]["total"] == 0
    assert summary["stage_reach"]["responses"] == 1
    assert summary["stage_reach"]["unsupported"] == 1
    assert summary["stage_reach"]["tool_execution"] == 0
    assert summary["stage_reach"]["verification"] == 0


def test_repair_count_has_conditional_denominator():
    case, turn, response, gold = example()
    response, _, _ = run(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary", metric="resting_heart_rate", start_date=START, end_date=END
                    )
                ]
            ),
            findings("window_summary e99.healthkit"),
            findings("window_summary e1.healthkit"),
        ]
    )
    response.routing_status = "ready"
    response.request_spec = turn["expected"]["spec"]
    grade = eval_routed.grade_turn(case, turn, response, gold)
    assert grade["passed"] and grade["repair_triggered"] and grade["repair_recovered"]
    assert not grade["first_verification_passed"]


def test_non_synthetic_account_rejected_before_execution(tmp_path):
    rows = build_routed_set.build()[:1]
    rows[0]["source_user_id"] = "owner"
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(rows[0]) + "\n")
    with pytest.raises(ValueError, match="synthetic"):
        eval_routed.load_cases(path)


def test_correct_missing_answer_passes_without_fabricating_a_number():
    case, turn, _, _ = example()
    response, _, _ = run(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary", metric="resting_heart_rate", start_date=START, end_date=END
                    )
                ]
            ),
            findings("missing_data e1"),
        ],
        results={"resting_heart_rate": data(empty=True)},
    )
    response.routing_status = "ready"
    response.request_spec = turn["expected"]["spec"]
    gold = [{"tool": "daily_summary", "result": data(empty=True)}]
    grade = eval_routed.grade_turn(case, turn, response, gold)
    assert grade["passed"], grade
    assert response.coverage[0].query_status == "no_records"


def test_two_failed_verifications_do_not_count_as_recovery():
    case, turn, _, gold = example()
    response, _, _ = run(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary", metric="resting_heart_rate", start_date=START, end_date=END
                    )
                ]
            ),
            findings("window_summary e99.healthkit"),
            findings("window_summary e99.healthkit"),
        ]
    )
    response.routing_status = "ready"
    response.request_spec = turn["expected"]["spec"]
    grade = eval_routed.grade_turn(case, turn, response, gold)
    assert grade["repair_triggered"]
    assert not grade["repair_recovered"] and not grade["passed"]
    assert not grade["checks"]["final_verification_passed"]
