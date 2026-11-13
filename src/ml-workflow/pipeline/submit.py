"""healthops-pipeline: compile the Vertex AI Pipeline and submit one run.

    healthops-pipeline --image us-east4-docker.pkg.dev/<project>/healthops/model-training:<tag>
    healthops-pipeline --compile-only --out pipeline.json          # no cloud call
    healthops-pipeline --image ... --windows 14,30 --smoke --users 200   # cheap first run

Nothing is billable until the final submit; --compile-only never contacts the cloud. The W&B
key is read from Secret Manager (--wandb-secret, default wandb-api-key) and handed to the jobs
as an environment variable, exactly like src/model-training/vertex/submit.sh. The secret is
required: Vertex rejects an empty environment value, so the pipeline has no offline mode. Every
value can also come from the environment (PROJECT, REGION, BUCKET, SERVICE_ACCOUNT, IMAGE,
DATA_VERSION, WINDOWS, MODEL, WANDB_PROJECT).
"""

from __future__ import annotations

import argparse
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from kfp import compiler

from pipeline.pipeline import PIPELINE_NAME, build_pipeline

DEFAULT_PROJECT = "ac215-project-509705"
DEFAULT_REGION = "us-east4"
DEFAULT_BUCKET = "ac215-project-509705-healthops-data"


def parse_args(argv=None) -> argparse.Namespace:
    env = os.environ.get
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--project", default=env("PROJECT", DEFAULT_PROJECT))
    p.add_argument("--region", default=env("REGION", DEFAULT_REGION))
    p.add_argument("--bucket", default=env("BUCKET", DEFAULT_BUCKET))
    p.add_argument(
        "--service-account",
        default=env(
            "SERVICE_ACCOUNT",
            f"healthops-vertex-training@{DEFAULT_PROJECT}.iam.gserviceaccount.com",
        ),
    )
    p.add_argument("--image", default=env("IMAGE", ""), help="training image (the GPU build)")
    p.add_argument("--data-version", default=env("DATA_VERSION", "synthetic-v0.2"))
    p.add_argument("--windows", default=env("WINDOWS", "14,30"), help="comma-separated windows")
    p.add_argument("--model", default=env("MODEL", "transformer"))
    p.add_argument("--users", type=int, default=0, help="random subset of users (0 = all)")
    p.add_argument("--smoke", action="store_true", help="tiny model settings; proves the path")
    p.add_argument("--machine-type", default=env("MACHINE_TYPE", "g2-standard-4"))
    p.add_argument(
        "--accelerator-type",
        default=env("ACCELERATOR_TYPE", "NVIDIA_L4"),
        help="Vertex accelerator for the training steps, or 'none' for CPU-only training",
    )
    p.add_argument("--accelerator-count", type=int, default=int(env("ACCELERATOR_COUNT", "1")))
    p.add_argument("--prepare-machine-type", default=env("PREPARE_MACHINE_TYPE", "n1-standard-8"))
    p.add_argument("--wandb-project", default=env("WANDB_PROJECT", "healthops-m3"))
    p.add_argument("--wandb-secret", default=env("WANDB_SECRET", "wandb-api-key"))
    p.add_argument(
        "--parallelism",
        type=int,
        default=int(env("PIPELINE_PARALLELISM", "1")),
        help="training jobs at once; 1 fits one GPU of quota, 2 runs both windows together",
    )
    p.add_argument("--run-id", default="", help="default: UTC timestamp")
    p.add_argument("--out", default="pipeline.json", help="compiled pipeline spec")
    p.add_argument("--compile-only", action="store_true")
    p.add_argument("--wait", action="store_true", help="block until the run finishes")
    return p.parse_args(argv)


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "describe", "--always", "--dirty"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("GIT_COMMIT", "unknown")


def wandb_key(project: str, secret: str) -> str:
    """The W&B key from Secret Manager, or '' when it cannot be read."""
    if not secret:
        return ""
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project}/secrets/{secret}/versions/latest"
        return client.access_secret_version(name=name).payload.data.decode()
    except Exception as exc:  # noqa: BLE001 - any failure means offline tracking, not a crash
        print(f"W&B: secret {secret!r} not readable ({type(exc).__name__})")
        return ""


def parameters(args: argparse.Namespace, run_id: str, key: str) -> dict:
    windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    if not windows or any(not w.isdigit() for w in windows):
        raise SystemExit(f"--windows expects integers, got {args.windows!r}")
    accelerator = "" if args.accelerator_type.lower() in ("", "none") else args.accelerator_type
    return {
        "project": args.project,
        "location": args.region,
        "image": args.image,
        "bucket": args.bucket,
        "run_id": run_id,
        "data_version": args.data_version,
        "windows": windows,
        "model": args.model,
        "users": args.users,
        "smoke": "true" if args.smoke else "false",
        "service_account": args.service_account,
        "machine_type": args.machine_type,
        "accelerator_type": accelerator or "ACCELERATOR_TYPE_UNSPECIFIED",
        "accelerator_count": args.accelerator_count if accelerator else 0,
        "prepare_machine_type": args.prepare_machine_type,
        "wandb_project": args.wandb_project,
        "wandb_api_key": key,
        "git_commit": git_commit(),
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    compiler.Compiler().compile(build_pipeline(args.parallelism), str(out))
    print(f"compiled {PIPELINE_NAME} -> {out}")
    if args.compile_only:
        return 0
    if not args.image:
        raise SystemExit("--image (or IMAGE) is required to submit; use the GPU build")
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    key = wandb_key(args.project, args.wandb_secret)
    if not key:
        # An empty env value is rejected by Vertex, so there is no offline mode for the pipeline.
        raise SystemExit(
            f"no W&B key: create the Secret Manager secret {args.wandb_secret!r} (see README)"
        )
    params = parameters(args, run_id, key)
    shown = {k: ("<set>" if k == "wandb_api_key" and v else v) for k, v in params.items()}
    print("parameters:", shown)

    from google.cloud import aiplatform

    aiplatform.init(project=args.project, location=args.region)
    job = aiplatform.PipelineJob(
        display_name=f"{PIPELINE_NAME}-{run_id}",
        template_path=str(out),
        pipeline_root=f"gs://{args.bucket}/pipelines",
        parameter_values=params,
        enable_caching=False,
        job_id=f"{PIPELINE_NAME}-{run_id}".lower(),
    )
    job.submit(service_account=args.service_account)
    console = (
        f"https://console.cloud.google.com/vertex-ai/locations/{args.region}/pipelines/runs/"
        f"{job.job_id}?project={args.project}"
    )
    print(f"submitted {job.resource_name}")
    print(f"console: {console}")
    print(
        f"outputs: gs://{args.bucket}/pipelines/runs/{run_id}/ (prepared/, w<window>/, comparison/)"
    )
    if args.wait:
        job.wait()
        print(f"state: {job.state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
