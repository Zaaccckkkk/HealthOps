# ML Workflow: Vertex AI Pipeline

One pipeline that prepares the training table, trains the Transformer once per context window
on a GPU, and compares the windows in a single table and W&B run. Every step is a
Vertex AI custom job running the `model-training` image, so the pipeline executes the same
code as `make train` locally and `make train-vertex` for a single job.

```text
prepare (n1-standard-8, CPU)            healthops-train --prepare-only
   └─> train w14 (g2-standard-4 + L4)   healthops-train --prepared ... --config '{"transformer":{"window":14}}'
   └─> train w30 (g2-standard-4 + L4)   healthops-train --prepared ... --config '{"transformer":{"window":30}}'
         └─> compare (CPU)              healthops-compare --run w14=... --run w30=...
```

| | |
|---|---|
| Definition | `pipeline/pipeline.py` (KFP v2 + Google Cloud Pipeline Components `CustomTrainingJobOp`) |
| Submit | `pipeline/submit.py` (`healthops-pipeline`), `make pipeline` from the repository root |
| Data in | `gs://<bucket>/data/synthetic/<version>/` (plain copy of the DVC version, mounted at `/gcs/<bucket>/data`) |
| Data out | `gs://<bucket>/pipelines/runs/<run_id>/{prepared, w14, w30, comparison}/` |
| Tracking | W&B project `healthops-m3`, group `pipeline-<version>-<run_id>`; runs `transformer-w14-*`, `transformer-w30-*`, `compare-transformer-w14-w30-*` |
| Identity | jobs run as `healthops-vertex-training@<project>.iam.gserviceaccount.com` |

Parameters (all have defaults; see `healthops-pipeline --help`): data version, windows
(`14,30`), model, users subset, smoke, machine and accelerator types, W&B project.
The window list is a pipeline parameter, so `--windows 14,21,30` trains three models in parallel.

## Prerequisites

The one-time project setup in [model-training/README.md](../model-training/README.md)
(APIs, Artifact Registry, W&B secret, plain data copy, service accounts) plus:

```bash
gcloud services enable aiplatform.googleapis.com --project=ac215-project-509705   # Pipelines use the same API
# Pipelines record lineage in Vertex ML Metadata as the job service account, which therefore
# needs roles/aiplatform.user on the project (bucket access alone is enough for plain custom jobs).
gcloud projects add-iam-policy-binding ac215-project-509705 \
  --member="serviceAccount:healthops-vertex-training@ac215-project-509705.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user" --condition=None
```

At submit time the SDK prints a traceback saying the output bucket "does not belong to project"
and then creates the job anyway: it compares the project ID with the project number. The
`submitted ...` line that follows is the real outcome.

The GPU image. The training image is built with CPU torch by default; the pipeline's training
steps need the CUDA build (`TORCH_VARIANT=cu132`, about 3 GB larger). From the repository root:

```bash
IMAGE=us-east4-docker.pkg.dev/ac215-project-509705/healthops/model-training:$(git rev-parse --short HEAD)-cu132
gcloud builds submit src/model-training \
  --config=src/model-training/vertex/cloudbuild.yaml \
  --substitutions=_IMAGE=$IMAGE,_TORCH_VARIANT=cu132 \
  --service-account=projects/ac215-project-509705/serviceAccounts/healthops-build@ac215-project-509705.iam.gserviceaccount.com \
  --project=ac215-project-509705
```

The Vertex AI training quota `CustomModelTrainingL4GPUsPerProjectPerRegion` in `us-east4` must be
at least 1 (approved for this project on 2026-11-07). By default the training steps run one at a
time (`--parallelism 1`), which fits that quota; with a quota of 2 or more, `--parallelism 2`
trains both windows at the same time.

## Running

In this folder, `uv sync`, then from the repository root with `gcloud auth application-default
login` done (the submit script uses Application Default Credentials):

```bash
# 1. compile only: writes pipeline.json, no cloud call (also what the tests do)
uv run --directory src/ml-workflow healthops-pipeline --compile-only --out .local/pipeline.json

# 2. cheap first run: 200 users, tiny model settings, proves the GPU path end to end
IMAGE=$IMAGE make pipeline PIPELINE_ARGS="--smoke --users 200"

# 3. the real run: all users, windows 14 and 30
IMAGE=$IMAGE make pipeline

# CPU-only variant (the cpu image tag, no accelerator): slower, no GPU quota or capacity needed
IMAGE=${IMAGE%-cu132}-cpu make pipeline PIPELINE_ARGS="--accelerator-type none --machine-type n1-highcpu-16"
```

