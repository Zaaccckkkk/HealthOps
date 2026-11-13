"""No model server or private data is contacted by these tests."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from healthops.assistant.model import ModelTurn
from healthops.assistant.narrative import SYSTEM
from scripts.eval_writer_checkpoint import (
    WriterDispatchModel,
    compare_reviews,
    evaluate,
    load_inputs,
)


class FakeModel:
    info = SimpleNamespace(name="test")

    def __init__(self, text="{}"):
        self.calls = []
        self.text = text

    async def complete(self, messages, tools):
        self.calls.append((messages, tools))
        return ModelTurn(text=self.text)


def test_only_writer_uses_adapter_router_and_tools_stay_on_base():
    base, writer = FakeModel(), FakeModel()
    model = WriterDispatchModel(base, writer)
    router_messages = [{"role": "system", "content": "Route this question"}]
    writer_messages = [{"role": "system", "content": SYSTEM}]
    asyncio.run(model.complete(router_messages, []))
    asyncio.run(model.complete(router_messages, [{"name": "daily_summary"}]))
    asyncio.run(model.complete(writer_messages, []))
    assert len(base.calls) == 2 and len(writer.calls) == 1
    assert model.info is base.info
    with pytest.raises(ValueError, match="tools"):
        asyncio.run(model.complete(writer_messages, [{"name": "daily_summary"}]))


def test_tampered_frozen_replay_fails_before_inference(tmp_path):
    (tmp_path / "writer-baseline-inputs.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="changed"):
        load_inputs(tmp_path)


def test_duration_supplement_is_also_hash_pinned(tmp_path):
    (tmp_path / "writer-baseline-inputs.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="changed"):
        load_inputs(tmp_path, duration=True)


def test_mechanical_pass_remains_semantically_unreviewed():
    facts = {"sources": [{"evidence_id": "e1"}]}
    raw = json.dumps({"answer": "No conclusion is supported.", "evidence_ids": ["e1"]})
    rows = [
        {
            "id": "fixture",
            "messages_sha256": "fixture-hash",
            "baseline_raw_output": raw,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps({"evidence": facts})},
            ],
        }
    ]
    model = FakeModel(raw)
    result = asyncio.run(evaluate(rows, model))
    assert result[0]["mechanical_error"] is None
    assert result[0]["semantic_verdict"] == "pending"
    assert result[0]["training_eligible"] is False
    assert model.calls[0][0] == rows[0]["messages"]


def test_paired_score_requires_complete_reviews_and_identical_inputs():
    before = [
        {
            "id": "one",
            "messages_sha256": "same",
            "semantic_verdict": "fail",
            "review_reason": "wrong attribution",
        }
    ]
    after = [
        {
            "id": "one",
            "messages_sha256": "same",
            "semantic_verdict": "pass",
            "review_reason": "matches evidence",
        }
    ]
    assert compare_reviews(before, after) == {
        "paired_writer_inputs": 1,
        "before_pass": 0,
        "after_pass": 1,
        "not_a_full_chain_score": True,
    }
    after[0]["semantic_verdict"] = "pending"
    with pytest.raises(ValueError, match="reviews"):
        compare_reviews(before, after)
    after[0]["messages_sha256"] = "changed"
    with pytest.raises(ValueError, match="identical"):
        compare_reviews(before, after)
