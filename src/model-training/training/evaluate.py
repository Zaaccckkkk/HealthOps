"""Evaluation against the synthetic ground truth, on held-out users.

A prediction frame has one row per user-day-metric with q05/q50/q95 in data units; NaN means
"no estimate". The rule applied to every model before scoring: no estimate on a day when no
device recorded the metric (`apply_rule`), mirroring the assistant's "not enough data".

Reported per model (and per cohort):
    mae, coverage90 (q05 <= truth <= q95), width90, n
    structure recovery: slope of (reading - q50) on the applied bias; correlation between the
    implied per-user offset and the drawn per-user offset
    paired attribution: does the device the model moves away from most have the larger true error
Baselines use the same frame layout with q05/q95 = NaN: trust one device, average the available
devices, average after subtracting each device's mean training-set error.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from training.data import METRICS, has_reading

UNITS = {"sleep": "min", "steps": "steps", "rhr": "bpm"}


def long_truth(daily: pd.DataFrame) -> pd.DataFrame:
    """daily -> one row per user-day-metric with truth, both readings, bias and masks."""
    parts = []
    for name in METRICS:
        parts.append(
            pd.DataFrame(
                {
                    "user_id": daily["user_id"].to_numpy(),
                    "date": daily["date"].to_numpy(),
                    "metric": name,
                    "cohort": daily["cohort"].to_numpy(),
                    "split": daily["split"].to_numpy(),
                    "truth": daily[f"truth_{name}"].to_numpy(dtype=float),
                    "obs_hk": daily[f"obs_hk_{name}"].to_numpy(dtype=float),
                    "obs_gh": daily[f"obs_gh_{name}"].to_numpy(dtype=float),
                    "bias_hk": daily[f"bias_hk_{name}"].to_numpy(dtype=float),
                    "bias_gh": daily[f"bias_gh_{name}"].to_numpy(dtype=float),
                    "biasuser_hk": daily[f"biasuser_hk_{name}"].to_numpy(dtype=float),
                    "biasuser_gh": daily[f"biasuser_gh_{name}"].to_numpy(dtype=float),
                    "has_reading": has_reading(daily, name).to_numpy(),
                    "history_days": daily["day_index"].to_numpy(),
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def apply_rule(pred: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    """No estimate on a day without any reading of that metric."""
    merged = pred.merge(
        truth[["user_id", "date", "metric", "has_reading"]],
        on=["user_id", "date", "metric"],
        how="right",
    )
    for col in ("q05", "q50", "q95"):
        merged.loc[~merged["has_reading"], col] = np.nan
    return merged.drop(columns="has_reading")


def _in_device_space(name: str, values: pd.Series) -> pd.Series:
    return np.log1p(values) if name == "steps" else values


def _from_device_space(name: str, values: pd.Series) -> pd.Series:
    return np.expm1(values) if name == "steps" else values


def baselines(truth: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Rule-based estimators as prediction frames (point estimates only)."""
    train = truth[truth["split"] == "train"]
    correction = {}
    for name in METRICS:
        part = train[train["metric"] == name]
        for p in ("hk", "gh"):
            err = _in_device_space(name, part[f"obs_{p}"]) - _in_device_space(name, part["truth"])
            correction[(name, p)] = float(np.nanmean(err)) if err.notna().any() else 0.0
    out = {}
    base = truth[["user_id", "date", "metric"]].copy()
    out["apple_only"] = base.assign(q05=np.nan, q50=truth["obs_hk"].to_numpy(), q95=np.nan)
    out["fitbit_only"] = base.assign(q05=np.nan, q50=truth["obs_gh"].to_numpy(), q95=np.nan)
    avg, corrected = [], []
    for name in METRICS:
        rows = truth["metric"] == name
        hk = _in_device_space(name, truth.loc[rows, "obs_hk"])
        gh = _in_device_space(name, truth.loc[rows, "obs_gh"])
        avg.append(_from_device_space(name, pd.concat([hk, gh], axis=1).mean(axis=1)))
        corrected.append(
            _from_device_space(
                name,
                pd.concat(
                    [hk - correction[(name, "hk")], gh - correction[(name, "gh")]], axis=1
                ).mean(axis=1),
            )
        )
    out["device_average"] = base.assign(
        q05=np.nan, q50=pd.concat(avg).sort_index().to_numpy(), q95=np.nan
    )
    out["global_correction"] = base.assign(
        q05=np.nan, q50=pd.concat(corrected).sort_index().to_numpy(), q95=np.nan
    )
    return out


def _scores(frame: pd.DataFrame) -> dict:
    est = frame.dropna(subset=["q50"])
    if est.empty:
        return {"n": 0}
    err = est["q50"] - est["truth"]
    out = {"n": int(len(est)), "mae": float(err.abs().mean()), "bias": float(err.mean())}
    with_interval = est.dropna(subset=["q05", "q95"])
    if not with_interval.empty:
        inside = (with_interval["q05"] <= with_interval["truth"]) & (
            with_interval["truth"] <= with_interval["q95"]
        )
        out["coverage90"] = float(inside.mean())
        out["width90"] = float((with_interval["q95"] - with_interval["q05"]).mean())
    return out


