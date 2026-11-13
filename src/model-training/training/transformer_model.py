"""Transformer encoder on 14-day windows, direct quantile prediction for the last day."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn

from training.data import METRICS
from training.windows import DAY_FEATURES, WINDOW, Scaler, make_windows, quantile_frame

QUANTILES = (0.05, 0.5, 0.95)
DEFAULTS = {
    "d_model": 64,
    "heads": 4,
    "layers": 2,
    "ff": 128,
    "dropout": 0.1,
    "epochs": 30,
    "batch_size": 256,
    "lr": 1e-3,
    "patience": 5,
    "stride": 1,
    "window": WINDOW,  # days of context per prediction; 14 is the trained default
}


def pinball(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """pred [B, 3 metrics, 3 quantiles], target [B, 3]."""
    q = torch.tensor(QUANTILES, device=pred.device).view(1, 1, -1)
    diff = target.unsqueeze(-1) - pred
    return torch.maximum(q * diff, (q - 1) * diff).mean()


class Net(nn.Module):
    def __init__(self, cfg: dict):
        super().__init__()
        d = cfg["d_model"]
        self.embed = nn.Linear(DAY_FEATURES, d)
        self.position = nn.Parameter(torch.zeros(1, cfg["window"], d))
        layer = nn.TransformerEncoderLayer(
            d, cfg["heads"], cfg["ff"], cfg["dropout"], batch_first=True, norm_first=True
        )
        self.encoder = nn.TransformerEncoder(layer, cfg["layers"])
        self.head = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, len(METRICS) * len(QUANTILES)))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.encoder(self.embed(x) + self.position)
        return self.head(h[:, -1]).view(x.shape[0], len(METRICS), len(QUANTILES))


class TransformerModel:
    name = "transformer"

    def __init__(self, cfg: dict | None = None, device: str | None = None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.cfg["window"] = int(self.cfg["window"])  # JSON overrides may carry 30.0
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.net: Net | None = None
        self.scaler: Scaler | None = None

    def fit(self, daily: pd.DataFrame, log=None) -> dict:
        torch.manual_seed(0)
        self.scaler = Scaler(daily)
        window = self.cfg["window"]
        train = make_windows(
            daily, self.scaler, ("train",), stride=self.cfg["stride"], window=window
        )
        val = make_windows(daily, self.scaler, ("val",), window=window)
        self.net = Net(self.cfg).to(self.device)
        opt = torch.optim.AdamW(self.net.parameters(), lr=self.cfg["lr"], weight_decay=1e-4)
        x = torch.tensor(train["x"]).to(self.device)
        y = torch.tensor(train["y_last"]).to(self.device)
        xv = torch.tensor(val["x"]).to(self.device)
        yv = torch.tensor(val["y_last"]).to(self.device)
        best, best_state, bad, history = float("inf"), None, 0, []
        for epoch in range(self.cfg["epochs"]):
            self.net.train()
            order = torch.randperm(len(x), device=self.device)
            total = 0.0
            for start in range(0, len(x), self.cfg["batch_size"]):
                batch = order[start : start + self.cfg["batch_size"]]
                loss = pinball(self.net(x[batch]), y[batch])
                opt.zero_grad()
                loss.backward()
                opt.step()
                total += loss.item() * len(batch)
            self.net.eval()
            with torch.no_grad():
                val_loss = float(pinball(self.net(xv), yv)) if len(xv) else float("nan")
            history.append(
                {"epoch": epoch, "train_pinball": total / len(x), "val_pinball": val_loss}
            )
            if log:
                log(
                    {
                        "transformer/train_pinball": total / len(x),
                        "transformer/val_pinball": val_loss,
                        "epoch": epoch,
                    }
                )
            if val_loss < best:
                best, bad = val_loss, 0
                best_state = {k: v.detach().clone() for k, v in self.net.state_dict().items()}
            else:
                bad += 1
                if bad >= self.cfg["patience"]:
                    break
        if best_state:
            self.net.load_state_dict(best_state)
        return {"epochs_run": len(history), "best_val_pinball": best, "windows": int(len(x))}

    def predict(self, daily: pd.DataFrame) -> pd.DataFrame:
        splits = tuple(daily["split"].unique())
        windows = make_windows(daily, self.scaler, splits, window=self.cfg["window"])
        self.net.eval()
        out = []
        with torch.no_grad():
            x = torch.tensor(windows["x"]).to(self.device)
            for start in range(0, len(x), 2048):
                out.append(self.net(x[start : start + 2048]).cpu().numpy())
        q = np.concatenate(out) if out else np.zeros((0, len(METRICS), len(QUANTILES)))
        return quantile_frame(windows["meta"], q, self.scaler)

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        torch.save(self.net.state_dict(), folder / "transformer.pt")
        (folder / "meta.json").write_text(
            json.dumps({"scaler": self.scaler.stats, "cfg": self.cfg}, indent=2)
        )
