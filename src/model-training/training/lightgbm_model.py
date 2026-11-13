"""LightGBM quantile regression: one model per metric and quantile, one row per user-day.

Missing readings stay NaN: LightGBM learns a branch for "missing" at every split, so a day
without a device reading is a signal, never an imputed zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from training.data import METRICS, feature_columns
from training.windows import Scaler, quantile_frame

QUANTILES = (0.05, 0.5, 0.95)
DEFAULTS = {"n_estimators": 600, "learning_rate": 0.03, "num_leaves": 31, "min_child_samples": 40}


class LightGBMModel:
    name = "lightgbm"

    def __init__(self, cfg: dict | None = None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.models: dict[tuple[str, float], lgb.Booster] = {}
        self.features: list[str] = []
        self.scaler: Scaler | None = None

    def fit(self, daily: pd.DataFrame, log=None) -> dict:
        self.features = feature_columns(daily)
        self.scaler = Scaler(daily)
        train, val = daily[daily["split"] == "train"], daily[daily["split"] == "val"]
        params = {
            "objective": "quantile",
            "learning_rate": self.cfg["learning_rate"],
            "num_leaves": self.cfg["num_leaves"],
            "min_data_in_leaf": self.cfg["min_child_samples"],
            "verbosity": -1,
            "seed": 0,
        }
        summary = {}
        for name in METRICS:
            y_train = self.scaler.encode(name, train[f"truth_{name}"])
            y_val = self.scaler.encode(name, val[f"truth_{name}"])
            train_set = lgb.Dataset(train[self.features], y_train, free_raw_data=False)
            val_set = (
                lgb.Dataset(val[self.features], y_val, reference=train_set) if len(val) else None
            )
            for q in QUANTILES:
                booster = lgb.train(
                    {**params, "alpha": q},
                    train_set,
                    num_boost_round=self.cfg["n_estimators"],
                    valid_sets=[val_set] if val_set else None,
                    callbacks=[lgb.early_stopping(50, verbose=False)] if val_set else None,
                )
                self.models[(name, q)] = booster
                key = f"{name}/q{int(q * 100):02d}"
                summary[f"{key}/best_iteration"] = int(booster.best_iteration or 0)
                if val_set and log:
                    log(
                        {
                            f"lightgbm/{key}/val_pinball": float(
                                booster.best_score["valid_0"]["quantile"]
                            )
                        }
                    )
        summary["feature_importance"] = self.importance()
        return summary

    def importance(self) -> dict[str, float]:
        total = np.zeros(len(self.features))
        for model in self.models.values():
            total += model.feature_importance(importance_type="gain")
        total = total / max(total.sum(), 1e-9)
        return {
            f: round(float(v), 4) for f, v in sorted(zip(self.features, total), key=lambda t: -t[1])
        }

    def predict(self, daily: pd.DataFrame) -> pd.DataFrame:
        q = np.zeros((len(daily), len(METRICS), len(QUANTILES)))
        for i, name in enumerate(METRICS):
            for j, quantile in enumerate(QUANTILES):
                booster = self.models[(name, quantile)]
                q[:, i, j] = booster.predict(
                    daily[self.features], num_iteration=booster.best_iteration or None
                )
        meta = daily[["user_id", "date", "cohort"]].reset_index(drop=True)
        return quantile_frame(meta, q, self.scaler)

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        for (name, q), model in self.models.items():
            model.save_model(str(folder / f"{name}_q{int(q * 100):02d}.txt"))
        (folder / "meta.json").write_text(
            json.dumps(
                {"features": self.features, "scaler": self.scaler.stats, "cfg": self.cfg}, indent=2
            )
        )
