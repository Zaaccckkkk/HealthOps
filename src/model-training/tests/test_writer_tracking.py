"""CPU tracking/restore contracts; no GPU, W&B server or GCP calls."""

import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from writer_dpo.restore import run_restore_check, snapshot, verify
from writer_dpo.tracking import inject_wandb_secret, logger_config
from writer_dpo.vertex import job_spec


def test_tracking_defaults_offline_and_never_serializes_credentials(tmp_path):
    logger, record = logger_config(tmp_path, {})
    assert logger["wandb_enabled"] and record["mode"] == "offline"
    assert record["run_id"] == logger_config(tmp_path, {})[1]["run_id"]
    with pytest.raises(ValueError, match="explicit"):
        logger_config(tmp_path, {"WANDB_MODE": "online"})
    env = {
        "WANDB_MODE": "online",
        "WANDB_ENTITY": "fixture-owner",
        "WANDB_API_KEY": "private-fixture",
    }
    result = logger_config(tmp_path, env)
    assert "private-fixture" not in json.dumps(result)
    assert result[0]["wandb"]["entity"] == "fixture-owner"
    assert not logger_config(tmp_path, {"WANDB_MODE": "disabled"})[0]["wandb_enabled"]


def test_pinned_secret_is_injected_only_into_memory():
    env = {}

    class Session:
        def get(self, url, timeout):
            assert url.endswith("/versions/1:access") and timeout == 30
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"payload": {"data": base64.b64encode(b"fixture-key").decode()}},
            )

    inject_wandb_secret("projects/fixture/secrets/wandb/versions/1", env, Session())
    assert env == {"WANDB_API_KEY": "fixture-key"}
    with pytest.raises(ValueError, match="pinned"):
        inject_wandb_secret("projects/fixture/secrets/wandb/versions/latest", {}, Session())


def test_cloud_tracking_separates_jobs_but_preserves_restart_identity(tmp_path):
    first = {"HEALTHOPS_RUN_IDENTITY": "gs://fixture/runs/smoke-1"}
    second = {"HEALTHOPS_RUN_IDENTITY": "gs://fixture/runs/smoke-2"}
    first_id = logger_config(tmp_path, first)[1]["run_id"]
    assert first_id != logger_config(tmp_path, second)[1]["run_id"]
    assert first_id == logger_config(tmp_path / "new-container", first)[1]["run_id"]


def make_checkpoint(root, step):
    path = root / f"step_{step}"
    weights = path / "policy/weights"
    weights.mkdir(parents=True)
    (weights / "fixture.bin").write_text(f"fixture state {step}")
    (path / "training_info.json").write_text(
        json.dumps({"total_steps": step, "consumed_samples": step * 4})
    )
    (path / "train_dataloader.pt").write_bytes(b"synthetic cursor")
    return snapshot(path)


def test_restore_requires_selected_state_and_actual_progress(tmp_path):
    before, after = make_checkpoint(tmp_path, 1), make_checkpoint(tmp_path, 2)
    audit = {
        "policy_initialized": True,
        "checkpoint_path": before["path"],
        "weights_path": str(Path(before["path"]) / "policy/weights"),
        "optimizer_path": str(Path(before["path"]) / "policy/optimizer"),
        "loaded_state": before["state"],
    }
    assert verify(before, after, audit, 4)["restore_verified"]
    for key, value in [
        ("optimizer_path", None),
        ("checkpoint_path", "wrong"),
        ("loaded_state", {"total_steps": 0, "consumed_samples": 0}),
        ("policy_initialized", False),
    ]:
        assert not verify(before, after, {**audit, key: value}, 4)["restore_verified"]
    assert not verify(before, before, audit, 4)["restore_verified"]


