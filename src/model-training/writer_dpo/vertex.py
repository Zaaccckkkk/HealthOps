"""Create a reviewable single-node Vertex job spec without provisioning resources."""

import argparse
import json
import re
from pathlib import Path

from writer_dpo.cloud import gcs_path


def job_spec(
    image,
    service_account,
    bundle_uri,
    bundle_sha256,
    snapshot_uri,
    snapshot_sha256,
    output_uri,
    mode="smoke",
    wandb_mode="offline",
    wandb_project="healthops-writer-dpo",
    wandb_entity=None,
    wandb_secret_version=None,
):
    if not re.fullmatch(r"[\w./:-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("Use an immutable training image digest")
    if mode not in {"smoke", "train", "restore-check"}:
        raise ValueError("Unknown execution mode")
    if wandb_mode not in {"offline", "online", "disabled"}:
        raise ValueError("Unknown W&B mode")
    if wandb_mode == "online" and (not wandb_entity or not wandb_secret_version):
        raise ValueError("Online W&B requires explicit entity and pinned secret version")
    if wandb_secret_version and not re.fullmatch(
        r"projects/[\w-]+/secrets/[\w-]+/versions/[1-9]\d*", wandb_secret_version
    ):
        raise ValueError("Use a pinned numeric Secret Manager version")
    tracking_env = [
        {"name": "WANDB_MODE", "value": wandb_mode},
        {"name": "WANDB_PROJECT", "value": wandb_project},
        {"name": "HF_HUB_OFFLINE", "value": "1"},
    ]
    if wandb_entity:
        tracking_env.append({"name": "WANDB_ENTITY", "value": wandb_entity})
    if wandb_secret_version:
        tracking_env.append(
            {"name": "HEALTHOPS_WANDB_SECRET_VERSION", "value": wandb_secret_version}
        )
    for uri in (bundle_uri, snapshot_uri, output_uri):
        gcs_path(uri)
    for digest in (bundle_sha256, snapshot_sha256):
        if not re.fullmatch("[a-f0-9]{64}", digest):
            raise ValueError("Input manifest SHA-256 is required")
    return {
        "displayName": f"healthops-writer-dpo-{mode}",
        "labels": {"component": "writer-dpo", "mode": mode},
        "jobSpec": {
            "serviceAccount": service_account,
            "scheduling": {
                "strategy": "FLEX_START",
                "timeout": "7200s",
                "maxWaitDuration": "1800s",
                "restartJobOnWorkerRestart": False,
            },
            "workerPoolSpecs": [
                {
                    "replicaCount": "1",
                    "machineSpec": {
                        "machineType": "a2-highgpu-4g",
                        "acceleratorType": "NVIDIA_TESLA_A100",
                        "acceleratorCount": 4,
                    },
                    "diskSpec": {"bootDiskType": "pd-ssd", "bootDiskSizeGb": 500},
                    "containerSpec": {
                        "imageUri": image,
                        "command": [
                            "/opt/nvidia/nvidia_entrypoint.sh",
                            "/opt/healthops-io/bin/python",
                            "-m",
                            "writer_dpo.cloud",
                        ],
                        "args": [
                            "--bundle-uri",
                            bundle_uri,
                            "--bundle-sha256",
                            bundle_sha256,
                            "--snapshot-uri",
                            snapshot_uri,
                            "--snapshot-sha256",
                            snapshot_sha256,
                            "--output-uri",
                            output_uri,
                            "--mode",
                            mode,
                        ],
                        "env": tracking_env,
                    },
                }
            ],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "image",
        "service-account",
        "bundle-uri",
        "bundle-sha256",
        "snapshot-uri",
        "snapshot-sha256",
        "output-uri",
    ):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--mode", choices=["smoke", "train", "restore-check"], default="smoke")
    parser.add_argument(
        "--wandb-mode", choices=["offline", "online", "disabled"], default="offline"
    )
    parser.add_argument("--wandb-project", default="healthops-writer-dpo")
    parser.add_argument("--wandb-entity")
    parser.add_argument("--wandb-secret-version")
    parser.add_argument("--output", required=True, type=Path)
    args = vars(parser.parse_args())
    output = args.pop("output")
    spec = job_spec(**args)
    with output.open("x") as stream:
        json.dump(spec, stream, indent=2)
    print("Job specification written; no cloud resource created")


if __name__ == "__main__":
    main()
