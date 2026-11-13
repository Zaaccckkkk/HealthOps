"""Fail closed before exporting weights or replaying held-out prompts."""

import hashlib
import json

import pytest

from writer_dpo import posttrain


def receipt():
    return {
        "completed": True,
        "artifacts_synced": True,
        "returncode": 0,
        "inputs": {"bundle": "approved", "snapshot": "pinned"},
        "checkpoint_files": {
            "checkpoints/step_60/config.yaml": "a",
            "checkpoints/step_60/training_info.json": "b",
            "checkpoints/step_60/policy/weights/iter_0000000/__0_0.distcp": "c",
            "checkpoints/step_56/config.yaml": "other",
        },
    }


def test_selects_only_exact_completed_checkpoint():
    result = posttrain.checkpoint_files(receipt(), 60, receipt()["inputs"])
    assert len(result) == 3 and result["config.yaml"] == "a"


@pytest.mark.parametrize(
    "field,value",
    [("completed", False), ("artifacts_synced", False), ("returncode", 1), ("inputs", {})],
)
def test_rejects_failed_or_unapproved_training(field, value):
    value_receipt = receipt()
    value_receipt[field] = value
    with pytest.raises(ValueError, match="approved inputs"):
        posttrain.checkpoint_files(value_receipt, 60, receipt()["inputs"])


def test_rejects_missing_checkpoint_and_path_escape():
    with pytest.raises(ValueError, match="incomplete"):
        posttrain.checkpoint_files(receipt(), 6, receipt()["inputs"])
    unsafe = receipt()
    unsafe["checkpoint_files"]["checkpoints/step_60/../escape"] = "d"
    with pytest.raises(ValueError, match="Unsafe"):
        posttrain.checkpoint_files(unsafe, 60, receipt()["inputs"])


def frozen_fixture(tmp_path, monkeypatch, **overrides):
    messages = [{"role": "system", "content": "Synthetic test instructions"}]
    digest = hashlib.sha256(
        json.dumps(messages, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    row = {
        "id": "test",
        "split": "heldout_test",
        "training_eligible": False,
        "tools": [],
        "messages": messages,
        "messages_sha256": digest,
    }
    row.update(overrides)
    path = tmp_path / "test.jsonl"
    path.write_text(json.dumps(row) + "\n")
    monkeypatch.setattr(posttrain, "FROZEN", {path.name: (posttrain.file_hash(path), 1)})
    return path


def test_freeze_accepts_valid_inputs_and_rejects_tampering(tmp_path, monkeypatch):
    path = frozen_fixture(tmp_path, monkeypatch)
    assert len(posttrain.load_replays(tmp_path)) == 1
    path.write_text(path.read_text().replace("Synthetic", "Changed"))
    with pytest.raises(ValueError, match="changed"):
        posttrain.load_replays(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"training_eligible": True},
        {"split": "train"},
        {"tools": ["sql"]},
        {"messages_sha256": "wrong"},
    ],
)
def test_test_provenance_is_required(tmp_path, monkeypatch, overrides):
    frozen_fixture(tmp_path, monkeypatch, **overrides)
    with pytest.raises(ValueError, match="provenance"):
        posttrain.load_replays(tmp_path)


def test_export_is_pinned_to_reviewed_upstream_converter():
    assert len(posttrain.CONVERTER_SHA256) == 64
    assert "MegatronPolicyWorker/bin/python" in posttrain.WORKER_PYTHON