def score(pred: pd.DataFrame, truth: pd.DataFrame, split: str = "test") -> dict:
    frame = truth[truth["split"] == split].merge(pred, on=["user_id", "date", "metric"], how="left")
    result = {"overall": {}, "by_metric": {}, "by_cohort": {}, "by_history": {}, "rule": {}}
    for name in METRICS:
        part = frame[frame["metric"] == name]
        result["by_metric"][name] = _scores(part)
        result["by_cohort"][name] = {c: _scores(g) for c, g in part.groupby("cohort")}
        result["by_history"][name] = {
            label: _scores(part[(part["history_days"] >= lo) & (part["history_days"] < hi)])
            for label, (lo, hi) in {
                "days_0_6": (0, 7),
                "days_7_29": (7, 30),
                "days_30_plus": (30, 10**6),
            }.items()
        }
        no_reading = part[~part["has_reading"]]
        with_reading = part[part["has_reading"]]
        result["rule"][name] = {
            "no_estimate_when_no_reading": float(no_reading["q50"].isna().mean())
            if len(no_reading)
            else None,
            "estimate_when_reading": float(with_reading["q50"].notna().mean())
            if len(with_reading)
            else None,
        }
    covered = [m["coverage90"] for m in result["by_metric"].values() if "coverage90" in m]
    result["overall"] = {
        "mae_normalised": float(
            np.mean(
                [
                    m["mae"] / truth.loc[truth["metric"] == k, "truth"].std()
                    for k, m in result["by_metric"].items()
                    if "mae" in m
                ]
            )
        ),
        "coverage90": float(np.mean(covered)) if covered else None,
    }
    return result


def structure_recovery(pred: pd.DataFrame, truth: pd.DataFrame, split: str = "test") -> dict:
    """How much of the injected bias the model removes, per device and metric."""
    frame = (
        truth[truth["split"] == split]
        .merge(pred, on=["user_id", "date", "metric"], how="left")
        .dropna(subset=["q50"])
    )
    out = {}
    for name in METRICS:
        part = frame[frame["metric"] == name]
        for p in ("hk", "gh"):
            rows = part.dropna(subset=[f"obs_{p}", f"bias_{p}"])
            if len(rows) < 10:
                continue
            residual = _in_device_space(name, rows[f"obs_{p}"]) - _in_device_space(
                name, rows["q50"]
            )
            bias = rows[f"bias_{p}"]
            if bias.std() > 0 and residual.std() > 0:
                slope = float(np.cov(bias, residual)[0, 1] / bias.var())
                corr = float(np.corrcoef(bias, residual)[0, 1])
            else:
                slope, corr = float("nan"), float("nan")
            # Implied per-user offset: the user's mean residual minus the device constant
            # and context part of the bias (what is left is the per-user part).
            constant_part = rows[f"bias_{p}"] - rows[f"biasuser_{p}"]
            implied = (residual - constant_part).groupby(rows["user_id"]).mean()
            drawn = rows.groupby("user_id")[f"biasuser_{p}"].first()
            user_corr = (
                float(implied.corr(drawn))
                if len(drawn) >= 5 and drawn.std() > 0 and implied.std() > 0
                else float("nan")
            )
            out[f"{name}/{p}"] = {
                "n": int(len(rows)),
                "residual_on_bias_slope": slope,
                "residual_on_bias_corr": corr,
                "user_offset_corr": user_corr,
                "mean_removed": float(residual.mean()),
                "mean_bias": float(bias.mean()),
            }
    return out


def attribution(pred: pd.DataFrame, truth: pd.DataFrame, split: str = "test") -> dict:
    """Paired days with both readings: is the device the estimate moves away from most the one
    with the larger true error?"""
    frame = truth[(truth["split"] == split) & (truth["cohort"] == "paired")].merge(
        pred, on=["user_id", "date", "metric"], how="left"
    )
    out = {}
    for name in METRICS:
        part = frame[frame["metric"] == name].dropna(subset=["obs_hk", "obs_gh", "q50"])
        if part.empty:
            continue
        true_err = {
            p: (
                _in_device_space(name, part[f"obs_{p}"]) - _in_device_space(name, part["truth"])
            ).abs()
            for p in ("hk", "gh")
        }
        model_err = {
            p: (
                _in_device_space(name, part[f"obs_{p}"]) - _in_device_space(name, part["q50"])
            ).abs()
            for p in ("hk", "gh")
        }
        worse_true = true_err["hk"] > true_err["gh"]
        worse_model = model_err["hk"] > model_err["gh"]
        out[name] = {"n": int(len(part)), "accuracy": float((worse_true == worse_model).mean())}
    return out


def evaluate(pred: pd.DataFrame, truth: pd.DataFrame, split: str = "test") -> dict:
    pred = apply_rule(pred, truth)
    return {
        "scores": score(pred, truth, split),
        "structure": structure_recovery(pred, truth, split),
        "attribution": attribution(pred, truth, split),
    }


def comparison_rows(results: dict[str, dict]) -> pd.DataFrame:
    """One row per model and metric for the summary table."""
    rows = []
    for model, result in results.items():
        for name in METRICS:
            s = result["scores"]["by_metric"].get(name, {})
            rows.append(
                {
                    "model": model,
                    "metric": name,
                    "unit": UNITS[name],
                    "n": s.get("n"),
                    "mae": s.get("mae"),
                    "coverage90": s.get("coverage90"),
                    "width90": s.get("width90"),
                    "attribution_acc": result.get("attribution", {}).get(name, {}).get("accuracy"),
                }
            )
    return pd.DataFrame(rows)


def _fmt(value, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return f"{value:.{digits}f}"


def results_markdown(results: dict[str, dict], info: dict) -> str:
    table = comparison_rows(results)
    title = (
        f"## {info['version']} · {info['users']} users ({info['users_by_split']}) · "
        f"split {info['split_sha256'][:12]}"
    )
    lines = [
        title,
        "",
        "| model | metric | n | MAE | coverage90 | width90 | attribution |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in table.itertuples():
        lines.append(
            f"| {r.model} | {r.metric} ({r.unit}) | {r.n} | {_fmt(r.mae)} | {_fmt(r.coverage90)} "
            f"| {_fmt(r.width90, 1)} | {_fmt(r.attribution_acc)} |"
        )
    return "\n".join(lines) + "\n"
