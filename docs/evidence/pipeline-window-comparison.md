# Vertex AI Pipeline on GPU: Transformer context window, 14 vs 30 days

Observed 2026-11-07. One run of the ML workflow in [src/ml-workflow](../../src/ml-workflow/README.md)
on Vertex AI Pipelines, region `northamerica-northeast2` (Toronto), project `ac215-project-509705`.
All data is synthetic (`synthetic-v0.2`); ground truth is the synthesizer's own.

## The run of record

| | |
|---|---|
| Pipeline run | `healthops-reconciliation-training-20261107t231608z` (run id `20261107T231608Z`) |
| Image | `us-east4-docker.pkg.dev/ac215-project-509705/healthops/model-training:74f8f6e-cu132`, built by Cloud Build from commit `74f8f6e` with `TORCH_VARIANT=cu132` (torch 2.15.0+cu132) |
| Steps | prepare on `n1-standard-8` (CPU) → train w14 → train w30, each on **`g2-standard-4` + 1 × NVIDIA L4**, one at a time → compare on `n1-standard-8` |
| Durations | prepare 8.0 min; train w14 4.4 min (fit 202.4 s, 30 epochs); train w30 3.8 min (fit 166.5 s, 20 epochs, early stopping); compare 40 s. Whole pipeline 25.6 min |
| Outputs | `gs://ac215-project-509705-healthops-data/pipelines/runs/20261107T231608Z/{prepared,w14,w30,comparison}/` |
| W&B | project `healthops-m3`, group `pipeline-synthetic-v0.2-20261107T231608Z`, five runs: `transformer-w14-synthetic-v0.2` (`7331sv3j`) and `transformer-w30-synthetic-v0.2` (`xumnr6fr`), each training step's own `comparison-w14` / `comparison-w30` run against the baselines, and the compare step's `compare-transformer-w14-w30-synthetic-v0.2` (`oag0ep19`) |
| Full tables | the pipeline's `comparison/COMPARISON.md`, copied to [pipeline-20261107T231608Z-COMPARISON.md](pipeline-20261107T231608Z-COMPARISON.md) |

Both models were trained from the same prepared table (209,733 training windows; 5,000 users split
70/15/15 by user-ID hash; 756 test users) with the same configuration, `d_model=64, layers=2,
epochs=30, stride=1`, differing only in `window`.

## Evidence that training ran on the GPU

Three independent observations, all for this run:

1. **W&B system metrics** (queried through the W&B API): each training run reports
   `gpu: NVIDIA L4`, with 15 utilisation samples averaging 17.5 % (max 23 %) for w14 and 12
   samples averaging 23.1 % (max 33 %) for w30. Utilisation is moderate because the model is small
   (64-wide, two layers) and batches of 256 windows do not saturate an L4.
2. **Fit time against the CPU run of the same job.** Earlier the same day the identical pipeline ran on
   the same machines but, through an image fault, trained on their CPUs (see "Incident" below):
   w14 1,427.6 s and w30 2,744.9 s. On the GPU: 202.4 s and 166.5 s, seven and sixteen times faster.
3. **A device probe job** (`gpu-check-2`, same image, same machine type) wrote
   `torch 2.15.0+cu132 cuda_available=True device=NVIDIA L4 matmul_8192_seconds=0.326` to the bucket;
   an 8192 × 8192 matrix product takes tens of seconds on a CPU.

Every later image also logs `torch <version>, cuda available=<bool>, device=<name>` as the first
line of each job (`training/train.py`).

## Results on held-out test users

### Overall (756 users)

| Window | Sleep MAE (min) | Steps MAE | Resting HR MAE (bpm) | Coverage of the 90 % interval (sleep / steps / HR) | Steps attribution |
|---|---|---|---|---|---|
| Best rule baseline | 13.00 | 1,209 | 1.21 | n/a | 0.53 |
| **14 days** | 12.17 | **987** | 1.18 | **0.93 / 0.91 / 0.93** | 0.75 |
| **30 days** | 12.13 | 1,006 | 1.18 | 0.88 / 0.90 / 0.89 | 0.73 |

### Paired users (both devices), where a longer window could help

| Window | Sleep MAE | Steps MAE | Resting HR MAE | Coverage (sleep / steps / HR) |
|---|---|---|---|---|
| 14 days | 11.36 | **630** | 1.01 | 0.93 / 0.93 / 0.92 |
| 30 days | 11.27 | 668 | 1.03 | 0.89 / 0.93 / 0.88 |

### Users with fewer than 7 days of history

