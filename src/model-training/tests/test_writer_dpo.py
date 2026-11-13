"""CPU contract tests; these do not claim distributed training has run."""

import copy
import json
import subprocess
import sys

import pytest

from writer_dpo.data import export_bundle, fingerprint, inspect, pair, verify_capture
from writer_dpo.launch import bounded_run, configuration, validate_runtime_config, verify_bundle
from writer_dpo.snapshot import CONFIG, verify_snapshot


def candidate(ident="one", split="train"):
    facts = {"sources": [{"evidence_id": ident}]}
    return {
        "id": ident,
        "split": "development_candidate",
        "split_group": ident,
        "proposed_split": split,
        "contract_blocked": False,
        "basic_validator_error": None,
        "prompt": [
            {"role": "system", "content": "Writer contract"},
            {"role": "user", "content": json.dumps({"evidence": facts})},
        ],
        "evidence_packet": facts,
        "chosen": [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "answer": "The recording cannot establish actual inactivity.",
                        "evidence_ids": [ident],
                    }
                ),
            }
        ],
        "rejected": [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "answer": "You were inactive.",
                        "evidence_ids": [ident],
                    }
                ),
            }
        ],
    }


def audit(train, validation):
    return inspect(train, validation, "Writer contract", capture_check=lambda _: "trace-hash")


def bundle(tmp_path):
    train = [candidate(str(i)) for i in range(4)]
    validation = [candidate("val", "validation")]
    report = audit(train, validation)
    approvals = [{**r, "decision": "approve", "reviewer": "fixture-reviewer"} for r in report]
    output = tmp_path / "bundle"
    export_bundle(train, validation, report, approvals, output)
    return output, verify_bundle(output)


def test_export_preserves_exact_conversation_and_completions(tmp_path):
    path, manifest = bundle(tmp_path)
    first = json.loads((path / "train.jsonl").read_text().splitlines()[0])
    assert first == pair(candidate("0"))
    assert set(first) == {"prompt", "chosen", "rejected"}
    assert manifest["counts"] == {"train": 4, "validation": 1}


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "pending"),
        ("reviewer", ""),
        ("pair_sha256", "stale"),
        ("split", "validation"),
    ],
)
def test_pending_or_stale_approval_cannot_export(tmp_path, field, value):
    train, validation = [candidate()], [candidate("two", "validation")]
    report = audit(train, validation)
    approvals = [{**r, "decision": "approve", "reviewer": "fixture"} for r in report]
    approvals[0][field] = value
    with pytest.raises(ValueError, match="approval"):
        export_bundle(train, validation, report, approvals, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "writer-holdout-case"),
        ("split", "heldout_test"),
        ("contract_blocked", True),
        ("basic_validator_error", "unsupported_number"),
    ],
)
def test_holdout_or_contract_blocked_rows_fail(field, value):
    row = candidate()
    row[field] = value
    with pytest.raises(ValueError):
        audit([row], [candidate("val", "validation")])


@pytest.mark.parametrize("kind", ["group", "evidence", "prompt", "id"])
def test_split_leakage_rejected(kind):
    train, val = candidate(), candidate("val", "validation")
    key = {"group": "split_group", "evidence": "evidence_packet", "prompt": "prompt", "id": "id"}[
        kind
    ]
    val[key] = copy.deepcopy(train[key])
    with pytest.raises(ValueError):
        audit([train], [val])


def test_changed_prompt_rejected():
    row = candidate()
    row["prompt"][0]["content"] = "Another contract"
    with pytest.raises(ValueError, match="contract changed"):
        audit([row], [candidate("val", "validation")])


def test_capture_requires_observed_negative_not_an_invented_one(tmp_path):
    row = candidate()
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "provenance": "reconstructed_synthetic_fixture_uploaded_via_API_not_cohort",
                "input_messages": row["prompt"],
                "raw_model_output": row["rejected"][0]["content"],
            }
        )
    )
    row["source"] = str(path)
    assert verify_capture(row)
    row["rejected"][0]["content"] = "invented negative"
    with pytest.raises(ValueError, match="captured"):
        verify_capture(row)


