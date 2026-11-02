"""Training eligibility is never inferred from a failed or routing-only diagnostic."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from triage_assistant_eval import summarize  # noqa: E402


def case(**updates):
    return {
        "id": "synthetic-case",
        "source_user_id": "synthetic-00000",
        "passed": True,
        "turns": [],
    } | updates


def test_routing_pass_with_empty_reference_still_requires_review():
    report = summarize(
        [
            case(
                turns=[
                    {
                        "response": {
                            "status": "tool_error",
                            "answer": "PRIVATE DO NOT COPY",
                            "evidence": [
                                {"tool": "lookup_reference", "result": {"references": []}}
                            ],
                        },
                        "grade": {"checks": {"route": True}, "passed": True},
                    }
                ]
            )
        ]
    )
    assert report["signal_counts"] == {"retrieval_empty": 1}
    assert report["reviews"][0]["eligible_for_training"] is False
    assert report["reviews"][0]["passed_current_rubric"] is True
    assert "PRIVATE DO NOT COPY" not in str(report)


def test_model_choice_and_transport_failures_are_separate_and_not_training_labels():
    report = summarize(
        [
            case(passed=False, turns=[{"grade": {"checks": {"route": False}}, "response": {}}]),
            case(id="synthetic-timeout", passed=False, turns=[{"error_type": "ReadTimeout"}]),
        ]
    )
    assert report["signal_counts"] == {"routing_or_scope": 1, "transport_or_state": 1}
    assert report["training_examples_created"] == 0
    assert all(not r["eligible_for_training"] for r in report["reviews"])


def test_unknown_identity_and_duplicate_ids_are_rejected():
    with pytest.raises(ValueError):
        summarize([case(source_user_id="personal-user")])
    with pytest.raises(ValueError):
        summarize([case(), case()])
