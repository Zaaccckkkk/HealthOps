"""Dataset hygiene and input isolation, not certification of assistant-authored prose."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from eval.build_writer_corpus import records
from healthops.assistant import narrative
from healthops.assistant.cards import Card
from healthops.assistant.model import ModelTurn
from scripts.eval_answer_quality import load_cases, writer_input

ROOT = Path(__file__).parents[1] / "eval/writer-corpus"


def read(name):
    return [json.loads(line) for line in (ROOT / name).read_text().splitlines()]


def test_seventy_prompts_keep_scenarios_cohorts_and_paraphrases_in_one_split():
    dev = read("development.synthetic.jsonl")
    reserved = read("reserved_test.synthetic.jsonl")
    assert len(dev) == 50 and len(reserved) == 20
    assert len({r["scenario_group"] for r in dev}) == 25
    assert len({r["scenario_group"] for r in reserved}) == 10
    for field in ("id", "scenario_group", "synthetic_cohort", "question"):
        assert not {r[field] for r in dev} & {r[field] for r in reserved}
    dev_dates = {c["start_date"] for r in dev for c in r["cards"]}
    test_dates = {c["start_date"] for r in reserved for c in r["cards"]}
    assert not dev_dates & test_dates
    by_id = {r["id"]: r for r in dev + reserved}
    assert by_id == {r["id"]: r for r in records()}
    manifest = json.loads((ROOT / "manifest.json").read_text())
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "name",
    [
        "development.synthetic.jsonl",
        "reserved_test.synthetic.jsonl",
        "known_failures.synthetic.jsonl",
    ],
)
def test_references_are_unapproved_silver_and_pass_only_basic_consistency(name):
    for row in read(name):
        assert row["data_kind"] == "synthetic"
        assert row["label_provenance"] == "assistant_authored_silver"
        assert row["review_status"] == "pending_human_review"
        assert row["training_eligible"] is False
        assert row["manual_rubric"] and row["required_facts"]
        cards = [Card.model_validate(c) for c in row["cards"]]
        facts = narrative.packet(cards, row["today"])
        turn = ModelTurn(
            text=json.dumps(
                {"answer": row["reference_answer"], "evidence_ids": [c.evidence_id for c in cards]}
            )
        )
        _, error = narrative.validate(turn, facts)
        assert error is None, (row["id"], error)


def test_seen_failures_are_not_called_independent_and_checker_case_is_not_sft_error():
    rows = read("known_failures.synthetic.jsonl")
    assert len(rows) == 9
    assert all(r["split"] == "known_failure_regression" and r["observed_run"] for r in rows)
    assert sum(r["disposition"] == "writing_candidate" for r in rows) == 8
    checker = next(r for r in rows if r["source_case_id"] == "injection")
    assert checker["disposition"] == "checker_regression_only"
    assert not {r["id"] for r in rows} & {r["id"] for r in records()}


def test_labels_and_rubrics_never_leak_into_model_input():
    row = copy.deepcopy(read("development.synthetic.jsonl")[0])
    for key in ("reference_answer", "required_facts", "manual_rubric", "forbidden_claims"):
        row[key] = "SECRET_LABEL_SENTINEL_DO_NOT_SEND"
    _, _, payload = writer_input(row)
    assert "SECRET_LABEL_SENTINEL" not in json.dumps(payload)
    assert row["question"] in payload[1]["content"]


def test_reserved_set_is_explicit_and_development_loader_cannot_mix_it_in():
    assert len(load_cases("development")) == 50
    with pytest.raises(ValueError, match="include-reserved"):
        load_cases("reserved")
    assert len(load_cases("reserved", include_reserved=True)) == 20


def test_expansion_preserves_the_frozen_holdout_and_original_development_records():
    assert hashlib.sha256((ROOT / "reserved_test.synthetic.jsonl").read_bytes()).hexdigest() == (
        "58cbe48d6203019175049d63ec10116d0fe4e776aed76811896ce21ed27bf98b"
    )
    original = [
        r for r in read("development.synthetic.jsonl") if r["version"] == "writer-silver-v1"
    ]
    assert len(original) == 30
    content = "".join(json.dumps(r, ensure_ascii=True, sort_keys=True) + "\n" for r in original)
    assert hashlib.sha256(content.encode()).hexdigest() == (
        "0a920b997c7f791afedf5f95d5286f51a1f49e22e680b4d28c882a7527d08330"
    )