| Window | Sleep MAE | Steps MAE | Resting HR MAE | Coverage (sleep / steps / HR) |
|---|---|---|---|---|
| 14 days | 12.00 | 1,005 | 1.19 | 0.93 / 0.90 / 0.93 |
| 30 days | 12.05 | 1,027 | 1.18 | 0.87 / 0.88 / 0.89 |

MAE is the mean absolute error of the median estimate; coverage is the share of test days whose true
value lies inside the model's 90 % interval (target 0.90); attribution is how often, on days where
the two devices disagree, the estimate moves away from the device with the larger true error.

## Decision: keep the 14-day window

The rule set before the run: adopt the longer window only if paired users improve by more than
about 3 % without short-history users getting worse, and without losing calibration.

- **Accuracy.** Overall the windows tie on sleep and resting heart rate (within 0.5 %); on steps the
  30-day model is 2 % worse overall and **6 % worse for paired users** (668 vs 630), the one group
  where more context could have helped.
- **Calibration.** The 14-day model keeps the 90 % promise everywhere (0.91 to 0.94). The 30-day
  model's intervals are narrower but under-cover: 0.88 on sleep overall, 0.84 for Fitbit-only sleep,
  0.87 to 0.89 for users with little history. Narrower intervals that miss the truth more often are
  not an improvement.
- **Short-history users** are not helped by the longer window (12.05 vs 12.00 min, 1,027 vs 1,005
  steps) and lose coverage.
- **Cost and availability.** A 30-day model needs a month of data before a window is full, against
  two weeks; it also stopped early at epoch 20, so the extra context did not give it more to learn.

Why more context does not help here: the error that remains after the models is the per-user device
offset. For a single-device user it is not identifiable from any length of history (a user who truly
sleeps 20 minutes less and a device that under-reads by 20 minutes produce identical data). For
paired users it is identifiable from the gap between the two devices, and 14 days of that gap already
pin it down. The flat error across history-length buckets in both windows says the same.

Run-to-run noise: the 14-day model of this run and the one trained on CPU earlier the same day
(same data, same split, same seed, different device) differ by 1 % on sleep (12.17 vs 12.32), 1 % on
steps (987 vs 994) and 1 % on resting heart rate. Differences of that size between windows are noise;
the 6 % steps gap for paired users and the coverage gap are not.

## Incident: the first full run trained on CPU inside GPU machines

Run `20261107T203952Z` (same pipeline, image `ac38f58-cu132`) completed with the training steps on
`g2-standard-4` + L4, but W&B recorded no GPU and the fit times (1,427.6 s and 2,744.9 s) matched CPU
training. Cause: the image, based on `python:3.12-slim`, did not have `/usr/local/nvidia/lib64` on its
library path, which is where Vertex AI mounts the host's NVIDIA driver, so CUDA torch could not load
`libcuda` and fell back to the CPU without an error. Fix (commit `74f8f6e`): `LD_LIBRARY_PATH` and the
NVIDIA container variables in the Dockerfile, plus the device log line. The comparison of that run
reached the same decision (keep 14); its tables remain in the bucket under its run id.

Smoke runs the same day, 200 users with tiny settings, used to prove each stage before the full runs:
`20261107T195526Z` (CPU, `us-east4`), `20261107T200632Z` and `20261107T223548Z` (GPU, Toronto). Two
smoke runs submitted to `us-east4` and `us-central1` waited for L4 capacity and were cancelled.

## Where to look

- Vertex AI Pipelines run page (graph of the four steps, each linking to its custom job and logs):
  `https://console.cloud.google.com/vertex-ai/locations/northamerica-northeast2/pipelines/runs/healthops-reconciliation-training-20261107t231608z?project=ac215-project-509705`
- Custom jobs of the run: Vertex AI → Training → Custom jobs, region Toronto, names ending in
  `20261107T231608Z`; the training jobs show machine `g2-standard-4`, accelerator NVIDIA L4 × 1.
- W&B: the three runs above; the System tab of a training run shows the GPU and its utilisation.
- Logs: Cloud Logging under each custom job (console *View logs*, or `gcloud ai custom-jobs
  stream-logs <job>`); see the monitoring section of the ml-workflow README.
- Screenshots: [docs/screenshots](../screenshots/README.md).

## Limits

- One run per window, one seed. Differences of about 1 % are within run-to-run variation.
- Synthetic ground truth from literature priors: the comparison is about which window recovers the
  synthesizer's structure, not about real-device accuracy.
- Fit time includes window building on the CPU, which is why GPU utilisation is moderate rather than
  high; a larger batch or a bigger model would raise it.
- The W&B key is passed to the jobs as an environment variable readable by project editors; reading
  it inside the container would be stricter.