`make pipeline` is `uv run --directory src/ml-workflow healthops-pipeline --image $IMAGE
$PIPELINE_ARGS`. The script prints the run's console link and the output folder. With `--wait`
it blocks until the run finishes. Direct `gcloud` equivalents are not needed: the compiled
`pipeline.json` can also be submitted from the console (Vertex AI → Pipelines → Create run).

## Reading the results

```bash
RUN=<run_id printed at submit>
gsutil cat gs://ac215-project-509705-healthops-data/pipelines/runs/$RUN/comparison/COMPARISON.md
gsutil cat gs://ac215-project-509705-healthops-data/pipelines/runs/$RUN/w14/RESULTS.md
gsutil cat gs://ac215-project-509705-healthops-data/pipelines/runs/$RUN/w30/RESULTS.md
```

`COMPARISON.md` has, per window: test MAE, 90 % coverage and width by metric; the same by
cohort (paired, Apple Watch only, Fitbit only) and by history length; paired attribution; the
rule baselines; each run's configuration and W&B link. The decision rule for the window:
adopt the longer one only if paired users improve without short-history users getting worse.

## Observed run (2026-11-07)

Run of record `healthops-reconciliation-training-20261107t231608z` in `northamerica-northeast2`,
image `74f8f6e-cu132`: prepare 8.0 min, train w14 4.4 min (fit 202 s) and train w30 3.8 min
(fit 167 s) on `g2-standard-4` + NVIDIA L4, compare 40 s; all steps succeeded. W&B system metrics
report the L4 at 17 to 23 % average utilisation; the same jobs on CPU took 1,428 s and 2,745 s.
The two windows tie on sleep and resting heart rate, the 30-day model is worse on steps and
under-covers, and the 14-day window is kept. Tables, decision rule, GPU evidence and the CPU-fallback
incident of the first full run: [docs/evidence/pipeline-window-comparison.md](../../docs/evidence/pipeline-window-comparison.md).
Smoke runs the same day: `20261107T195526Z` (CPU, `us-east4`), `20261107T200632Z` and
`20261107T223548Z` (GPU, Toronto).

## Monitoring and logs

- **Pipeline run**: Vertex AI → Pipelines → Runs (`https://console.cloud.google.com/vertex-ai/pipelines/runs?project=ac215-project-509705`).
  Each step shows its state, duration, the custom job it created and a link to its logs.
- **Step logs** (Cloud Logging): every custom job writes its stdout/stderr, including the
  `healthops-train` progress lines (prepare counts, epochs, MAE per model) to Cloud Logging.
  From the console: Vertex AI → Training → Custom jobs → the job → *View logs*. From a terminal:

  ```bash
  gcloud ai custom-jobs list --region=us-east4 --project=ac215-project-509705 \
    --filter="displayName~train-transformer" --format="table(displayName,state,createTime)"
  gcloud ai custom-jobs stream-logs <job id> --region=us-east4 --project=ac215-project-509705
  # Logs Explorer query for one job (the console's "View logs" opens the same):
  #   resource.labels.job_id="<numeric job id>"
  gcloud logging read 'resource.labels.job_id="<numeric job id>"' \
    --project=ac215-project-509705 --limit=100 --format="value(timestamp,jsonPayload.message)"
  ```

  The program's own lines (`healthops-train: ...`, the result rows, `done in <n>s`) are logged by
  the worker as `workerpool0-0`; the service's provisioning and teardown lines sit beside them.

- **Training curves and metrics**: W&B project `healthops-m3`; filter by the group
  `pipeline-<version>-<run_id>`. Each training run logs the loss per epoch, the test metrics,
  the structure-recovery table and the saved model as an artifact; the compare run holds the
  three comparison tables.
- **Errors**: a failed step marks the pipeline run failed; the step's logs show the Python
  traceback. Cloud Logging keeps job logs for 30 days; the bucket keeps outputs until deleted.
- **Cost**: each GPU step runs on `g2-standard-4` (about $0.80 per hour); prepare and compare
  run on `n1-standard-8` for a few minutes.

## Tests

```bash
uv run pytest -q          # compiles the pipeline and checks its graph, GPU spec and the submit CLI
uv run ruff check . && uv run ruff format --check .
```

The tests never contact the cloud. They do not prove that a run succeeds; that is what the
smoke run (step 2 above) is for.

## Limits

- The W&B key is passed to the jobs as an environment variable, visible to project editors in
  the job specs (same as `vertex/submit.sh`). Reading it inside the container would be stricter.
  The key is required: Vertex rejects an empty environment value, so the pipeline has no offline
  mode and no entity override; runs go to the key's default W&B entity.
- Steps exchange data through GCS paths, not KFP artifacts, so the Vertex lineage view shows
  the custom jobs but not typed datasets/models.
- The pipeline trains one model family per run (`--model`); LightGBM and Diffusion remain
  available through `make train-vertex`.
