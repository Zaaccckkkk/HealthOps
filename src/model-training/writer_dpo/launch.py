"""Prepare or execute a bounded, single-node four-GPU NeMo DPO job."""

import argparse
import json
import os
import signal
import subprocess
from pathlib import Path

import yaml

from writer_dpo.data import file_hash, fingerprint, read_rows, write_json
from writer_dpo.snapshot import CONFIG, verify_snapshot
from writer_dpo.tracking import logger_config, runtime_record


def verify_nemo(root, config):
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != config["nemo_revision"]:
        raise ValueError("NeMo checkout must match config.json, not floating main")
    for name, expected in config["nemo_files"].items():
        if file_hash(root / name) != expected:
            raise ValueError(f"Pinned upstream interface changed: {name}")


def validate_runtime_config(resolved):
    """Reject known incompatible settings after NeMo resolves inherited defaults."""
    megatron = resolved["policy"]["megatron_cfg"]
    if megatron.get("use_fused_linear_logprobs") and (
        megatron.get("distributed_data_parallel_config", {}).get("overlap_param_gather")
        is not False
    ):
        raise ValueError("Fused logprobs require explicit overlap_param_gather=false")


def trainable_prompt_sha256(config, manifest):
    """v1 bundles are bound to the archived pin; v2 bundles to the registered current prompt."""
    if manifest.get("schema") == "healthops-writer-dpo-v2":
        prompts, current = config["writer_prompts"], config["writer_system_current"]
        if manifest.get("prompt_version") != current:
            raise ValueError("Approved bundle uses a different writer prompt version")
        return prompts[current]["fingerprint"]
    return config["writer_system_sha256"]


def verify_bundle(path):
    manifest = json.loads((path / "manifest.json").read_text())
    if (
        manifest.get("schema") not in {"healthops-writer-dpo-v1", "healthops-writer-dpo-v2"}
        or manifest.get("approved") is not True
        or manifest.get("data_kind") != "synthetic_development"
        or set(manifest.get("files", {})) != {"train.jsonl", "validation.jsonl"}
    ):
        raise ValueError("Only an approved synthetic development bundle is trainable")
    for split in ("train", "validation"):
        name = f"{split}.jsonl"
        if file_hash(path / name) != manifest["files"][name]:
            raise ValueError("Training bundle changed after approval")
        rows = read_rows(path / name)
        approved = [r for r in manifest["pairs"] if r["split"] == split]
        if not rows or len(rows) != manifest["counts"][split] or len(rows) != len(approved):
            raise ValueError("Bundle row count mismatch")
        key = "export_sha256" if manifest["schema"] == "healthops-writer-dpo-v2" else "pair_sha256"
        if [fingerprint(r) for r in rows] != [r[key] for r in approved]:
            raise ValueError("Export differs from reviewed pairs")
    return manifest


