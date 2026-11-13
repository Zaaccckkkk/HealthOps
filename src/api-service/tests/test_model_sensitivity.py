"""Stage replay must not leak reference answers or consume heldout/real captures."""

import asyncio
import json

import pytest

from healthops.assistant.model import ModelTurn
from healthops.assistant.narrative import SYSTEM
from scripts import eval_model_sensitivity as replay


@pytest.mark.parametrize("field,value", [("split", "test"), ("data_kind", "personal")])
def test_replay_rejects_non_development_capture(tmp_path, field, value):
    case = {"data_kind": "synthetic", "split": "development", "source_user_id": "synthetic-00000"}
    case[field] = value
    (tmp_path / "capture.json").write_text(json.dumps({"case": case, "turns": []}))
    with pytest.raises(ValueError, match="Only synthetic development"):
        replay.load_rows(tmp_path, "writer")


def test_replay_keeps_chosen_out_of_input_and_checks_only_once(tmp_path, monkeypatch):
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps({"evidence": []})},
    ]
    calls, checks = [], []

    class Model:
        async def complete(self, supplied, tools):
            calls.append(supplied)
            assert "SECRET_REFERENCE" not in json.dumps(supplied)
            return ModelTurn(
                text=json.dumps({"answer": "Synthetic final answer.", "evidence_ids": []})
            )

    def check(turn, facts):
        checks.append(facts)
        return None, ["fixture diagnostic"]

    monkeypatch.setattr(replay, "check_draft", check)
    row = {
        "id": "fixture",
        "stage": "writer",
        "messages": messages,
        "tools": [],
        "input_sha256": "fixture",
        "reference_chosen": "SECRET_REFERENCE",
    }
    result = asyncio.run(replay.evaluate([row], Model(), tmp_path))
    assert len(calls) == 2 and len(checks) == 1
    assert result[0]["edited"] and result[0]["semantic_review"] == "pending"
