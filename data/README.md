# HealthOps data

This folder holds the **description** of every dataset version: manifests (in Git) and DVC
pointers (`*.dvc`, in Git). The dataset contents themselves are never committed; they live in
the DVC remote and are rebuilt or pulled on demand.

```text
data/
├── README.md                       this file
├── manifests/<version>.yaml        one per built version (written by the pipeline)
├── raw/<raw-version>/              downloaded + Synthea inputs        (DVC: raw/<version>.dvc)
├── processed/canonical/<version>/  canonical parquet                  (DVC: processed/canonical/<version>.dvc)
└── synthetic/<version>/            synthetic cohort parquet           (DVC: synthetic/<version>.dvc)
```

## Getting the data

Two ways, from the repository root:

1. **Pull a version someone already built** from the DVC remote: `make dvc-pull`. The default
   remote (`.dvc/config`) is the team's Cloud Storage bucket
   `gs://ac215-project-509705-healthops-data/healthops-dvc-store` in the GCP project, which needs
   a Google login with read access to the bucket (`gcloud auth application-default login`).
   Without cloud access, select the offline remote, a copy of the `healthops-dvc-store` folder
   next to the repository: `uvx --from "dvc[gs]==3.67.1" dvc remote default --local local`.
   The same bucket is the dataset source for Vertex AI training jobs.
2. **Rebuild from the public sources** (Docker with Compose; downloads about 95 MB):
   `cp .env.example .env && make run`

Sources and their terms (attribution is required by both licenses):