def configuration(config, bundle, manifest, nemo_root, snapshot, output, mode):
    if config["quantization"] is not None or config["precision"] != "bfloat16":
        raise ValueError("This verified integration is BF16 LoRA, not QLoRA")
    if (
        config["tensor_parallel"],
        config["data_parallel"],
        config["pipeline_parallel"],
        config["context_parallel"],
        config["gpus"],
    ) != (2, 2, 1, 1, 4):
        raise ValueError("This pilot fixes TP=2, DP=2, PP=1, CP=1 on four GPUs")
    batch = config["global_batch_size"]
    if batch % (config["micro_batch_size"] * config["data_parallel"]):
        raise ValueError("Global batch must be divisible by micro batch times DP")
    if manifest["counts"]["train"] % batch:
        raise ValueError("NeMo drops partial training batches; choose a batch dividing the dataset")
    epoch_cap = config.get("experiment_epoch_cap", 10)
    if (
        not 1 <= epoch_cap <= 12
        or not 1 <= config["max_epochs"] <= epoch_cap
        or not 1 <= config["timeout_seconds"] <= 7200
    ):
        raise ValueError("Epoch/runtime cap exceeded")
    steps_per_epoch = manifest["counts"]["train"] // batch
    steps = min(config["max_steps"], steps_per_epoch * config["max_epochs"])
    if mode in {"smoke", "restore-check"}:
        steps = min(steps, config["smoke_steps"])
    if steps < 1:
        raise ValueError("No optimizer steps")
    stop = config.get("stop_after_step") if mode == "train" else None
    if stop is not None and not 1 <= stop <= steps:
        raise ValueError("Stop step must lie within the fixed scheduler horizon")
    warmup = config.get("warmup_steps", 1) if mode == "train" else 1
    keep = config.get("keep_top_k", 2)
    if not 0 <= warmup < steps or not 1 <= keep <= 16:
        raise ValueError("Warmup/checkpoint retention exceeds experiment bounds")
    result = {
        "defaults": str(nemo_root / "examples/configs/dpo.yaml"),
        "dpo": {
            "max_num_epochs": config["max_epochs"],
            "max_num_steps": steps,
            "seed": config["seed"],
            "val_period": steps if mode == "smoke" else steps_per_epoch,
            "val_batches": -1,
            "val_global_batch_size": 2,
            "val_micro_batch_size": 1,
            "val_at_start": True,
            "val_at_end": True,
            "reference_policy_kl_penalty": config["beta"],
            "preference_loss_weight": 1.0,
            "sft_loss_weight": 0.0,
            "preference_average_log_probs": False,
        },
        "policy": {
            "model_name": str(snapshot),
            "tokenizer": {
                "name": str(snapshot),
                "chat_template_kwargs": {"enable_thinking": False},
            },
            "precision": "bfloat16",
            "train_global_batch_size": batch,
            "train_micro_batch_size": config["micro_batch_size"],
            "max_total_sequence_length": config["max_sequence_length"],
            "make_sequence_length_divisible_by": 2,
            "automodel_cfg": {"enabled": False},
            "sequence_packing": {"enabled": False},
            "dynamic_batching": {"enabled": False},
            "megatron_cfg": {
                "enabled": True,
                "tensor_model_parallel_size": 2,
                "pipeline_model_parallel_size": 1,
                "context_parallel_size": 1,
                "sequence_parallel": True,
                "activation_checkpointing": True,
                "use_fused_linear_logprobs": True,
                # NeMo's fused forward bypasses the parameter-prefetch hook.
                "distributed_data_parallel_config": {"overlap_param_gather": False},
                "fp8_cfg": {"enabled": False},
                "peft": {
                    "enabled": True,
                    "dim": config["lora_rank"],
                    "alpha": config["lora_alpha"],
                    "dropout": 0.0,
                    "target_modules": ["linear_qkv", "linear_proj", "linear_fc1", "linear_fc2"],
                },
                "optimizer": {"lr": config["learning_rate"], "min_lr": config["learning_rate"]},
                "scheduler": {
                    "lr_decay_style": "constant",
                    "lr_warmup_iters": warmup,
                    "lr_warmup_init": 1e-8,
                },
            },
        },
        "data": {
            "_override_": True,
            "max_input_seq_length": config["max_sequence_length"],
            "shuffle": True,
            "num_workers": 0,
            "default": {
                "dataset_name": "BinaryPreferenceDataset",
                "prompt_key": "prompt",
                "chosen_key": "chosen",
                "rejected_key": "rejected",
                "prompt_file": None,
                "system_prompt_file": None,
            },
            "train": {"data_path": str(bundle / "train.jsonl")},
            "validation": {"data_path": str(bundle / "validation.jsonl")},
        },
        "checkpointing": {
            "enabled": True,
            "checkpoint_dir": str(output / "checkpoints"),
            "save_period": steps if mode == "smoke" else steps_per_epoch,
            "keep_top_k": keep,
            "save_optimizer": True,
            "metric_name": "val:validation-default_loss",
            "higher_is_better": False,
        },
        "logger": logger_config(output)[0],
        "cluster": {"num_nodes": 1, "gpus_per_node": 4},
    }
    if stop is not None:
        result["dpo"]["stop_after_step"] = stop
    return result


