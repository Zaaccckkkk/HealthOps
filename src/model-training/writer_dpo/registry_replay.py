"""Generate final-test responses for an already frozen selection on an existing GPU.

This entry point never provisions GPUs, trains, chooses a checkpoint, or registers a model.
It writes the artifact-bound receipt consumed by writer_dpo.registry.finalize.
"""

import argparse
import json
import time
from pathlib import Path

from writer_dpo import registry
from writer_dpo.snapshot import verify_snapshot


def replay_inputs(experiment, snapshot, adapter):
    policy = registry.policy_for(experiment)
    selection = registry.select(experiment)
    if selection["selected"] == "base":
        raise ValueError("Base selected; no fine-tuned final comparison to register")
    config = registry.read(experiment / "training/R1-v6-config.json")
    verify_snapshot(snapshot, config)
    check_path = experiment / (
        "training/observed-output/generation/step" + selection["selected"] + "/adapter-check.json"
    )
    check = registry.read(check_path)
    if not check.get("finite") or not check.get("nonzero_lora_b"):
        raise ValueError("Adapter export was not verified")
    for name in ("adapter_config.json", "adapter_model.safetensors"):
        if registry.file_hash(adapter / name) != check["files"][name]:
            raise ValueError("Selected adapter changed before final generation")
    execution = registry.read(experiment / "training/execution-plan.json")
    if registry.file_hash(snapshot / "healthops-snapshot.json") != execution["snapshot_sha256"]:
        raise ValueError("Wrong original base snapshot")
    rows = [
        r for r in registry.read(experiment / "inventory124.json")["rows"] if r["split"] == "test"
    ]
    expected = policy["expected_inputs"]["test"]
    if len(rows) != len(expected) or {r["id"] for r in rows} != set(expected):
        raise ValueError("Final-test inventory mismatch")
    for row in rows:
        if registry.fingerprint(row["messages"]) != expected[row["id"]]:
            raise ValueError("Final-test input changed")
    receipt = {
        "completed": False,
        "selected": selection["selected"],
        "test_plan_sha256": registry.file_hash(experiment / "registry/final-test-plan.json"),
        "inputs": {
            "bundle": execution["bundle_sha256"],
            "snapshot": execution["snapshot_sha256"],
            "adapter_check": registry.file_hash(check_path),
        },
        "response_hashes": {},
    }
    return rows, receipt


def run(experiment, snapshot, adapter):
    rows, receipt = replay_inputs(experiment, snapshot, adapter)
    folder = experiment / "registry"
    destinations = [folder / f"final-test-{name}.jsonl" for name in ("base", "selected")]
    if any(p.exists() for p in destinations) or (folder / "generation-receipt.json").exists():
        raise ValueError("Final generations already exist; never silently overwrite or rerun")
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.manual_seed(42)
    tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
    model = (
        AutoModelForCausalLM.from_pretrained(
            snapshot, torch_dtype=torch.bfloat16, local_files_only=True, attn_implementation="sdpa"
        )
        .to("cuda")
        .eval()
    )
    for name, path in zip(("base", "selected"), destinations, strict=True):
        if name == "selected":
            model = PeftModel.from_pretrained(model, adapter, is_trainable=False).eval()
        with path.open("x") as stream:
            for row in rows:
                prompt = tokenizer.apply_chat_template(
                    row["messages"],
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
                batch = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to("cuda")
                length = batch["input_ids"].shape[1]
                if length + 800 > 32768:
                    raise ValueError("Final input exceeds context; do not truncate evidence")
                started = time.monotonic()
                with torch.inference_mode():
                    sequence = model.generate(
                        **batch,
                        max_new_tokens=800,
                        do_sample=False,
                        pad_token_id=tokenizer.eos_token_id,
                    )[0, length:]
                result = {
                    "id": row["id"],
                    "messages_sha256": registry.fingerprint(row["messages"]),
                    "raw_output": tokenizer.decode(sequence, skip_special_tokens=True),
                    "prompt_tokens": length,
                    "completion_tokens": len(sequence),
                    "seconds": time.monotonic() - started,
                    "training_eligible": False,
                }
                stream.write(json.dumps(result, ensure_ascii=True) + "\n")
                stream.flush()
        receipt["response_hashes"][path.name] = registry.file_hash(path)
    receipt.update(completed=True, backend="transformers_sdpa", seed=42, max_new_tokens=800)
    registry.write_once(folder / "generation-receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("experiment", "snapshot", "adapter"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(run(args.experiment.resolve(), args.snapshot.resolve(), args.adapter.resolve()))
    )


if __name__ == "__main__":
    main()
