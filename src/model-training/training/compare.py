"""healthops-compare: one table across several training runs (e.g. transformer window 14 vs 30).

    healthops-compare --run w14=gs/run-a --run w30=gs/run-b --out comparison/

Each run directory is the output of healthops-train: metrics.json (scores per model and
baseline) and run.json (configuration, W&B URL). The comparison keeps the rule baselines from
the first run (they do not depend on the model) and writes COMPARISON.md, comparison.json and a
W&B run holding the same tables, so the decision between the runs is documented in one place.

Reported per labelled model: test MAE / coverage / width by metric, the same by cohort and by
history length, and paired attribution. These are the criteria for the window decision: a longer
window is adopted only if paired users improve without short-history users getting worse.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import pandas as pd

from training.data import METRICS
from training.evaluate import UNITS
from training.tracking import Tracker

log = logging.getLogger("healthops-compare")
BASELINES = ("apple_only", "fitbit_only", "device_average", "global_correction")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--run",
        action="append",
        required=True,
        help="label=run_dir; the label names the run in the tables, e.g. w14=.../runs/a",
    )
    p.add_argument("--model", default="transformer", help="model name inside each run")
    p.add_argument("--out", required=True)
    p.add_argument("--group", default="", help="W&B group of the compared runs")
    p.add_argument("--version", default=os.environ.get("TRAIN_DATA_VERSION", ""))
    return p.parse_args(argv)


def load_runs(specs: list[str], model: str) -> dict[str, dict]:
    runs = {}
    for spec in specs:
        label, _, folder = spec.partition("=")
        if not label or not folder:
            raise SystemExit(f"--run expects label=dir, got {spec!r}")
        path = Path(folder)
        metrics = json.loads((path / "metrics.json").read_text())
        record = json.loads((path / "run.json").read_text())
        if model not in metrics:
            raise SystemExit(f"{path}: no results for model {model!r}")
        runs[label] = {
            "dir": str(path),
            "result": metrics[model],
            "baselines": {b: metrics[b] for b in BASELINES if b in metrics},
            "record": record["models"].get(model, {}),
            "info": record.get("info", {}),
        }
    return runs


def _row(label: str, metric: str, scores: dict, attribution: dict | None = None) -> dict:
    return {
        "run": label,
        "metric": metric,
        "unit": UNITS[metric],
        "n": scores.get("n"),
        "mae": scores.get("mae"),
        "coverage90": scores.get("coverage90"),
        "width90": scores.get("width90"),
        "attribution": (attribution or {}).get(metric, {}).get("accuracy"),
    }


def tables(runs: dict[str, dict]) -> dict[str, pd.DataFrame]:
    first = next(iter(runs.values()))
    overall, cohort, history = [], [], []
    for name, base in first["baselines"].items():
        for m in METRICS:
            overall.append(
                _row(name, m, base["scores"]["by_metric"].get(m, {}), base["attribution"])
            )
    for label, run in runs.items():
        scores = run["result"]["scores"]
        for m in METRICS:
            overall.append(
                _row(label, m, scores["by_metric"].get(m, {}), run["result"]["attribution"])
            )
            for c, s in scores["by_cohort"].get(m, {}).items():
                cohort.append({"run": label, "metric": m, "cohort": c, **_row(label, m, s)})
            for h, s in scores["by_history"].get(m, {}).items():
                history.append({"run": label, "metric": m, "history": h, **_row(label, m, s)})
    return {
        "overall": pd.DataFrame(overall),
        "by_cohort": pd.DataFrame(cohort).drop(columns=["unit", "attribution"], errors="ignore"),
        "by_history": pd.DataFrame(history).drop(columns=["unit", "attribution"], errors="ignore"),
    }


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def markdown(runs: dict[str, dict], t: dict[str, pd.DataFrame], model: str) -> str:
    lines = [f"## {model}: {' vs '.join(runs)}", ""]
    for label, run in runs.items():
        cfg = run["record"].get("config", {})
        keys = ("window", "epochs", "d_model", "layers", "stride")
        shown = ", ".join(f"{k}={cfg[k]}" for k in keys if k in cfg)
        lines.append(
            f"- **{label}**: {shown}; fit {run['record'].get('fit_seconds', '?')} s; "
            f"W&B {run['record'].get('wandb_url') or 'offline'}; `{run['dir']}`"
        )
    lines += [
        "",
        "### Test users, overall",
        "",
        "| run | metric | n | MAE | coverage90 | width90 | attribution |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in t["overall"].itertuples():
        lines.append(
            f"| {r.run} | {r.metric} ({r.unit}) | {r.n} | {_fmt(r.mae)} | {_fmt(r.coverage90)} | "
            f"{_fmt(r.width90)} | {_fmt(r.attribution)} |"
        )
    for key, col, title in (
        ("by_cohort", "cohort", "By cohort"),
        ("by_history", "history", "By history length"),
    ):
        lines += [
            "",
            f"### {title}",
            "",
            f"| run | metric | {col} | n | MAE | coverage90 | width90 |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in t[key].itertuples():
            lines.append(
                f"| {r.run} | {r.metric} | {getattr(r, col)} | {r.n} | {_fmt(r.mae)} | "
                f"{_fmt(r.coverage90)} | {_fmt(r.width90)} |"
            )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    args = parse_args(argv)
    runs = load_runs(args.run, args.model)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t = tables(runs)
    text = markdown(runs, t, args.model)
    (out / "COMPARISON.md").write_text(text)
    (out / "comparison.json").write_text(
        json.dumps(
            {
                "model": args.model,
                "runs": {
                    k: {
                        "dir": v["dir"],
                        "config": v["record"].get("config"),
                        "wandb_url": v["record"].get("wandb_url"),
                    }
                    for k, v in runs.items()
                },
                **{k: df.to_dict(orient="records") for k, df in t.items()},
            },
            indent=2,
            default=float,
        )
    )
    version = args.version or next(iter(runs.values()))["info"].get("version", "")
    tracker = Tracker(
        f"compare-{args.model}-{'-'.join(runs)}-{version}",
        {"model": args.model, "runs": {k: v["record"].get("config") for k, v in runs.items()}},
        group=args.group or None,
        tags=["comparison", args.model, *runs],
    )
    for name, df in t.items():
        tracker.table(name, df)
    tracker.summary(
        {
            f"{r.run}/{r.metric}/mae": r.mae
            for r in t["overall"].itertuples()
            if r.run in runs and r.mae is not None
        }
    )
    tracker.finish()
    print(text)
    log.info("comparison written to %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
