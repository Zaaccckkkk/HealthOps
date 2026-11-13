"""Run the pinned upstream entry point with an optional post-setup restore audit."""

import json
import os
import runpy
import sys
from dataclasses import asdict
from pathlib import Path

from writer_dpo.data import write_json


def main():
    import nemo_rl.algorithms.dpo as dpo
    from nemo_rl.utils.checkpoint import CheckpointManager

    audit_path = os.environ.get("HEALTHOPS_RESTORE_AUDIT")
    if audit_path or os.environ.get("HEALTHOPS_RUN_RECORD"):
        observed = {}
        original_paths = CheckpointManager.get_resume_paths
        original_setup = dpo.setup

        def resume_paths(checkpoint_path, *args, **kwargs):
            weights, optimizer = original_paths(checkpoint_path, *args, **kwargs)
            observed.update(
                checkpoint_path=str(checkpoint_path) if checkpoint_path else None,
                weights_path=str(weights) if weights else None,
                optimizer_path=str(optimizer) if optimizer else None,
            )
            return weights, optimizer

        def setup(*args, **kwargs):
            result = original_setup(*args, **kwargs)
            record = os.environ.get("HEALTHOPS_RUN_RECORD")
            if record:
                result[5].log_hyperparams({"healthops": json.loads(Path(record).read_text())})
            # Pinned DPO setup returns the save state at index seven, after model
            # initialization and dataloader restoration both completed successfully.
            observed.update(policy_initialized=True, loaded_state=asdict(result[7]))
            if audit_path:
                write_json(Path(audit_path), observed)
            return result

        CheckpointManager.get_resume_paths = staticmethod(resume_paths)
        dpo.setup = setup
    entry = Path.cwd() / "examples/run_dpo.py"
    sys.argv = [str(entry), *sys.argv[1:]]
    runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
