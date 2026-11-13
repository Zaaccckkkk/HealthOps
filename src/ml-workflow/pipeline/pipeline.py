"""Vertex AI Pipeline for the reconciliation models: prepare -> train per window (GPU) -> compare.

Every step is a Vertex AI custom job running the model-training image, so the pipeline executes
exactly the same code as `make train` and `make train-vertex`:

    prepare   healthops-train --prepare-only          one row per user-day -> daily.parquet
    train[w]  healthops-train --prepared ... --config  one job per transformer window (14, 30, ...)
    compare   healthops-compare --run w14=... --run w30=...   one table + W&B comparison run

Steps exchange data through the pipeline folder in the bucket, mounted at /gcs/<bucket> inside
every job (<pipeline_root>/runs/<run_id>/{prepared,w14,w30,comparison}). The W&B key is given
to the jobs as an environment variable, read from Secret Manager at submit time (submit.py).
"""

from google_cloud_pipeline_components.v1.custom_job import CustomTrainingJobOp
from kfp import dsl

PIPELINE_NAME = "healthops-reconciliation-training"


# Every value handed to a job's environment must be non-empty at run time (Vertex rejects an
# empty env value), so optional settings such as the W&B entity are not pipeline parameters:
# runs go to the default entity of the key, and submit.py refuses to run without a key.


def _env(values: dict) -> list[dict]:
    return [{"name": k, "value": v} for k, v in values.items() if v != ""]


def worker_pool(
    image: str,
    args: list,
    env: dict,
    machine_type: str,
    accelerator_type: str = "ACCELERATOR_TYPE_UNSPECIFIED",
    accelerator_count: int = 0,
) -> list[dict]:
    """One replica running the training image with `args`. The accelerator fields are always
    present because their values are pipeline parameters, unknown at compile time: a CPU step
    passes ACCELERATOR_TYPE_UNSPECIFIED (the API's "none") with a count of 0."""
    return [
        {
            "machine_spec": {
                "machine_type": machine_type,
                "accelerator_type": accelerator_type,
                "accelerator_count": accelerator_count,
            },
            "replica_count": 1,
            "disk_spec": {"boot_disk_type": "pd-ssd", "boot_disk_size_gb": 100},
            "container_spec": {"image_uri": image, "args": args, "env": _env(env)},
        }
    ]


def build_pipeline(parallelism: int = 1):
    """The pipeline with a fixed number of training jobs allowed at once. KFP evaluates the
    pipeline body when it is defined, so the limit is bound here, before compilation. 1 fits
    the project's approved Vertex training quota of one L4 GPU."""

    @dsl.pipeline(
        name=PIPELINE_NAME, description="prepare -> transformer per window (GPU) -> compare"
    )
    def reconciliation_pipeline(
        project: str,
        location: str,
        image: str,
        bucket: str,
        run_id: str,
        data_version: str = "synthetic-v0.2",
        windows: list = ["14", "30"],  # strings: KFP stores list numbers as floats  # noqa: B006
        model: str = "transformer",
        users: int = 0,
        smoke: str = "false",  # a string: KFP cannot place a boolean into an env value
        service_account: str = "",
        machine_type: str = "g2-standard-4",
        accelerator_type: str = "NVIDIA_L4",
        accelerator_count: int = 1,
        prepare_machine_type: str = "n1-standard-8",
        wandb_project: str = "healthops-m3",
        wandb_api_key: str = "",
        git_commit: str = "unknown",
    ):
        root = f"/gcs/{bucket}/pipelines/runs/{run_id}"
        data_root = f"/gcs/{bucket}/data"
        group = f"pipeline-{data_version}-{run_id}"
        wandb_dir = "/tmp/wandb"
        env = {
            "WANDB_PROJECT": wandb_project,
            "WANDB_API_KEY": wandb_api_key,
            "WANDB_DIR": wandb_dir,
            "GIT_COMMIT": git_commit,
            "HOME": "/tmp",
        }

        prepare = CustomTrainingJobOp(
            display_name=f"prepare-{run_id}",
            project=project,
            location=location,
            service_account=service_account,
            worker_pool_specs=worker_pool(
                image,
                [
                    "healthops-train",
                    "--prepare-only",
                    "--data-root",
                    data_root,
                    "--version",
                    data_version,
                    "--users",
                    f"{users}",
                    "--out",
                    f"{root}/prepared",
                ],
                env,
                prepare_machine_type,
            ),
        ).set_display_name("prepare daily table")

        with dsl.ParallelFor(
            items=windows, name="train-per-window", parallelism=parallelism
        ) as window:
            train = CustomTrainingJobOp(
                display_name=f"train-{model}-w{window}-{run_id}",
                project=project,
                location=location,
                service_account=service_account,
                worker_pool_specs=worker_pool(
                    image,
                    [
                        "healthops-train",
                        "--prepared",
                        f"{root}/prepared/prepared",
                        "--version",
                        data_version,
                        "--models",
                        model,
                        "--run-suffix",
                        f"w{window}",
                        "--group",
                        group,
                        "--config",
                        f'{{"{model}": {{"window": {window}}}}}',
                        "--out",
                        f"{root}/w{window}",
                    ],
                    # smoke is a run-time value: passed through the environment, not a flag
                    {**env, "TRAIN_SMOKE": smoke},
                    machine_type,
                    accelerator_type,
                    accelerator_count,
                ),
            ).set_display_name(f"train {model} window")
            train.after(prepare)

        compare = CustomTrainingJobOp(
            display_name=f"compare-{model}-{run_id}",
            project=project,
            location=location,
            service_account=service_account,
            worker_pool_specs=worker_pool(
                image,
                [
                    "sh",
                    "-c",
                    # The window list is only known at run time, so the run labels are listed from
                    # the folders the train steps wrote.
                    f'set -e; cd {root}; args=""; for d in w*/; do l=${{d%/}}; '
                    f'args="$args --run $l={root}/$l"; done; '
                    f"healthops-compare --model {model} --group {group} --version {data_version} "
                    f"--out {root}/comparison $args",
                ],
                env,
                prepare_machine_type,
            ),
        ).set_display_name("compare windows")
        compare.after(train)

    return reconciliation_pipeline


reconciliation_pipeline = build_pipeline()
