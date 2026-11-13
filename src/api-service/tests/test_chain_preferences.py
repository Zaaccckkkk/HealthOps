"""Preference export guards using tiny scripted fixtures, never observed health records."""

import copy
import json

import pytest
from test_assistant_narrative import draft, run_writer

from healthops.assistant import narrative
from scripts.build_chain_preferences import candidate


def fixture():
    response, _ = run_writer(draft("The available recordings differ."))
    response.routing_status = "ready"
    question = "Describe the recorded resting heart rate."
    context = {
        "user_dialogue": [{"role": "user", "content": question}],
        "confirmed_request": response.request_spec,
    }
    packet = narrative.packet(response.cards, str(response.time_semantics.today))
    raw = draft("The available recordings differ.").text
    trace = {
        "id": "scripted-fixture",
        "case": {"split": "development", "data_kind": "synthetic", "questions": [question]},
        "gold_tool_query": response.evidence[0],
        "turns": [
            {
                "http_status": 200,
                "request": {"provider": None},
                "response": response.model_dump(mode="json"),
                "model_calls": [
                    {
                        "messages": narrative.messages(json.dumps(context), packet, "en"),
                        "tools": [],
                        "raw_text": raw,
                    }
                ],
            }
        ],
    }
    review = {
        "disposition": "candidate",
        "quality": "partial",
        "evidence_review": "approved_silver",
        "reason": "Scripted test only",
        "evidence_support": ["sources[0]"],
        "chosen_answer": (
            "The recorded readings differ; these samples do not establish a full-day average."
        ),
    }
    return trace, review


def test_preserves_exact_prompt_and_rejected_output_without_approving_training():
    trace, review = fixture()
    original = copy.deepcopy(trace)
    row = candidate(trace, review)
    call = trace["turns"][0]["model_calls"][0]
    assert row["prompt"] == call["messages"]
    assert row["rejected"][0]["content"] == call["raw_text"]
    assert row["training_eligible"] is False
    assert row["review_status"] == "pending_independent_human_review"
    assert trace == original


def test_multimetric_candidate_requires_all_expected_tool_results():
    trace, review = fixture()
    first = trace["gold_tool_query"]
    trace["gold_tool_query"] = [first]
    assert candidate(trace, review)["training_eligible"] is False
    absent = copy.deepcopy(first)
    absent["result"] = {"metric": "hrv_rmssd", "missing_test_result": True}
    trace["gold_tool_query"].append(absent)
    with pytest.raises(ValueError, match="Evidence/trace"):
        candidate(trace, review)


@pytest.mark.parametrize(
    "field,value",
    [("split", "reserved"), ("split", "heldout_test"), ("data_kind", "personal")],
)
def test_non_development_or_personal_traces_cannot_be_candidates(field, value):
    trace, review = fixture()
    trace["case"][field] = value
    with pytest.raises(ValueError, match="Only synthetic development"):
        candidate(trace, review)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("writer_missing", "Evidence/trace"),
        ("packet_changed", "Evidence/trace"),
        ("dialogue_changed", "dialogue differs"),
        ("provider_missing", "Selected provider"),
        ("unreviewed", "reviewed writing defect"),
        ("unsupported_chosen", "unsupported_number"),
    ],
)
def test_rejects_upstream_gaps_tampering_and_unsupported_chosen(mutation, reason):
    trace, review = fixture()
    last = trace["turns"][0]
    if mutation == "writer_missing":
        last["model_calls"] = []
    elif mutation == "packet_changed":
        call = last["model_calls"][0]
        payload = json.loads(call["messages"][1]["content"])
        payload["evidence"]["sources"][0]["provider"] = "invented"
        call["messages"][1]["content"] = json.dumps(payload)
    elif mutation == "dialogue_changed":
        trace["case"]["questions"] = ["A different question"]
    elif mutation == "provider_missing":
        last["request"]["provider"] = "google_health"
    elif mutation == "unreviewed":
        review["evidence_review"] = "pending"
    else:
        review["chosen_answer"] = "You recorded 999999 bpm."
    with pytest.raises(ValueError, match=reason):
        candidate(trace, review)


def test_retry_audit_preserves_scope_but_export_requires_separate_review():
    from healthops.assistant.contracts import AskResponse
    from healthops.assistant.model import ModelTurn
    from scripts.eval_writer_chain import audit

    trace, review = fixture()
    last = trace["turns"][0]
    first = last["model_calls"][0]
    first["raw_text"] = draft(ids=["wrong-card"]).text
    facts = json.loads(first["messages"][1]["content"])["evidence"]
    second = copy.deepcopy(first)
    first_turn = ModelTurn(text=first["raw_text"])
    _, issues = narrative.check_draft(first_turn, facts)
    second["messages"] = narrative.editor_messages(first["messages"], first_turn, issues)
    second["raw_text"] = draft().text
    last["model_calls"].append(second)
    response = AskResponse.model_validate(last["response"])
    assert all(audit(response, last["model_calls"], trace["gold_tool_query"]).values())
    with pytest.raises(ValueError, match="retries require separate review"):
        candidate(trace, review)
    second["messages"][-1]["content"] = "Use different evidence"
    assert not audit(response, last["model_calls"], trace["gold_tool_query"])[
        "writer_repair_matches_contract"
    ]


@pytest.mark.parametrize("version", list(narrative.SYSTEM_VERSIONS))
@pytest.mark.parametrize("language", ["en", "zh"])
def test_audit_reconstructs_versioned_evidence_and_rejects_tampering(version, language):
    from healthops.assistant.contracts import AskResponse
    from healthops.assistant.evidence_facts import enriched_packet
    from healthops.assistant.presentation import presentation_packet
    from scripts.eval_writer_chain import audit

    trace, _ = fixture()
    last = trace["turns"][0]
    response = AskResponse.model_validate(last["response"])
    facts = narrative.packet(response.cards, str(response.time_semantics.today))
    call = last["model_calls"][0]
    call["messages"][0]["content"] = narrative.SYSTEM_VERSIONS[version]
    payload = json.loads(call["messages"][1]["content"])
    payload["language"] = language
    if version == "grounded-answer-v6-computed-evidence":
        payload["evidence"] = enriched_packet(facts, language)
    elif version == "grounded-answer-v5-precomputed-presentation":
        payload["evidence"] = presentation_packet(facts, language)
    else:
        payload["evidence"] = copy.deepcopy(facts)
    call["messages"][1]["content"] = json.dumps(payload)
    before = copy.deepcopy(last["model_calls"])
    assert all(audit(response, last["model_calls"], trace["gold_tool_query"]).values())
    assert last["model_calls"] == before
    source = payload["evidence"]["sources"][0]
    if version == "grounded-answer-v6-computed-evidence":
        source["computed_facts"]["first_to_last"]["absolute_change"] = "999999"
    elif version == "grounded-answer-v5-precomputed-presentation":
        source["provider_display"] = "Invented source"
    else:
        source["days"][0]["value"] = "999999"
    call["messages"][1]["content"] = json.dumps(payload)
    assert not audit(response, last["model_calls"], trace["gold_tool_query"])[
        "writer_input_matches_cards"
    ]
