# Model Training

Cross-device reconciliation: estimate the true daily sleep, steps and resting heart rate behind
one or two device readings, with a 90% interval. Three model families are trained and compared
on the same held-out users of a synthetic version; the synthetic ground truth
(`latent_daily`, `device_bias_daily`) is what they are scored against.

| | |
|---|---|
| Input | `DATA_ROOT/synthetic/<version>/` (`latent_daily`, `observations_*`, `users`, `device_bias_daily`) and its manifest |
| Output | `--out` (default `.local/runs/<timestamp>-<version>`): `prepared/daily.parquet`, `models/<name>/`, `predictions.parquet`, `metrics.json`, `RESULTS.md`, `run.json`; W&B runs |
| Environment | `DATA_ROOT`, `TRAIN_DATA_VERSION`, `TRAIN_MODELS`, `TRAIN_USERS`, `TRAIN_SMOKE`, `TRAIN_OUT`, `WANDB_*`, `GIT_COMMIT` |
| Image | `python:3.12-slim` + CPU torch, LightGBM, W&B; the same image runs locally, in Compose and as the Vertex AI custom job |

## Pipeline inside one run

| Step | Module | What happens |
|---|---|---|
| prepare | `data.py` | Under the synthetic observation contract, steps are summed and resting heart rate is averaged per source-local day; sleep segments are summed per latent night. This is not a general online adapter: aggregation-kind validation and requested-timezone handling differ from the API. One row per user-day with both readings (NaN when a device recorded nothing), the truth, the applied bias, context flags, day of week, and history features: rolling 7/14/30-day means of past readings, count of past days, and the past gap between the two devices. Users are split 70/15/15 by a hash of their id; the split hash is recorded. |
| lightgbm | `lightgbm_model.py` | One booster per metric and quantile (q05, q50, q95), pinball loss, early stopping on the validation users. NaN readings stay NaN: a missing device is a branch at every split, never an imputed zero. |
| transformer | `transformer_model.py` | Encoder on 14-day windows of day features (standardised readings, masks, context, day of week), quantile heads for the last day, stride 1. |
| diffusion | `diffusion_model.py` | One conditional DDPM over the nine channels [truth, Apple, Fitbit] × 3 metrics on 14-day windows, conditioned on masks, context and day of week (cosine schedule, EMA, stride 7). Inference is inpainting: observed device channels are held fixed, truth channels are sampled with DDIM; the quantiles of the samples on the last day are the estimate and the interval. |
| evaluate | `evaluate.py` | On test users, after the rule "no estimate on a day without any reading": MAE, 90% coverage and width by metric, cohort and history length; structure recovery (slope of reading − estimate on the applied bias, correlation of the implied per-user offset with the drawn one); attribution on paired days (does the model move away from the device with the larger true error); rule-based baselines (one device, device average, global constant correction). |

`train.py` runs the steps in sequence, one W&B run per model plus a comparison run, and prints
`RESULTS.md`. For the Vertex AI Pipeline the same entry point runs one stage at a time:
`--prepare-only` writes the daily table and stops; `--prepared <dir>` trains from a table
written earlier instead of rebuilding it; `--run-suffix w30` and `--group` name the W&B runs.
`healthops-compare` (`compare.py`) merges several runs into one table (`COMPARISON.md`,
`comparison.json`, a W&B run) by run label: overall, by cohort, by history length, attribution.

The Transformer's context window is a configuration value (`window`, default 14 days):
`--config '{"transformer": {"window": 30}}'` trains a 30-day model with every other setting
unchanged; a user with fewer days than the window is zero-padded with the valid flag at 0.
The 14-vs-30-day comparison on the full cohort (Vertex AI Pipeline, NVIDIA L4) keeps the 14-day
default: the windows tie on sleep and resting heart rate, 30 days is worse on steps and under-covers.
Tables and the decision rule: [docs/evidence/pipeline-window-comparison.md](../../docs/evidence/pipeline-window-comparison.md).

## Model selection

Three families were trained and scored under identical conditions (same prepared table, same
user split by ID hash, same seed, same held-out users, same criteria) so that the choice between
them would be an observation, not a preference. Observed on the full `synthetic-v0.2` cohort
([docs/evidence/model-training-results.md](../../docs/evidence/model-training-results.md)):

- **Diffusion** is eliminated: worse than the rule baselines on sleep (30.8 vs 13.0 min) and steps
  (1,682 vs 1,209), 90 % intervals four to five times wider than the other two with 0.98 to 0.99
  coverage (hedging), attribution near chance, and only part of the constant device bias removed.
- **LightGBM** and the **Transformer** tie: both beat every rule baseline, both keep the 90 %
  promise, both remove the constant device bias in full; what remains is the per-user offset that
  a single device cannot reveal. LightGBM is marginally better calibrated; the Transformer is
  marginally better on steps and resting heart rate.

The Transformer is the family developed further and served, for reasons the scores alone do not
decide: it is a time-series model that attends over the ordered days with their masks and context,
where LightGBM sees one day plus fixed rolling summaries; its context window is therefore an
explicit design parameter that can be tested (the 14 vs 30-day pipeline experiment, 14 kept); it
is the better of the two on steps, the one metric not yet at the noise floor; it extends to
next-day prediction and to windowed tool inputs without architectural change; and it trains in
minutes on one L4. LightGBM remains the calibrated baseline in every comparison. Nothing here was
chosen before the comparison was run.

## Local smoke test (before any cloud job)