def test_restore_runner_keeps_scheduler_horizon_and_restarts_process(tmp_path):
    cfg = {
        "dpo": {"max_num_steps": 3},
        "policy": {"train_global_batch_size": 4},
        "checkpointing": {"checkpoint_dir": str(tmp_path / "checkpoints")},
    }
    calls = []

    def runner(command, cwd, timeout, log_path, env):
        phase = yaml.safe_load(Path(command[-1]).read_text())
        calls.append(copy.deepcopy(phase))
        step = phase["dpo"]["stop_after_step"]
        assert phase["dpo"]["max_num_steps"] == 3
        make_checkpoint(Path(phase["checkpointing"]["checkpoint_dir"]), step)
        if step == 2:
            before = snapshot(tmp_path / "checkpoints/step_1")
            audit = {
                "policy_initialized": True,
                "checkpoint_path": before["path"],
                "weights_path": str(Path(before["path"]) / "policy/weights"),
                "optimizer_path": str(Path(before["path"]) / "policy/optimizer"),
                "loaded_state": before["state"],
            }
            Path(env["HEALTHOPS_RESTORE_AUDIT"]).write_text(json.dumps(audit))
        return 0

    assert run_restore_check(cfg, tmp_path, tmp_path, 60, runner) == 0
    assert len(calls) == 2
    assert json.loads((tmp_path / "restore-check.json").read_text())["restore_verified"]
    assert "stop_after_step" not in cfg["dpo"]


def test_vertex_tracking_contains_secret_reference_not_key():
    args = (
        "registry/p/image@sha256:" + "a" * 64,
        "training@example.com",
        "gs://bucket/bundle",
        "b" * 64,
        "gs://bucket/model",
        "c" * 64,
        "gs://bucket/output",
    )
    with pytest.raises(ValueError, match="explicit"):
        job_spec(*args, wandb_mode="online")
    spec = job_spec(
        *args,
        mode="restore-check",
        wandb_mode="online",
        wandb_entity="fixture-owner",
        wandb_secret_version="projects/fixture/secrets/wandb/versions/1",
    )
    env = spec["jobSpec"]["workerPoolSpecs"][0]["containerSpec"]["env"]
    assert not any(row["name"] == "WANDB_API_KEY" for row in env)
    assert any(row["name"] == "HEALTHOPS_WANDB_SECRET_VERSION" for row in env)


def test_nemo_adapter_preserves_static_checkpoint_method(tmp_path, monkeypatch):
    import sys
    from dataclasses import dataclass
    from types import ModuleType

    from writer_dpo import nemo_entry

    @dataclass
    class State:
        total_steps: int = 1
        consumed_samples: int = 4

    class Manager:
        @staticmethod
        def get_resume_paths(path, *, model_component="policy"):
            return Path(path) / model_component / "weights", Path(
                path
            ) / model_component / "optimizer"

    modules = {
        name: ModuleType(name)
        for name in (
            "nemo_rl",
            "nemo_rl.algorithms",
            "nemo_rl.algorithms.dpo",
            "nemo_rl.utils",
            "nemo_rl.utils.checkpoint",
        )
    }
    dpo = modules["nemo_rl.algorithms.dpo"]
    modules["nemo_rl.algorithms"].dpo = dpo
    modules["nemo_rl.utils.checkpoint"].CheckpointManager = Manager
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def setup():
        Manager.get_resume_paths(tmp_path / "step_1")
        return (None,) * 7 + (State(), None)

    dpo.setup = setup
    audit = tmp_path / "audit.json"
    monkeypatch.setenv("HEALTHOPS_RESTORE_AUDIT", str(audit))
    monkeypatch.delenv("HEALTHOPS_RUN_RECORD", raising=False)
    monkeypatch.setattr(nemo_entry.runpy, "run_path", lambda *a, **kw: dpo.setup())
    monkeypatch.setattr(sys, "argv", ["fixture"])
    nemo_entry.main()
    result = json.loads(audit.read_text())
    assert result["loaded_state"] == {"total_steps": 1, "consumed_samples": 4}
    assert result["weights_path"] == str(tmp_path / "step_1/policy/weights")
    assert result["policy_initialized"] is True
