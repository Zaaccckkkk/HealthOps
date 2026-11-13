"""healthops-train: prepare -> LightGBM / Transformer / Diffusion -> evaluate, tracked in W&B.

    healthops-train --data-root ../../data --version synthetic-v0.2 --users 20 --smoke

Environment defaults (for the container): DATA_ROOT, TRAIN_DATA_VERSION, TRAIN_MODELS,
TRAIN_USERS, TRAIN_SMOKE, TRAIN_OUT. Outputs under --out (default .local/runs/<timestamp>):
prepared/daily.parquet, models/<name>/, predictions.parquet, metrics.json, RESULTS.md, run.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from training import data, evaluate
from training.diffusion_model import DiffusionModel
from training.lightgbm_model import LightGBMModel
from training.tracking import Tracker
from training.transformer_model import TransformerModel

log = logging.getLogger("healthops-train")
MODELS = {"lightgbm": LightGBMModel, "transformer": TransformerModel, "diffusion": DiffusionModel}
SMOKE = {
    "lightgbm": {"n_estimators": 60},
    "transformer": {"epochs": 3, "d_model": 32, "ff": 64, "patience": 3},
    "diffusion": {
        "epochs": 3,
        "hidden": 32,
        "blocks": 2,
        "ddim_steps": 10,
        "samples": 8,
        "patience": 3,
    },
}


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "describe", "--always", "--dirty"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("GIT_COMMIT", "unknown")


def parse_args(argv=None) -> argparse.Namespace:
    env = os.environ.get
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--data-root", default=env("DATA_ROOT", "/data"))
    p.add_argument("--version", default=env("TRAIN_DATA_VERSION", "synthetic-v0.2"))
    p.add_argument("--models", default=env("TRAIN_MODELS", "lightgbm,transformer,diffusion"))
    p.add_argument(
        "--users",
        type=int,
        default=int(env("TRAIN_USERS", "0")) or None,
        help="random subset of users",
    )
    p.add_argument(
        "--smoke", action="store_true", default=env("TRAIN_SMOKE", "").lower() in ("1", "true")
    )
    p.add_argument("--out", default=env("TRAIN_OUT", ""))
    p.add_argument("--seed", type=int, default=215)
    p.add_argument(
        "--config",
        default="",
        help='JSON with per-model overrides, e.g. \'{"diffusion": {"epochs": 60}}\'',
    )
    # Pipeline stages (Vertex AI Pipelines runs prepare, train and compare as separate steps):
    p.add_argument(
        "--prepare-only", action="store_true", help="build the daily table under --out and stop"
    )
    p.add_argument(
        "--prepared",
        default="",
        help="directory with daily.parquet + prepared.json from an earlier --prepare-only run",
    )
    p.add_argument(
        "--run-suffix", default="", help="appended to W&B run names, e.g. w30 -> transformer-w30"
    )
    p.add_argument("--group", default="", help="W&B group; default <version>-<timestamp>")
    return p.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args(argv)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = (
        Path(args.out)
        if args.out
        else Path(".local/runs") / f"{stamp}-{args.version}{'-smoke' if args.smoke else ''}"
    )
    out.mkdir(parents=True, exist_ok=True)
    overrides = json.loads(args.config) if args.config else {}
    names = [n.strip() for n in args.models.split(",") if n.strip()]
    commit = git_commit()
    import torch  # local import: LightGBM-only runs still work without touching CUDA at start

    log.info(
        "torch %s, cuda available=%s, device=%s",
        torch.__version__,
        torch.cuda.is_available(),
        torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
    )
    log.info(
        "version=%s users=%s models=%s smoke=%s out=%s commit=%s",
        args.version,
        args.users or "all",
        names,
        args.smoke,
        out,
        commit,
    )

    started = time.time()
    if args.prepared:
        prepared = Path(args.prepared)
        info = json.loads((prepared / "prepared.json").read_text())
        log.info("using prepared table %s (%d user-days)", prepared, info["user_days"])
    else:
        prepared = out / "prepared"
        info = data.prepare(Path(args.data_root), args.version, prepared, args.users, args.seed)
        info["prepare_seconds"] = round(time.time() - started, 1)
        log.info(
            "prepared %d users, %d user-days, splits %s",
            info["users"],
            info["user_days"],
            info["users_by_split"],
        )
        if args.prepare_only:
            log.info("prepare only: done in %.0fs: %s", time.time() - started, prepared)
            return 0
    daily = pd.read_parquet(prepared / "daily.parquet")
    truth = evaluate.long_truth(daily)

    common = {
        "data_version": args.version,
        "manifest_sha256": info["manifest_sha256"],
        "split_sha256": info["split_sha256"],
        "users": info["users"],
        "user_days": info["user_days"],
        "smoke": args.smoke,
        "git_commit": commit,
        "seed": args.seed,
    }
    group = args.group or f"{args.version}-{stamp}"
    suffix = f"-{args.run_suffix}" if args.run_suffix else ""
    results, predictions, run_record = {}, [], {"group": group, "out": str(out), "models": {}}

    base = evaluate.baselines(truth)
    for name, frame in base.items():
        results[name] = evaluate.evaluate(frame, truth)

    for name in names:
        cfg = {**(SMOKE[name] if args.smoke else {}), **overrides.get(name, {})}
        model = MODELS[name](cfg)
        tracker = Tracker(
            f"{name}{suffix}-{args.version}{'-smoke' if args.smoke else ''}",
            {**common, "model": name, "run_suffix": args.run_suffix, **model.cfg},
            group=group,
            tags=[name, args.version] + ([args.run_suffix] if args.run_suffix else []),
        )
        t0 = time.time()
        summary = model.fit(daily, log=tracker.log)
        fit_seconds = time.time() - t0
        t0 = time.time()
        pred = model.predict(daily[daily["split"].isin(["val", "test"])])
        predict_seconds = time.time() - t0
        model.save(out / "models" / name)
        result = evaluate.evaluate(pred, truth, "test")
        result["val"] = evaluate.score(evaluate.apply_rule(pred, truth), truth, "val")
        results[name] = result
        predictions.append(pred.assign(model=name))
        flat = (
            {f"test/{m}/mae": result["scores"]["by_metric"][m].get("mae") for m in data.METRICS}
            | {
                f"test/{m}/coverage90": result["scores"]["by_metric"][m].get("coverage90")
                for m in data.METRICS
            }
            | {"fit_seconds": round(fit_seconds, 1), "predict_seconds": round(predict_seconds, 1)}
        )
        tracker.summary({**flat, **{k: v for k, v in summary.items() if not isinstance(v, dict)}})
        tracker.table("structure_recovery", pd.DataFrame(result["structure"]).T.reset_index())
        tracker.artifact(f"{name}{suffix}-{args.version}", "model", out / "models" / name)
        run_record["models"][name] = {
            "fit_seconds": round(fit_seconds, 1),
            "predict_seconds": round(predict_seconds, 1),
            "config": model.cfg,
            "summary": {k: v for k, v in summary.items() if k != "feature_importance"},
            "wandb_url": tracker.url,
        }
        if "feature_importance" in summary:
            (out / "models" / name / "feature_importance.json").write_text(
                json.dumps(summary["feature_importance"], indent=2)
            )
        tracker.finish()
        log.info(
            "%s: fit %.1fs predict %.1fs test MAE %s",
            name,
            fit_seconds,
            predict_seconds,
            {
                m: round(result["scores"]["by_metric"][m].get("mae", float("nan")), 2)
                for m in data.METRICS
            },
        )

    if predictions:
        pd.concat(predictions, ignore_index=True).to_parquet(
            out / "predictions.parquet", index=False
        )
    (out / "metrics.json").write_text(json.dumps(results, indent=2, default=float))
    table = evaluate.comparison_rows(results)
    (out / "RESULTS.md").write_text(evaluate.results_markdown(results, info))
    run_record.update(
        {
            "info": info,
            "common": common,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    (out / "run.json").write_text(json.dumps(run_record, indent=2, default=str))

    comparison = Tracker(
        f"comparison{suffix}-{args.version}{'-smoke' if args.smoke else ''}",
        common,
        group=group,
        tags=["comparison", args.version],
    )
    comparison.table("comparison", table)
    comparison.summary(
        {
            f"{r.model}/{r.metric}/mae": r.mae
            for r in table.itertuples()
            if r.mae is not None and not np.isnan(r.mae)
        }
    )
    comparison.finish()
    print((out / "RESULTS.md").read_text())
    log.info("done in %.0fs: %s", time.time() - started, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
