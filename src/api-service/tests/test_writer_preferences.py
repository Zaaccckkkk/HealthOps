"""Preference provenance and leakage guards, not a semantic-quality judge."""

import copy
import hashlib
import json

import pytest

from scripts.build_writer_preferences import build_candidates
from scripts.eval_answer_quality import load_cases, writer_input


def fixture():
    case = copy.deepcopy(load_cases("development")[0])
    _, facts, payload = writer_input(case)
    raw = json.dumps(
        {"answer": "Your activity crashed today.", "evidence_ids": ["e1.google_health"]}
    )
    run = {
        "suite": "development",
        "corpus_sha256": hashlib.sha256(
            (json.dumps(case, ensure_ascii=True, sort_keys=True) + "\n").encode()
        ).hexdigest(),
        "model": {"name": "scripted-test"},
        "results": [
            {
                "id": case["id"],
                "input_messages": payload,
                "evidence_packet": facts,
                "raw_model_output": raw,
                "answer": "SERVER_ADDED_TEXT_MUST_NOT_BECOME_REJECTED",
            }
        ],
    }
    reviews = [
        {
            "id": case["id"],
            "quality": "fail",
            "disposition": "candidate",
            "reason": "Unfinished readings do not establish actual activity.",
            "evidence_support": "includes_today=true; interval totals.",
            "chosen_answer": "Today's unfinished recording cannot establish an activity crash.",
        }
    ]
    return run, [case], reviews


def test_candidates_preserve_exact_input_and_raw_completion_and_remain_unapproved():
    run, cases, reviews = fixture()
    (candidate,) = build_candidates(run, cases, reviews)
    assert candidate["prompt"] == run["results"][0]["input_messages"]
    assert candidate["rejected"][0]["content"] == run["results"][0]["raw_model_output"]
    assert "SERVER_ADDED_TEXT" not in json.dumps(candidate)
    assert cases[0]["reference_answer"] not in json.dumps(candidate["prompt"])
    assert candidate["training_eligible"] is False
    assert candidate["review_status"] == "pending_independent_human_review"


@pytest.mark.parametrize("location", ["run", "case"])
def test_reserved_data_cannot_be_exported(location):
    run, cases, reviews = fixture()
    if location == "run":
        run["suite"] = "reserved"
    else:
        cases[0]["split"] = "reserved_test"
    with pytest.raises(ValueError, match="Only synthetic development"):
        build_candidates(run, cases, reviews)


@pytest.mark.parametrize("field", ["question", "reference_answer"])
def test_snapshot_edits_are_detected(field):
    run, cases, reviews = fixture()
    cases[0][field] += " CHANGED"
    with pytest.raises(ValueError, match="corpus hash"):
        build_candidates(run, cases, reviews)


def test_changed_prompt_cannot_be_paired_with_an_old_completion():
    run, cases, reviews = fixture()
    run["results"][0]["input_messages"][0]["content"] = "Different instructions"
    with pytest.raises(ValueError, match="Recorded input differs"):
        build_candidates(run, cases, reviews)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown"])
def test_review_must_cover_exactly_the_evaluated_cases(change):
    run, cases, reviews = fixture()
    if change == "missing":
        reviews.clear()
    elif change == "duplicate":
        reviews.append(copy.deepcopy(reviews[0]))
    else:
        reviews[0]["id"] = "unobserved"
    with pytest.raises(ValueError, match="One explicit review"):
        build_candidates(run, cases, reviews)


def test_missing_inference_cannot_become_a_bad_answer():
    run, cases, reviews = fixture()
    run["results"][0]["raw_model_output"] = None
    with pytest.raises(ValueError, match="Infrastructure failures"):
        build_candidates(run, cases, reviews)


def test_chosen_must_pass_consistency_but_that_does_not_certify_semantics():
    run, cases, reviews = fixture()
    reviews[0]["chosen_answer"] = "You recorded 999999999 steps."
    with pytest.raises(ValueError, match="Chosen answer fails"):
        build_candidates(run, cases, reviews)


def test_successes_and_exclusions_do_not_create_forced_preferences():
    run, cases, reviews = fixture()
    for disposition in ("retain_success", "exclude"):
        reviews[0]["disposition"] = disposition
        assert build_candidates(run, cases, reviews) == []
    reviews[0].update(quality="pass", disposition="candidate")
    with pytest.raises(ValueError, match="acceptable answer"):
        build_candidates(run, cases, reviews)