def test_export_tampering_fails_before_gpu_use(tmp_path):
    path, _ = bundle(tmp_path)
    with (path / "train.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="changed"):
        verify_bundle(path)


def test_smoke_and_training_keep_topology_and_use_all_validation(tmp_path):
    config = json.loads(CONFIG.read_text())
    manifest = {"counts": {"train": 24, "validation": 7}}

    def build(mode):
        return configuration(config, tmp_path, manifest, tmp_path, tmp_path, tmp_path, mode)

    smoke, train = build("smoke"), build("train")
    assert smoke["dpo"]["max_num_steps"] == 3
    assert train["dpo"]["max_num_steps"] == 60
    assert train["dpo"]["val_period"] == 6
    assert train["dpo"]["val_batches"] == -1
    assert train["policy"]["megatron_cfg"]["tensor_model_parallel_size"] == 2
    assert train["cluster"] == {"gpus_per_node": 4, "num_nodes": 1}
    assert train["data"]["default"]["dataset_name"] == "BinaryPreferenceDataset"
    assert train["policy"]["tokenizer"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert train["policy"]["megatron_cfg"]["peft"]["enabled"] is True
    assert not train["policy"]["sequence_packing"]["enabled"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantization", "nf4"),
        ("tensor_parallel", 4),
        ("max_epochs", 11),
        ("timeout_seconds", 9000),
    ],
)
def test_unsupported_configuration_fails(field, value, tmp_path):
    config = json.loads(CONFIG.read_text())
    config[field] = value
    with pytest.raises(ValueError):
        configuration(
            config, tmp_path, {"counts": {"train": 24}}, tmp_path, tmp_path, tmp_path, "train"
        )


def test_nondivisible_train_batch_must_not_silently_drop_rows(tmp_path):
    with pytest.raises(ValueError, match="drops partial"):
        configuration(
            json.loads(CONFIG.read_text()),
            tmp_path,
            {"counts": {"train": 23}},
            tmp_path,
            tmp_path,
            tmp_path,
            "train",
        )


@pytest.mark.parametrize("mode", ["smoke", "train", "restore-check"])
def test_fused_logprobs_disable_parameter_prefetch_in_every_mode(tmp_path, mode):
    cfg = configuration(
        json.loads(CONFIG.read_text()),
        tmp_path,
        {"counts": {"train": 28, "validation": 7}},
        tmp_path,
        tmp_path,
        tmp_path,
        mode,
    )
    validate_runtime_config(cfg)
    megatron = cfg["policy"]["megatron_cfg"]
    assert megatron["use_fused_linear_logprobs"] is True
    assert megatron["distributed_data_parallel_config"]["overlap_param_gather"] is False
    megatron["distributed_data_parallel_config"]["overlap_param_gather"] = True
    with pytest.raises(ValueError, match="overlap_param_gather=false"):
        validate_runtime_config(cfg)
    del megatron["distributed_data_parallel_config"]
    with pytest.raises(ValueError, match="explicit"):
        validate_runtime_config(cfg)


def test_tokenizer_only_snapshot_is_not_trainable(tmp_path):
    config = json.loads(CONFIG.read_text())
    (tmp_path / "healthops-snapshot.json").write_text(
        json.dumps(
            {
                "model_id": config["model_id"],
                "revision": config["model_revision"],
                "files": {"tokenizer.json": "hash"},
            }
        )
    )
    with pytest.raises(ValueError, match="Full HF weights"):
        verify_snapshot(tmp_path, config)


def test_timeout_stops_training_subprocess(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        bounded_run(
            [sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, 0.1, tmp_path / "log"
        )


def test_fingerprint_is_serialization_order_independent():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
