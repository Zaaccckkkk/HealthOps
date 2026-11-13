"""Verify a completed run, export its adapter, and replay frozen synthetic inputs.

Run cloud orchestration in the isolated I/O environment. GPU stages use the same
pinned Megatron worker environment as training; no packages are installed at runtime.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

from writer_dpo.cloud import artifact_files, claim_output, fetch_manifest, gcs_path, safe_file
from writer_dpo.data import file_hash, write_json

CONVERTER_SHA256 = "6a16b0532aed090ee3a788f231ae3063350ac2527d58cfe02170844dcd1a855d"
WORKER_PYTHON = (
    "/opt/ray_venvs/nemo_rl.models.policy.workers.megatron_policy_worker."
    "MegatronPolicyWorker/bin/python"
)
FROZEN = {
    "holdout.jsonl": ("74123deb9030526e799dfcec4923544a37bf0fd0af117fcdd6d4257fd5582710", 11),
    "duration.jsonl": ("da41bbeeb206ff0a60f3a3f69011953874c4e9a97b169efc0d047aadfaf6273a", 2),
}


def checkpoint_files(receipt, step, inputs):
    if not (
        receipt.get("completed") is True
        and receipt.get("artifacts_synced") is True
        and receipt.get("returncode") == 0
        and receipt.get("inputs") == inputs
    ):
        raise ValueError("Training did not complete with the approved inputs")
    prefix = f"checkpoints/step_{step}/"
    files = {
        k[len(prefix) :]: v for k, v in receipt["checkpoint_files"].items() if k.startswith(prefix)
    }
    for name in files:
        safe_file(Path("/checkpoint"), name)
    if not {"config.yaml", "training_info.json"} <= files.keys() or not any(
        name.endswith(".distcp") for name in files
    ):
        raise ValueError("Selected checkpoint is incomplete")
    return files


def load_replays(folder):
    rows = []
    for name, (digest, count) in FROZEN.items():
        path = folder / name
        if file_hash(path) != digest:
            raise ValueError("Frozen test inputs changed")
        group = [json.loads(line) for line in path.read_text().splitlines()]
        if len(group) != count:
            raise ValueError("Frozen test input count changed")
        for row in group:
            encoded = json.dumps(
                row["messages"], sort_keys=True, ensure_ascii=True, separators=(",", ":")
            )
            if (
                row["split"] != "heldout_test"
                or row["training_eligible"] is not False
                or row["tools"]
                or hashlib.sha256(encoded.encode()).hexdigest() != row["messages_sha256"]
            ):
                raise ValueError("Invalid test provenance")
        rows.extend(group)
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate replay IDs")
    return rows


def gpu_stage(stage, work):
    import torch

    torch.manual_seed(42)
    snapshot = work / "snapshot"
    output = work / "output"
    if stage == "base-convert":
        from megatron.bridge.training.model_load_save import temporary_distributed_context
        from nemo_rl.models.megatron.community_import import import_model_from_hf_name

        with temporary_distributed_context(backend="gloo"):
            import_model_from_hf_name(
                str(snapshot),
                str(work / "base-megatron"),
                megatron_config={
                    "tensor_model_parallel_size": 1,
                    "pipeline_model_parallel_size": 1,
                    "context_parallel_size": 1,
                    "expert_tensor_parallel_size": 1,
                    "expert_model_parallel_size": 1,
                    "pipeline_dtype": "bfloat16",
                    "sequence_parallel": False,
                    "gradient_accumulation_fusion": False,
                    "num_layers_in_first_pipeline_stage": None,
                    "num_layers_in_last_pipeline_stage": None,
                },
            )
        return
    if stage == "adapter-export":
        path = Path("/opt/nemo-rl/examples/converters/convert_lora_to_hf.py")
        if file_hash(path) != CONVERTER_SHA256:
            raise ValueError("Pinned upstream converter changed")
        spec = importlib.util.spec_from_file_location("pinned_lora_converter", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.export_lora_adapter_to_hf(
            str(work / "base-megatron/iter_0000000"),
            str(work / "checkpoint/policy/weights/iter_0000000"),
            str(snapshot),
            str(output / "adapter"),
        )
        from safetensors.torch import load_file

        weights = load_file(str(output / "adapter/adapter_model.safetensors"))
        if not weights or not all(torch.isfinite(t).all() for t in weights.values()):
            raise ValueError("Adapter export is empty or non-finite")
        if not any("lora_B" in k and torch.count_nonzero(t) > 0 for k, t in weights.items()):
            raise ValueError("Exported adapter has no learned LoRA B weights")
        write_json(
            output / "adapter-check.json",
            {
                "finite": True,
                "nonzero_lora_b": True,
                "tensor_count": len(weights),
                "files": {
                    p.name: file_hash(p) for p in (output / "adapter").iterdir() if p.is_file()
                },
                "scope": "Export structure and learned weights; not numerical equivalence.",
            },
        )
        return
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    rows = load_replays(work / "evaluation")
    tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
    model = (
        AutoModelForCausalLM.from_pretrained(
            snapshot,
            torch_dtype=torch.bfloat16,
            local_files_only=True,
            attn_implementation="sdpa",
        )
        .to("cuda")
        .eval()
    )
    if stage == "adapted":
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, output / "adapter", is_trainable=False).eval()
    elif stage != "base":
        raise ValueError("Unknown GPU stage")
    result_path = output / f"{stage}-responses.jsonl"
    with result_path.open("x") as stream:
        for row in rows:
            prompt = tokenizer.apply_chat_template(
                row["messages"],
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            batch = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to("cuda")
            length = batch["input_ids"].shape[1]
            if length + 800 > 8192:
                raise ValueError("Frozen input exceeds context bound; never truncate evidence")
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
                "messages_sha256": row["messages_sha256"],
                "raw_output": tokenizer.decode(sequence, skip_special_tokens=True),
                "prompt_tokens": length,
                "completion_tokens": len(sequence),
                "seconds": time.monotonic() - started,
                "training_eligible": False,
                "semantic_verdict": "pending",
                "review_reason": "",
            }
            stream.write(json.dumps(result, ensure_ascii=True) + "\n")
            stream.flush()
            print(f"Completed {stage} replay {row['id']}", flush=True)
    write_json(
        output / f"{stage}-runtime.json",
        {
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "gpu": torch.cuda.get_device_name(),
            "precision": "bfloat16",
            "backend": "transformers_sdpa",
            "do_sample": False,
            "max_new_tokens": 800,
            "enable_thinking": False,
            "seed": 42,
            "count": len(rows),
        },
    )


def cloud_run(args):
    from google.cloud import storage

    from writer_dpo.cloud import sync_results, upload_results

    client = storage.Client()
    work = args.workdir
    claim_output(client, args.output_uri, "posttrain-export-replay")
    work.mkdir(parents=True, exist_ok=False)
    output = work / "output"
    output.mkdir()
    fetch_manifest(client, args.plan_uri, args.plan_sha256, work / "evaluation", "manifest.json")
    plan = json.loads((work / "evaluation/plan.json").read_text())
    load_replays(work / "evaluation")
    fetch_manifest(
        client,
        plan["snapshot_uri"],
        plan["inputs"]["snapshot"],
        work / "snapshot",
        "healthops-snapshot.json",
    )
    bucket, prefix = gcs_path(plan["training_uri"])
    receipt_path = work / "training-receipt.json"
    client.bucket(bucket).blob(prefix + "/receipt.json").download_to_filename(receipt_path)
    if file_hash(receipt_path) != plan["receipt_sha256"]:
        raise ValueError("Training receipt changed after candidate selection")
    files = checkpoint_files(json.loads(receipt_path.read_text()), plan["step"], plan["inputs"])
    for name, digest in files.items():
        target = safe_file(work / "checkpoint", name)
        target.parent.mkdir(parents=True, exist_ok=True)
        client.bucket(bucket).blob(
            f"{prefix}/checkpoints/step_{plan['step']}/{name}"
        ).download_to_filename(target)
        if file_hash(target) != digest:
            raise ValueError("Checkpoint file failed integrity verification")
    write_json(output / "provenance.json", plan)
    started = time.monotonic()
    previous = {}
    code = 1
    try:
        for stage in ("base-convert", "adapter-export", "base", "adapted"):
            with (output / f"{stage}.log").open("w") as log:
                process = subprocess.Popen(
                    [
                        WORKER_PYTHON,
                        "-m",
                        "writer_dpo.posttrain",
                        "--stage",
                        stage,
                        "--workdir",
                        str(work),
                    ],
                    cwd="/opt/nemo-rl",
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    while process.poll() is None:
                        if time.monotonic() - started > 5400:
                            raise TimeoutError("Post-training execution exceeded 90 minutes")
                        sync_results(client, output, args.output_uri, previous)
                        time.sleep(10)
                    if process.returncode:
                        raise RuntimeError(
                            f"Stage {stage} failed with exit {process.returncode}; inspect its log"
                        )
                finally:
                    if process.poll() is None:
                        import signal

                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
        code = 0
    finally:
        upload_results(client, output, args.output_uri, {})
        result = {
            "completed": code == 0,
            "returncode": code,
            "semantic_review": "pending",
            "files": {str(p.relative_to(output)): file_hash(p) for p in artifact_files(output)},
            "scope": "Frozen writer replay only; no full-chain or production deployment.",
        }
        write_json(output / "receipt.json", result)
        bucket, prefix = gcs_path(args.output_uri)
        client.bucket(bucket).blob(prefix + "/receipt.json").upload_from_filename(
            output / "receipt.json"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["base-convert", "adapter-export", "base", "adapted"])
    parser.add_argument("--workdir", type=Path, default=Path("/workspace/.local/posttrain"))
    parser.add_argument("--plan-uri")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--output-uri")
    args = parser.parse_args()
    if args.stage:
        gpu_stage(args.stage, args.workdir)
    else:
        if not all((args.plan_uri, args.plan_sha256, args.output_uri)):
            parser.error("Cloud mode requires immutable plan and separate output URI")
        if gcs_path(args.output_uri) == gcs_path(args.plan_uri):
            parser.error("Output must not overwrite the evaluation plan")
        cloud_run(args)


if __name__ == "__main__":
    main()