def preflight_data(resolved):
    """Use the actual NeMo tokenizer/processor; refuse silently masked long examples."""
    from nemo_rl.algorithms.utils import get_tokenizer
    from nemo_rl.data.utils import setup_preference_data

    tokenizer = get_tokenizer(resolved["policy"]["tokenizer"])
    train, validation = setup_preference_data(tokenizer, resolved["data"])
    counts = {}
    for split, dataset in (("train", train), ("validation", validation)):
        datasets = dataset if isinstance(dataset, dict) else {"default": dataset}
        lengths = []
        for subset in datasets.values():
            for index in range(len(subset)):
                item = subset[index]
                if float(item["loss_multiplier"]) != 1.0:
                    raise ValueError(
                        "NeMo would mask an overlength example; increase context explicitly"
                    )
                lengths.append(max(int(item["length_chosen"]), int(item["length_rejected"])))
        counts[split] = {"rows": len(lengths), "max_tokens": max(lengths)}
    return counts


def bounded_run(command, cwd, timeout, log_path, *, env=None):
    # Run in a dedicated container: timeout kills this group; container exit stops Ray workers.
    with log_path.open("x") as log:
        log_path.chmod(0o600)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "nemo-root", "snapshot", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--mode", choices=["smoke", "train", "restore-check"], default="smoke")
    parser.add_argument("--config-file", type=Path, default=CONFIG)
    parser.add_argument(
        "--execute", action="store_true", help="Run on existing GPUs; never provisions"
    )
    args = parser.parse_args()
    os.environ.setdefault("WANDB_MODE", "offline")
    for name in ("bundle", "nemo_root", "snapshot", "output"):
        setattr(args, name, getattr(args, name).resolve())
    if ".local" not in args.output.parts:
        raise ValueError("Training outputs must be under .local")
    config = json.loads(args.config_file.read_text())
    verify_nemo(args.nemo_root, config)
    manifest = verify_bundle(args.bundle)
    if manifest["system_prompt_sha256"] != trainable_prompt_sha256(config, manifest):
        raise ValueError("Approved bundle uses a different writer prompt version")
    snapshot = verify_snapshot(args.snapshot, config)
    cfg = configuration(
        config, args.bundle, manifest, args.nemo_root, args.snapshot, args.output, args.mode
    )
    args.output.mkdir(parents=True, exist_ok=False)
    target = args.output / "dpo.yaml"
    target.write_text(yaml.safe_dump(cfg, sort_keys=False))
    target.chmod(0o600)
    write_json(
        args.output / "run.json",
        {
            "status": "prepared_not_run",
            "mode": args.mode,
            "config": config,
            "bundle_sha256": file_hash(args.bundle / "manifest.json"),
            "snapshot_manifest_sha256": fingerprint(snapshot),
            "config_sha256": file_hash(target),
            "tracking": logger_config(args.output)[1],
            "runtime": runtime_record(),
            "healthops_source_sha256": {
                p.name: file_hash(p) for p in Path(__file__).parent.glob("*.py")
            },
        },
    )
    if not args.execute:
        print("Configuration prepared; GPU preflight and training have NOT run")
        return
    # Run this module inside the pinned NeMo environment, not the CPU reconciliation venv.
    from nemo_rl.utils.config import load_config
    from omegaconf import OmegaConf

    resolved = OmegaConf.to_container(load_config(target), resolve=True)
    validate_runtime_config(resolved)
    report = preflight_data(resolved)
    if any(report[s]["rows"] != manifest["counts"][s] for s in report):
        raise ValueError("NeMo preprocessing changed the dataset counts")
    import torch

    if torch.cuda.device_count() != 4 or not torch.cuda.is_bf16_supported():
        raise ValueError("Exactly four visible BF16-capable GPUs are required")
    write_json(args.output / "preflight.json", report)
    write_json(
        args.output / "hardware.json",
        {
            "cuda": torch.version.cuda,
            "devices": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
        },
    )
    os.environ["HEALTHOPS_RUN_RECORD"] = str(args.output / "run.json")
    if args.mode == "restore-check":
        from writer_dpo.restore import run_restore_check

        code = run_restore_check(
            cfg, args.output, args.nemo_root, config["timeout_seconds"], bounded_run
        )
    else:
        code = bounded_run(
            [
                "uv",
                "run",
                "--frozen",
                "python",
                "-m",
                "writer_dpo.nemo_entry",
                "--config",
                str(target),
            ],
            args.nemo_root,
            config["timeout_seconds"],
            args.output / "training.log",
        )
    write_json(args.output / "exit.json", {"returncode": code, "training_completed": code == 0})
    raise SystemExit(code)


if __name__ == "__main__":
    main()
