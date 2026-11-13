"""Exercise PyTorch common-state decoding, with synthetic DCP metadata and no GPU."""

import io
import json
from types import SimpleNamespace

import pytest

from writer_dpo.checkpoint_state import inspect_checkpoint


@pytest.mark.parametrize("cursor,expected", [(496, True), (248, False)])
def test_common_state_uses_torch_serialization(tmp_path, monkeypatch, cursor, expected):
    import torch
    import torch.distributed.checkpoint as dcp
    from torch.distributed.checkpoint.metadata import MetadataIndex

    root = tmp_path / "policy/weights/iter_0000000"
    root.mkdir(parents=True)
    buffer = io.BytesIO()
    torch.save([{"opt_param_scheduler": {"num_steps": cursor, "max_lr": 2e-6}}], buffer)
    payload = buffer.getvalue()
    (root / "__0_0.distcp").write_bytes(b"padding" + payload)
    metadata = SimpleNamespace(
        state_dict_metadata={
            "optimizer.x.exp_avg": None,
            "optimizer.x.exp_avg_sq": None,
            "rng_state/shard_0": None,
        },
        storage_data={
            MetadataIndex("common_state/shard_0_1"): SimpleNamespace(
                relative_path="__0_0.distcp", offset=7, length=len(payload)
            )
        },
    )
    monkeypatch.setattr(
        dcp, "FileSystemReader", lambda path: SimpleNamespace(read_metadata=lambda: metadata)
    )
    (tmp_path / "training_info.json").write_text(
        json.dumps({"total_steps": 124, "consumed_samples": 248})
    )
    (tmp_path / "train_dataloader.pt").write_bytes(b"synthetic-presence-fixture")
    result = inspect_checkpoint(tmp_path, 124, 2)
    assert result["state_preserved"] is expected
    assert result["checks"]["scheduler_cursor"] is expected
