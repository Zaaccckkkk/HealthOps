"""CPU-only inference matching training/transformer_model.py and training/windows.py.

No training package, latent truth, cloud credentials or user-controlled artifact paths are
needed at runtime. Load a trusted, checksum-pinned state_dict once per API process.
"""

import hashlib
import json
import math
from pathlib import Path
from threading import Lock

METRICS = ("sleep_duration", "steps", "resting_heart_rate")
SHORT = ("sleep", "steps", "rhr")
PROVIDERS = ("healthkit", "google_health")
FEATURES = 30


def feature_rows(days, readings, scaler, first_day):
    """Match training feature order; unavailable context uses documented generator defaults.

    Each reading is already aggregated in API units (sleep seconds, steps count, RHR bpm).
    Missing measurements have a zero *standardized* value and an explicit zero mask.
    """
    result = []
    for day in days:
        if first_day is None or day < first_day:
            result.append([0.0] * FEATURES)
            continue
        values, masks = [], []
        for metric, short in zip(METRICS, SHORT):
            stat = scaler[short]
            for provider in PROVIDERS:
                value = readings.get((day, metric, provider))
                masks.append(float(value is not None))
                if value is None:
                    values.append(0.0)
                    continue
                value = float(value) / (60 if short == "sleep" else 1)
                if short == "steps":
                    value = math.log1p(value)
                values.append((value - stat["mean"]) / stat["sd"])
        # low stress, no workout/travel/illness, fatigue=3, from context_generation.yaml.
        context = [1, 0, 0, 1, 0, 0, 0, 0, 0, 0.3]
        dow = [float(day.weekday() == i) for i in range(7)]
        result.append(values + masks + context + dow + [1.0])
    return result


def build_net(cfg):
    """State-dict-compatible serving architecture; parity is tested against training.Net."""
    import torch
    from torch import nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            d = cfg["d_model"]
            self.embed = nn.Linear(FEATURES, d)
            self.position = nn.Parameter(torch.zeros(1, cfg["window"], d))
            layer = nn.TransformerEncoderLayer(
                d, cfg["heads"], cfg["ff"], cfg["dropout"], batch_first=True, norm_first=True
            )
            self.encoder = nn.TransformerEncoder(layer, cfg["layers"])
            self.head = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, 9))

        def forward(self, x):
            h = self.encoder(self.embed(x) + self.position)
            return self.head(h[:, -1]).view(x.shape[0], 3, 3)

    return Net()


class TransformerPredictor:
    def __init__(self, folder: str):
        import torch

        path = Path(folder)
        meta_bytes = (path / "meta.json").read_bytes()
        weights = (path / "transformer.pt").read_bytes()
        manifest = json.loads((path / "serving.json").read_text())
        for name, data in (("meta.json", meta_bytes), ("transformer.pt", weights)):
            if hashlib.sha256(data).hexdigest() != manifest["sha256"][name]:
                raise ValueError(f"Reconciliation artifact checksum mismatch: {name}")
        meta = json.loads(meta_bytes)
        self.window_days = meta["cfg"]["window"]
        if type(self.window_days) is not int or not 1 <= self.window_days <= 93:
            raise ValueError("Unsupported reconciliation window")
        self.scaler = meta["scaler"]
        for short in SHORT:
            s = self.scaler[short]
            if not math.isfinite(s["mean"]) or not math.isfinite(s["sd"]) or s["sd"] <= 0:
                raise ValueError("Invalid reconciliation scaler")
        self.version = manifest["model_version"]
        self.data_version = manifest["data_version"]
        self.artifact_sha256 = manifest["sha256"]["transformer.pt"]
        self.net = build_net(meta["cfg"])
        # weights_only prevents loading arbitrary pickle objects. Artifact is operator-pinned.
        self.net.load_state_dict(
            torch.load(path / "transformer.pt", map_location="cpu", weights_only=True), strict=True
        )
        self.net.eval()
        self.lock = Lock()

    def predict(self, days, readings, first_day, metric):
        import torch

        features = feature_rows(days, readings, self.scaler, first_day)
        with self.lock, torch.inference_mode():
            q = self.net(torch.tensor([features], dtype=torch.float32))[0, METRICS.index(metric)]
            q = q.sort().values.clamp(-8, 8).tolist()
        short = SHORT[METRICS.index(metric)]
        stat = self.scaler[short]
        decoded = [v * stat["sd"] + stat["mean"] for v in q]
        if short == "steps":
            decoded = [math.expm1(v) for v in decoded]
        if short == "sleep":
            decoded = [v * 60 for v in decoded]
        if any(not math.isfinite(v) or v < 0 for v in decoded):
            raise ValueError("Model produced nonphysical quantiles")
        return tuple(decoded)