| Source | Role | Files used | License |
|---|---|---|---|
| NetHealth Project Data, University of Notre Dame, Zenodo record [21904040](https://zenodo.org/records/21904040), DOI 10.5281/zenodo.21904040 | Fitbit (provider `google_health`) | `FitbitActivity(1-30-20).csv`, `FitbitSleep(1-30-20).csv` | CC BY 4.0 |
| BIDSleep Apple Watch Dataset, PhysioNet v1.0.1, DOI [10.13026/rees-1092](https://physionet.org/content/bidsleep-dataset/1.0.1/) (Song, Zhang, Zhou, Dutta; companion paper in IEEE TBME 2025, doi 10.1109/TBME.2025.3612158) | Apple Watch (provider `healthkit`) | `hr.csv`, `labels.mat` per night; `motion.csv` only with `BIDSLEEP_INCLUDE_MOTION=true` (+~28 GB) | ODC-By 1.0 |
| [Synthea](https://github.com/synthetichealth/synthea) v3.3.0 | Synthetic patients (clinical context) | `patients.csv`, `conditions.csv`, `medications.csv` | Apache 2.0 |

The data-collector verifies every downloaded file against the checksum its publisher lists
(MD5 from Zenodo, SHA-256 from PhysioNet's `SHA256SUMS.txt`) and refuses mismatches.

## Versions

| Name pattern | Stage | Written by | Contents |
|---|---|---|---|
| `raw-vN` | raw | synthea-generator + data-collector | Verified source files and Synthea tables |
| `canonical-vX.Y` | canonical | data-processor | `nethealth.parquet`, `bidsleep.parquet`, `users.parquet` |
| `synthetic-vX.Y` | synthetic | data-synthesizer | `observations_healthkit.parquet`, `observations_google_health.parquet`, `users.parquet`, `latent_daily.parquet`, `context_events.parquet`, `missingness_events.parquet`, `calibration.json`, `context_library.json`, `llm_cache.jsonl` |

`raw` counts snapshots of external data (`v1`, `v2`, ...). `canonical` and `synthetic` are outputs
of our own code and configuration, numbered major.minor: a minor step for new content with the
same schema, `v1.0` once the schema is frozen for downstream use.

Committed versions (`raw-v1`, `canonical-v0.1` and `synthetic-v0.1` built from Git commit `e3bc4ea`, `synthetic-v0.2` from `6929361`; details in `manifests/`):

| Version | Built | Files | Size | Contents |
|---|---|---|---|---|
| `raw-v1` | 2026-10-18 | 515 | 227 MB | NetHealth Fitbit CSVs, BIDSleep `hr.csv` + `labels.mat` for 253 nights (47 subjects, no motion), Synthea tables for 5000 patients |
| `canonical-v0.1` | 2026-10-18 | 3 | 49 MB | NetHealth 1,399,623 rows (623 users), BIDSleep 1,603,981 rows (47 users) |
| `synthetic-v0.1` | 2026-10-19 | 9 | 610 MB | 5000 users (2000 Apple Watch only, 2000 Fitbit only, 1000 paired); LLM context with `qwen3:4b`: 500 personal schedules and a 38-template event library; no rows dropped by validation |
| `synthetic-v0.2` | 2026-10-31 | 10 | 609 MB | Same 5000 users, trajectories, context and missingness as `synthetic-v0.1` (the LLM answers were replayed from its cache; one of the 500 schedules needed a fresh call), plus a systematic device-bias layer on sleep, steps and resting heart rate from literature priors ([docs/device-bias.md](../docs/device-bias.md)); every applied value is in `device_bias_daily.parquet`; no rows dropped by validation |

To add a version: set a new name in `.env`, run `make run`, then `make dvc-track` (pushes the
data to the bucket), and commit `data/manifests/` with the new `.dvc` files.

Rules:

- Every container reads `INPUT_DATA_VERSION` and `OUTPUT_DATA_VERSION` from its environment and
  logs both, with the Git commit, when it starts.
- A version is **immutable** once its manifest exists. Rerunning with identical configuration
  is a no-op; a changed config or changed files stop the run until you choose a new
  `OUTPUT_DATA_VERSION`. Generate a new version every time the data changes.
- A version depends on exactly one input version, recorded with the SHA-256 of the input
  manifest, so the chain synthetic -> canonical -> raw can always be traced.

## Manifests

Each `manifests/<version>.yaml` records: version, stage, description, creation time, Git commit
(`-dirty` when the tree had uncommitted changes), the input version and its manifest hash, the
SHA-256 of every config file used, run parameters, source URLs/DOIs/licenses (raw), summary
statistics (rows per provider/metric, users, dropped rows by reason, missingness events) and
the size and SHA-256 of **every output file**. The `.dvc` pointer stores the directory hash and
a `desc` line naming the manifest and its SHA-256.

## Canonical schema

Real and synthetic observations share one parquet schema: the ingestion API's
`ObservationIn` fields plus a user and three provenance columns, so any row can be posted to
`POST /v1/observations/batch` unchanged.

| Column | Meaning |
|---|---|
| `user_id` | `nethealth-<egoid>`, `bidsleep-<subject>` or `synthetic-<n>` |
| `provider` | `healthkit` (Apple Watch) or `google_health` (Fitbit) |
| `source_record_id` | Stable identity within user, provider and metric |
| `source_device` | Device label, e.g. `Fitbit (NetHealth)`, `Apple Watch (synthetic)` |
| `metric`, `value`, `unit` | Canonical metric with its fixed unit (bpm, count, ms, s, kcal) |
| `started_at`, `ended_at` | UTC; equal for samples, an interval for totals and summaries (the plan's "timestamp") |
| `source_timezone` | IANA zone of the recording |
| `aggregation` | `sample`, `interval_total`, `daily_total` or `daily_summary` |
| `provenance_dataset` | `nethealth`, `bidsleep` or `synthetic` |
| `provenance_version` | Data version the row was derived from |
| `provenance_source` | Source file, or generator component (`apple_watch_model`, `fitbit_model`) |

Mapping choices are documented at the top of `src/data-processor/processor/nethealth.py` and
`bidsleep.py`; synthetic generation is described in `src/data-synthesizer/README.md`.
