"""Inspect locally produced Megatron DCP state before retaining it for continuation."""

import argparse
import io
import json
from pathlib import Path


def state_checks(keys, common, step, global_batch_size):
    """Megatron stores optimizer, RNG and scheduler alongside weights, not in a new folder."""
    if isinstance(common, list):
        if len(common) != 1:
            raise ValueError("Unexpected common checkpoint shard layout")
        common = common[0]
    scheduler = common.get("opt_param_scheduler", {})
    checks = {
        "adam_first_moment": any(
            k.startswith("optimizer.") and k.endswith("exp_avg") for k in keys
        ),
        "adam_second_moment": any(
            k.startswith("optimizer.") and k.endswith("exp_avg_sq") for k in keys
        ),
        "rng_state": any(k.startswith("rng_state/") for k in keys),
        "scheduler": bool(scheduler),
        # NeMo DPO feeds both chosen and rejected sequences per preference pair.
        "scheduler_cursor": scheduler.get("num_steps") == step * global_batch_size * 2,
    }
    return {"state_preserved": all(checks.values()), "checks": checks, "scheduler": scheduler}


def inspect_checkpoint(checkpoint, step, global_batch_size):
    import torch
    from torch.distributed.checkpoint import FileSystemReader

    checkpoint = Path(checkpoint)
    root = checkpoint / "policy/weights/iter_0000000"
    metadata = FileSystemReader(str(root)).read_metadata()
    common = None
    for index, location in metadata.storage_data.items():
        if index.fqn.startswith("common_state/"):
            path = (root / location.relative_path).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError("Checkpoint shard escaped its directory")
            with path.open("rb") as stream:
                stream.seek(location.offset)
                # Only locally produced, trusted checkpoint files enter this worker.
                common = torch.load(
                    io.BytesIO(stream.read(location.length)), map_location="cpu", weights_only=False
                )
            break
    if common is None:
        raise ValueError("Checkpoint lacks common optimizer/scheduler state")
    result = state_checks(metadata.state_dict_metadata, common, step, global_batch_size)
    info = json.loads((checkpoint / "training_info.json").read_text())
    result["checks"].update(
        dataloader=(checkpoint / "train_dataloader.pt").is_file(),
        training_step=info["total_steps"] == step,
        consumed_samples=info["consumed_samples"] == step * global_batch_size,
    )
    result["state_preserved"] = all(result["checks"].values())
    result["scope"] = "Saved state presence and cursors; not a functional or bitwise resume test."
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--step", type=int, required=True)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_checkpoint(args.checkpoint, args.step, args.batch)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if not result["state_preserved"]:
        raise SystemExit("Required continuation state was not preserved")


if __name__ == "__main__":
    main()
