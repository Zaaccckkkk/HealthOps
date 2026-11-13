"""Joint conditional diffusion over [truth, Apple Watch, Fitbit] windows, with inpainting.

The model learns the joint distribution of the three channels of every metric over a 14-day
window, conditioned on the device masks, the context and the day of week. At inference the
observed device channels are held fixed (inpainting) and the truth channels are sampled with
DDIM; the quantiles of the samples on the last day are the estimate and its interval. Sampling
both device channels from the same truth is the "two devices, same seed" view; inpainting is
the reconciliation view. Both come from one network.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn

from training.data import METRICS
from training.windows import DAY_FEATURES, Scaler, make_windows, quantile_frame

DEFAULTS = {
    "hidden": 64,
    "blocks": 3,
    "timesteps": 1000,
    "epochs": 40,
    "batch_size": 256,
    "lr": 2e-4,
    "ema": 0.999,
    "ddim_steps": 50,
    "samples": 50,
    "stride": 7,
    "patience": 8,
    "sample_batch": 4096,
}
DATA_CHANNELS = 3 * len(METRICS)  # truth, Apple, Fitbit per metric
X0_CLIP = 4.0  # standardised units; bounds the x0 estimate at the noisiest steps
COND_CHANNELS = DAY_FEATURES - 2 * len(METRICS)  # everything in the day features except readings


def cosine_alphas(timesteps: int) -> torch.Tensor:
    """Cumulative alphas of the cosine schedule (Nichol and Dhariwal, 2021)."""
    steps = torch.arange(timesteps + 1, dtype=torch.float64) / timesteps
    f = torch.cos((steps + 0.008) / 1.008 * math.pi / 2) ** 2
    alphas_bar = (f / f[0])[1:]
    return alphas_bar.clamp(1e-4, 0.9999).float()


def time_embedding(t: torch.Tensor, dim: int) -> torch.Tensor:
    half = dim // 2
    freq = torch.exp(-math.log(10_000) * torch.arange(half, device=t.device) / half)
    arg = t.float().unsqueeze(1) * freq.unsqueeze(0)
    return torch.cat([arg.sin(), arg.cos()], dim=1)


class ResBlock(nn.Module):
    def __init__(self, channels: int, time_dim: int):
        super().__init__()
        self.norm1 = nn.GroupNorm(8, channels)
        self.conv1 = nn.Conv1d(channels, channels, 3, padding=1)
        self.time = nn.Linear(time_dim, channels)
        self.norm2 = nn.GroupNorm(8, channels)
        self.conv2 = nn.Conv1d(channels, channels, 3, padding=1)

    def forward(self, h: torch.Tensor, t_emb: torch.Tensor) -> torch.Tensor:
        out = self.conv1(nn.functional.silu(self.norm1(h))) + self.time(t_emb).unsqueeze(-1)
        out = self.conv2(nn.functional.silu(self.norm2(out)))
        return h + out


class Net(nn.Module):
    def __init__(self, cfg: dict):
        super().__init__()
        hidden, time_dim = cfg["hidden"], cfg["hidden"] * 2
        self.time_mlp = nn.Sequential(
            nn.Linear(hidden, time_dim), nn.SiLU(), nn.Linear(time_dim, time_dim)
        )
        self.conv_in = nn.Conv1d(DATA_CHANNELS + COND_CHANNELS, hidden, 3, padding=1)
        self.blocks = nn.ModuleList([ResBlock(hidden, time_dim) for _ in range(cfg["blocks"])])
        self.conv_out = nn.Sequential(
            nn.GroupNorm(8, hidden), nn.SiLU(), nn.Conv1d(hidden, DATA_CHANNELS, 3, padding=1)
        )
        self.hidden = hidden

    def forward(self, x: torch.Tensor, t: torch.Tensor, cond: torch.Tensor) -> torch.Tensor:
        t_emb = self.time_mlp(time_embedding(t, self.hidden))
        h = self.conv_in(torch.cat([x, cond], dim=1))
        for block in self.blocks:
            h = block(h, t_emb)
        return self.conv_out(h)


def _split(windows: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """x0 [N, 9, L] (truth then observed channels), cond [N, C, L], known mask [N, 9, L]."""
    truth = windows["y_seq"].transpose(0, 2, 1)
    obs = windows["obs_seq"].transpose(0, 2, 1)
    mask = windows["mask_seq"].transpose(0, 2, 1)
    x0 = np.concatenate([truth, obs], axis=1)
    cond = windows["x"][:, :, 2 * len(METRICS) :].transpose(0, 2, 1)  # masks, context, dow, valid
    known = np.concatenate([np.zeros_like(truth), mask], axis=1)
    return x0.astype(np.float32), cond.astype(np.float32), known.astype(np.float32)


class DiffusionModel:
    name = "diffusion"

    def __init__(self, cfg: dict | None = None, device: str | None = None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.net: Net | None = None
        self.ema: Net | None = None
        self.scaler: Scaler | None = None
        self.alphas_bar = cosine_alphas(self.cfg["timesteps"])

    def _loss(self, net: Net, x0: torch.Tensor, cond: torch.Tensor) -> torch.Tensor:
        t = torch.randint(0, self.cfg["timesteps"], (x0.shape[0],), device=self.device)
        ab = self.alphas_bar.to(self.device)[t].view(-1, 1, 1)
        noise = torch.randn_like(x0)
        xt = ab.sqrt() * x0 + (1 - ab).sqrt() * noise
        return nn.functional.mse_loss(net(xt, t, cond), noise)

    def fit(self, daily: pd.DataFrame, log=None) -> dict:
        torch.manual_seed(0)
        self.scaler = Scaler(daily)
        train = make_windows(daily, self.scaler, ("train",), stride=self.cfg["stride"])
        val = make_windows(daily, self.scaler, ("val",), stride=self.cfg["stride"])
        x0, cond, _ = _split(train)
        xv, cv, _ = _split(val)
        x0, cond = torch.tensor(x0).to(self.device), torch.tensor(cond).to(self.device)
        xv, cv = torch.tensor(xv).to(self.device), torch.tensor(cv).to(self.device)
        self.net = Net(self.cfg).to(self.device)
        self.ema = Net(self.cfg).to(self.device)
        self.ema.load_state_dict(self.net.state_dict())
        opt = torch.optim.AdamW(self.net.parameters(), lr=self.cfg["lr"])
        best, bad, history = float("inf"), 0, []
        for epoch in range(self.cfg["epochs"]):
            self.net.train()
            order = torch.randperm(len(x0), device=self.device)
            total = 0.0
            for start in range(0, len(x0), self.cfg["batch_size"]):
                batch = order[start : start + self.cfg["batch_size"]]
                loss = self._loss(self.net, x0[batch], cond[batch])
                opt.zero_grad()
                loss.backward()
                opt.step()
                with torch.no_grad():
                    for p_ema, p in zip(self.ema.parameters(), self.net.parameters()):
                        p_ema.mul_(self.cfg["ema"]).add_(p, alpha=1 - self.cfg["ema"])
                total += loss.item() * len(batch)
            self.ema.eval()
            with torch.no_grad():
                torch.manual_seed(1)  # same noise draws every epoch: comparable validation losses
                val_loss = float(self._loss(self.ema, xv, cv)) if len(xv) else float("nan")
            history.append({"epoch": epoch, "train_mse": total / len(x0), "val_mse": val_loss})
            if log:
                log(
                    {
                        "diffusion/train_mse": total / len(x0),
                        "diffusion/val_mse": val_loss,
                        "epoch": epoch,
                    }
                )
            if val_loss < best:
                best, bad = val_loss, 0
            else:
                bad += 1
                if bad >= self.cfg["patience"]:
                    break
        return {"epochs_run": len(history), "best_val_mse": best, "windows": int(len(x0))}

    @torch.no_grad()
    def _sample(
        self, x0: torch.Tensor, cond: torch.Tensor, known: torch.Tensor, n_samples: int
    ) -> torch.Tensor:
        """DDIM (eta = 0) with the known entries re-imposed at every step; returns the truth
        channels of the last day: [n_samples, N, 3]."""
        net = self.ema
        steps = self.cfg["ddim_steps"]
        schedule = torch.linspace(self.cfg["timesteps"] - 1, 0, steps, device=self.device).long()
        ab_all = self.alphas_bar.to(self.device)
        out = []
        batch = max(1, self.cfg["sample_batch"] // max(1, len(x0)))
        for start in range(0, n_samples, batch):
            k = min(batch, n_samples - start)
            xr = x0.repeat(k, 1, 1)
            cr = cond.repeat(k, 1, 1)
            kr = known.repeat(k, 1, 1)
            x = torch.randn_like(xr)
            for i, t in enumerate(schedule):
                ab = ab_all[t]
                ab_prev = (
                    ab_all[schedule[i + 1]]
                    if i + 1 < steps
                    else torch.tensor(1.0, device=self.device)
                )
                eps = net(x, t.repeat(len(x)), cr)
                x0_hat = ((x - (1 - ab).sqrt() * eps) / ab.sqrt()).clamp(-X0_CLIP, X0_CLIP)
                x0_hat = kr * xr + (1 - kr) * x0_hat
                x = ab_prev.sqrt() * x0_hat + (1 - ab_prev).sqrt() * eps
            x = kr * xr + (1 - kr) * x
            out.append(x[:, : len(METRICS), -1].view(k, len(x0), len(METRICS)))
        return torch.cat(out)

    def predict(self, daily: pd.DataFrame) -> pd.DataFrame:
        splits = tuple(daily["split"].unique())
        windows = make_windows(daily, self.scaler, splits)
        x0, cond, known = _split(windows)
        x0, cond, known = (torch.tensor(a).to(self.device) for a in (x0, cond, known))
        chunk = max(1, self.cfg["sample_batch"] // self.cfg["samples"])
        quantiles = []
        for start in range(0, len(x0), chunk):
            samples = self._sample(
                x0[start : start + chunk],
                cond[start : start + chunk],
                known[start : start + chunk],
                self.cfg["samples"],
            )
            quantiles.append(
                torch.quantile(samples, torch.tensor([0.05, 0.5, 0.95], device=self.device), dim=0)
                .permute(1, 2, 0)
                .cpu()
                .numpy()
            )
        q = np.concatenate(quantiles) if quantiles else np.zeros((0, len(METRICS), 3))
        return quantile_frame(windows["meta"], q, self.scaler)

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        torch.save(self.ema.state_dict(), folder / "diffusion_ema.pt")
        (folder / "meta.json").write_text(
            json.dumps({"scaler": self.scaler.stats, "cfg": self.cfg}, indent=2)
        )
