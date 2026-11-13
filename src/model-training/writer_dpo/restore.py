"""Functional checkpoint smoke: save, restart a process, restore and advance one step."""

import copy
import json
import os
import time
from pathlib import Path

import yaml

from writer_dpo.data import file_hash, write_json


def snapshot(checkpoint):
    path = Path(checkpoint)
    info = json.loads((path / "training_info.json").read_text())
    if not (path / "train_dataloader.pt").is_file():
        raise ValueError("Checkpoint has no saved dataloader state")
    files = {str(p.relative_to(path)): file_hash(p) for p in path.rglob("*") if p.is_file()}
    if not any(name.startswith("policy/weights/") for name in files):
        raise ValueError("Checkpoint has no policy weights")
    return {"path": str(path.resolve()), "state": info, "files": files}


def verify(before, after, audit, batch):
    a, b = before["state"], after["state"]
    checks = {
        "policy_initialized_after_restore": audit.get("policy_initialized") is True,
        "checkpoint_selected": audit.get("checkpoint_path") == before["path"],
        "weights_selected": audit.get("weights_path")
        == str(Path(before["path"]) / "policy/weights"),
        "optimizer_selected": audit.get("optimizer_path")
        == str(Path(before["path"]) / "policy/optimizer"),
        "step_restored": audit.get("loaded_state", {}).get("total_steps") == a["total_steps"],
        "sample_cursor_restored": audit.get("loaded_state", {}).get("consumed_samples")
        == a["consumed_samples"],
        "one_step_advanced": b["total_steps"] == a["total_steps"] + 1,
        "one_batch_consumed": b["consumed_samples"] == a["consumed_samples"] + batch,
        "weights_changed": {
            k: v for k, v in before["files"].items() if k.startswith("policy/weights/")
        }
        != {k: v for k, v in after["files"].items() if k.startswith("policy/weights/")},
    }
    return {
        "restore_verified": all(checks.values()),
        "checks": checks,
        "scope": "Functional restore and step advancement; not bitwise equivalence.",
    }


def run_restore_check(cfg, output, nemo_root, timeout, runner):
    if cfg["dpo"]["max_num_steps"] < 2:
        raise ValueError("Restore smoke needs a shared scheduler horizon of at least two steps")
    start = time.monotonic()
    before = None
    for stop in (1, 2):
        phase = copy.deepcopy(cfg)
        phase["dpo"]["stop_after_step"] = stop
        phase["checkpointing"]["save_period"] = 1
        phase["checkpointing"]["keep_top_k"] = 2
        # Keep scheduler horizon, seed, dataset and checkpoint directory identical.
        target = output / f"restore-phase-{stop}.yaml"
        target.write_text(yaml.safe_dump(phase, sort_keys=False))
        target.chmod(0o600)
        audit = output / f"restore-phase-{stop}-audit.json"
        env = dict(os.environ, HEALTHOPS_RESTORE_AUDIT=str(audit))
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            raise TimeoutError("Restore smoke exceeded the shared runtime budget")
        code = runner(
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
            nemo_root,
            remaining,
            output / f"restore-phase-{stop}.log",
            env=env,
        )
        if code:
            return code
        current = snapshot(Path(cfg["checkpointing"]["checkpoint_dir"]) / f"step_{stop}")
        if stop == 1:
            before = current
            write_json(output / "restore-before.json", before)
        else:
            report = verify(
                before,
                current,
                json.loads(audit.read_text()),
                cfg["policy"]["train_global_batch_size"],
            )
            report["before"] = before
            report["after"] = current
            write_json(output / "restore-check.json", report)
            return 0 if report["restore_verified"] else 1
    return 1