From the repository root, with a built synthetic version under `data/` (see `data/README.md`):

```bash
make train-smoke          # 20 random users, tiny model settings, all three models, about a minute
```

This is `TRAIN_USERS=20 TRAIN_SMOKE=true docker compose --profile train run --rm --build model-training`.
Outputs land in `src/model-training/.local/runs/`. Without Docker, in this folder:

```bash
uv sync
uv run healthops-train --data-root ../../data --version synthetic-v0.2 --users 20 --smoke
```

Smoke settings (`train.py: SMOKE`) only prove that the pipeline runs end to end; their numbers
mean nothing. `make train` (or the command without `--users`/`--smoke`) is the real run.
Per-model overrides: `--config '{"diffusion": {"epochs": 60}}'`.

## Serverless training on Vertex AI

The same image runs as a Vertex AI custom job. `src/model-training/vertex/submit.sh` builds the
image with Cloud Build (no local Docker), pushes it to Artifact Registry, fills
`vertex/job.template.yaml` and submits the job; `make train-vertex` is the shortcut.

Project setup, once, by someone with owner or editor rights (project `ac215-project-509705`,
region `us-east4`, bucket `ac215-project-509705-healthops-data`):

```bash
gcloud config set project ac215-project-509705
gcloud services enable aiplatform.googleapis.com artifactregistry.googleapis.com \
    cloudbuild.googleapis.com secretmanager.googleapis.com
gcloud artifacts repositories create healthops --repository-format=docker --location=us-east4
# W&B key for online runs (from https://wandb.ai/authorize); never in Git
printf '%s' "<wandb key>" | gcloud secrets create wandb-api-key --data-file=-
# A plain copy of the synthetic version next to the DVC store: this is what the job reads
gsutil -m rsync -r data/synthetic/synthetic-v0.2 gs://ac215-project-509705-healthops-data/data/synthetic/synthetic-v0.2
gsutil cp data/manifests/synthetic-v0.2.yaml gs://ac215-project-509705-healthops-data/data/manifests/
```

The job runs as `healthops-vertex-training@<project>.iam.gserviceaccount.com` (override with
`SERVICE_ACCOUNT`), which needs read and write on the bucket (`roles/storage.objectAdmin`). The
image is built by `healthops-build@<project>.iam.gserviceaccount.com` (override or empty
`BUILD_SERVICE_ACCOUNT` for the Cloud Build default), which needs Artifact Registry writer and
log writer. The submitter needs `roles/iam.serviceAccountUser` on both accounts,
`roles/aiplatform.user`, `roles/cloudbuild.builds.editor` and `roles/secretmanager.secretAccessor`.

Runs, cheapest first:

```bash
USERS=200 SMOKE=true make train-vertex                  # proves the cloud path, a few minutes
MODELS=lightgbm make train-vertex                        # full data, one model
make train-vertex                                        # full data, all three models
MACHINE_TYPE=n1-highcpu-32 make train-vertex             # more CPUs for the neural models
```

How the job sees data and writes results: Vertex mounts the bucket at `/gcs/<bucket>`, so
`DATA_ROOT=/gcs/<bucket>/data` and the outputs land in `gs://<bucket>/training/runs/<job>/`
(`RESULTS.md`, `metrics.json`, `models/`, `predictions.parquet`, `run.json`). The W&B key is read
from Secret Manager at submit time and passed to the job as an environment variable; without
the secret the run is offline and its files are written to `gs://<bucket>/training/wandb/`.
Logs: `gcloud ai custom-jobs stream-logs --region=us-east4 <job id>` or the Vertex AI console.

The image carries CPU torch by default; the default machine is `n1-highcpu-16`. For a GPU job
the Dockerfile build argument `TORCH_VARIANT=cu132` replaces the locked torch with the CUDA wheel
of the same version (image tag `<commit>-cu132`, about 3 GB larger), and the job spec gets the
accelerator:

```bash
ACCELERATOR_TYPE=NVIDIA_L4 MACHINE_TYPE=g2-standard-4 MODELS=transformer make train-vertex
```

`submit.sh` picks the CUDA build whenever `ACCELERATOR_TYPE` is set. The Vertex training quota
`CustomModelTrainingL4GPUsPerProjectPerRegion` (us-east4) must be at least 1. The multi-step
version of this, one GPU job per window in parallel, is the Vertex AI Pipeline in
[src/ml-workflow](../ml-workflow/README.md).

## Weights & Biases

- With `WANDB_API_KEY` in `.env` (or exported) runs are logged online to `WANDB_PROJECT`
  (default `healthops-m3`) under `WANDB_ENTITY`.
- Without a key, runs are written offline under `src/model-training/.local/wandb/` and can be
  uploaded later with `uv run wandb sync .local/wandb/offline-run-*`.
- `WANDB_MODE=disabled` turns tracking off (used by the tests).
- Every run carries the data version and its manifest sha256, the split hash, the Git commit,
  the model configuration, training curves, the test metrics, the structure-recovery table and
  the saved model as an artifact.

## Tests

```bash
uv run pytest -q          # tiny generated cohort: data assembly, every model, evaluation
uv run ruff check . && uv run ruff format --check .
```

## Limits

- Ground truth is the synthesizer's; the evaluation proves that known structure is recovered
  with calibrated uncertainty, not real-device accuracy (`docs/device-bias.md`).
- A single-device user's personal offset is not identifiable from one channel; intervals are
  wider there by design and reported per cohort.
- Daily values only; intra-day samples are not modelled.
