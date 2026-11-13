"""Weights & Biases wrapper that degrades to offline or disabled without changing the run.

Mode: WANDB_MODE if set; otherwise "online" when WANDB_API_KEY is present, else "offline"
(runs are written under WANDB_DIR, default .local/wandb, and can be synced later with
`wandb sync`). Project and entity come from WANDB_PROJECT / WANDB_ENTITY.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


class Tracker:
    def __init__(
        self, name: str, config: dict, group: str | None = None, tags: list[str] | None = None
    ):
        # Compose passes unset variables as empty strings; W&B rejects "" as a mode or entity.
        for key in ("WANDB_MODE", "WANDB_API_KEY", "WANDB_ENTITY", "WANDB_PROJECT"):
            if os.environ.get(key) == "":
                del os.environ[key]
        self.enabled = os.environ.get("WANDB_MODE", "").lower() != "disabled"
        self.run = None
        if not self.enabled:
            return
        import wandb

        mode = os.environ.get("WANDB_MODE") or (
            "online" if os.environ.get("WANDB_API_KEY") else "offline"
        )
        wandb_dir = Path(os.environ.get("WANDB_DIR", ".local/wandb"))
        wandb_dir.mkdir(parents=True, exist_ok=True)
        self.run = wandb.init(
            project=os.environ.get("WANDB_PROJECT", "healthops-m3"),
            entity=os.environ.get("WANDB_ENTITY") or None,
            name=name,
            group=group,
            tags=tags or [],
            config=config,
            mode=mode,
            dir=str(wandb_dir),
        )
        self.mode = mode

    def log(self, metrics: dict) -> None:
        if self.run:
            self.run.log(metrics)

    def summary(self, values: dict) -> None:
        if self.run:
            for key, value in values.items():
                self.run.summary[key] = value

    def table(self, name: str, frame: pd.DataFrame) -> None:
        if self.run:
            import wandb

            self.run.log({name: wandb.Table(dataframe=frame.reset_index(drop=True))})

    def artifact(self, name: str, kind: str, path: Path) -> None:
        if self.run:
            import wandb

            artifact = wandb.Artifact(name, type=kind)
            if path.is_dir():
                artifact.add_dir(str(path))
            else:
                artifact.add_file(str(path))
            self.run.log_artifact(artifact)

    @property
    def url(self) -> str | None:
        return self.run.url if self.run and getattr(self.run, "url", None) else None

    def finish(self) -> None:
        if self.run:
            self.run.finish()
