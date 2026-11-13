"""Protect the fixed-horizon LR experiment and deterministic presentation contract."""

import copy
import json
from pathlib import Path

import pytest

from writer_dpo import contract_v2 as c
from writer_dpo.checkpoint_state import state_checks
from writer_dpo.launch import configuration
from writer_dpo.presentation import presentation_packet


def test_unified_checkpoint_requires_moments_rng_and_correct_scheduler_cursor():
    keys = ["optimizer.x.exp_avg", "optimizer.x.exp_avg_sq", "rng_state/shard_0"]
    common = [{"opt_param_scheduler": {"num_steps": 496, "max_lr": 2e-6}}]
    assert state_checks(keys, common, 124, 2)["state_preserved"]
    assert not state_checks(keys[:-1], common, 124, 2)["state_preserved"]
    assert not state_checks(keys, common, 62, 2)["state_preserved"]
    assert not state_checks(keys, [{}], 124, 2)["state_preserved"]


def test_pause_does_not_shorten_schedule_or_remove_optimizer(tmp_path):
    config = json.loads(c.CONFIG.read_text())
    config.update(
        global_batch_size=2,
        max_steps=360,
        max_epochs=12,
        experiment_epoch_cap=12,
        stop_after_step=124,
        warmup_steps=10,
        keep_top_k=16,
    )
    manifest = {"counts": {"train": 62, "validation": 32}}
    paused = configuration(config, tmp_path, manifest, tmp_path, tmp_path, tmp_path, "train")
    config["stop_after_step"] = 360
    continued = configuration(config, tmp_path, manifest, tmp_path, tmp_path, tmp_path, "train")
    assert paused["dpo"].pop("stop_after_step") == 124
    assert continued["dpo"].pop("stop_after_step") == 360
    assert paused == continued
    assert paused["dpo"]["max_num_steps"] == 360
    assert paused["dpo"]["val_period"] == 31
    assert paused["dpo"]["sft_loss_weight"] == 0
    assert paused["checkpointing"]["save_optimizer"] is True
    assert paused["checkpointing"]["keep_top_k"] == 16
    assert paused["policy"]["megatron_cfg"]["scheduler"]["lr_warmup_iters"] == 10
    config["stop_after_step"] = 361
    with pytest.raises(ValueError, match="Stop step"):
        configuration(config, tmp_path, manifest, tmp_path, tmp_path, tmp_path, "train")


def test_display_values_cannot_change_canonical_evidence_or_supply_false_units():
    raw = {
        "today": "2026-12-14",
        "sources": [
            c.daily_table(
                "e1.healthkit",
                "sleep",
                "seconds",
                "healthkit",
                [("2026-12-13", 3590)],
                "interval_total_sum",
                today="2026-12-14",
            )
        ],
    }
    displayed = presentation_packet(raw, "en")
    assert c.raw_packet(displayed) == raw
    c.check_packet(displayed, "en")
    bad = copy.deepcopy(displayed)
    bad["sources"][0]["days"][0]["value_display"] = "30 hours"
    with pytest.raises(ValueError, match="Presentation fields"):
        c.check_packet(bad, "en")
    repo = Path(__file__).resolve().parents[3]
    assert (repo / "src/api-service/healthops/assistant/presentation.py").read_bytes() == (
        repo / "src/model-training/writer_dpo/presentation.py"
    ).read_bytes()
