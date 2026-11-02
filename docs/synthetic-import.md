# Synthetic cohort import (Milestone 3, step 1)

Connect a versioned synthetic cohort to the application's PostgreSQL observations and existing
authenticated history/tools endpoints. This is an operator job in `api-service`, not a new
public API or a model-training job. It calls the same `ObservationIn` validation and
`ingest_batch` repository function as `POST /v1/observations/batch`; it does not upload over
HTTP. A separate smoke script checks the resulting accounts over HTTP.

## Restore the input

This advanced path requires the team's synthetic parquet dataset and manifest, which are
not distributed in this portfolio. For a self-contained demo use [the public fixture quickstart](quickstart.md).

In a separately authorized checkout of the [team repository](https://github.com/HuihanCui/ac215_Megatron),
follow its DVC instructions to restore `synthetic-v0.1`. Copy only that synthetic dataset directory
to `data/synthetic/synthetic-v0.1/` here and its manifest to `data/manifests/synthetic-v0.1.yaml`.
The manifest and the three parquet files must match their recorded checksums. Never substitute
raw/canonical personal records. This repository does not configure or grant access to a DVC remote.
Keep the copied input local; the importer is optional and the application does not need it to start.

## Preview without a database

Run host commands in `src/api-service`:

```bash
uv sync --extra synthetic
uv run --extra synthetic python -m healthops.synthetic_import \
  --dataset ../../data/synthetic/synthetic-v0.1 \
  --manifest ../../data/manifests/synthetic-v0.1.yaml \
  --output-dir .local/synthetic-import/synthetic-v0.1
```

Without `--apply`, the job validates the data and writes `dry-run.json`; it creates no accounts,
tokens or database connections. Default selection is the first user ID in each of the three
cohorts, sorted lexicographically. This is a reproducible smoke sample, not a representative
evaluation set. Override with repeated `--user-id` options or `--users-per-cohort N`.

The manifest's SHA-256 and byte sizes are checked for `users.parquet` and both observation
files. Only selected rows are scanned into the import plan. Every selected observation is
validated, including synthetic provenance and provider/cohort consistency. Rows are spooled
to a temporary file before any database writes. The default cap is 100,000 selected records;
exceeding it fails, never silently truncates. Optional `--start-date` / `--end-date` are inclusive
and use each row's source-local **start date**, including for overnight intervals.

## Import and verify

From the repository root:

```bash
make app
make synthetic-import
```

`synthetic-import` is a one-shot Compose job built from `src/api-service`, with the optional
parquet dependencies enabled. It waits for migrations, mounts data read-only, and writes local
reports/credentials under `src/api-service/.local/synthetic-import/<version>/`.
`SYNTHETIC_DATA_VERSION` in Compose selects the version (default `synthetic-v0.1`).
It does not start the generator, contact Google Health, or call an LLM.

Alternatively, append `--apply` to the host preview command. Host mode uses loopback PostgreSQL
on port 55432 by default. The importer deliberately ignores `.env` and
`HEALTHOPS_DATABASE_URL`. Its separate `HEALTHOPS_SYNTHETIC_DATABASE_URL` override accepts only
local PostgreSQL hosts and the `healthops` / `healthops_test` database names; cloud imports are
not implemented. Run only against the known local Compose instance, not an SSH tunnel.

Then, in `src/api-service`:

```bash
uv run --extra synthetic python scripts/smoke_synthetic_import.py \
  --directory .local/synthetic-import/synthetic-v0.1
```

The smoke check verifies `/v1/me`, exact series counts from `/v1/tools/availability`, and a
seven-day `/v1/tools/daily-summary` response for each imported account. It prints no token or
measurement value. It requires the API to use the same database as the importer. Its exact
count check is intended for clean synthetic accounts and one source timezone per account.

## Identity, provenance and retries

- A deterministic UUID maps `(version, manifest SHA-256, cohort user ID)` to a **new synthetic
  API account**. No `--target-user` option exists. Provider, source record ID, metric, units,
  aggregation, device and timestamps are preserved. Imported account names start with
  `Synthetic`. A changed manifest produces a separate set of accounts; it does not update
  an earlier dataset in place.
- Parquet `user_id` and provenance columns do not enter `ObservationIn`. The local report
  preserves the source-to-account mapping, manifest hash, date bounds, timezones and per-series
  counts. The API continues deriving identity from the bearer token, never the request body.
- Credentials are created using the existing operator token code, with seven-day expiry and
  mode `0600`. Replays reuse the same credential file; they do not issue more tokens. Expired
  or revoked tokens are marked in the report. Renew with the existing `healthops issue-token`
  command to a new file, then use that token in the app. The smoke script continues using the
  file named in the report, so update that local file reference if you renew credentials.
- Batches contain at most 500 records per user. Replay skips identical observations through
  the existing uniqueness constraint; conflicting payloads fail rather than overwrite.
  Completed batches remain if a later batch fails. Fix the cause and rerun the same selection;
  earlier batches become duplicates. `report.json` is atomically updated after each committed
  batch and records `running`, `failed` or `completed`. A process crash can leave `running`
  and counts one batch behind; replay recovers from the database, not those counters.
- Run one importer at a time per selection/output directory. Concurrent operator runs and
  database deletion/restore reconciliation are not supported. Never delete or truncate the
  application database to retry an import.

## Use the imported account in the app

Open the local `report.json`, choose a cohort, and use the `token` from its named credential
file in the Simulator Setup screen with API URL `http://127.0.0.1:8000`. These are separate
accounts from the Simulator HealthKit seeder. Do not sync personal HealthKit records or connect
a real Google account into these synthetic accounts.

**The cohort is historical.** The job does not shift timestamps to today. A current-day or
last-30-days chart may correctly be empty. Use explicit dates from `first_date` / `last_date`
in assistant questions or Swagger tool queries; this step does not add a historical date
picker to the iOS Overview screen. The assistant still requires the configured model server;
successful import and deterministic tools do not prove model-answer accuracy.

For example, after the default `synthetic-v0.1` selection, the paired user has records from
2026-05-14 through 2026-08-09. Ask about a window within those dates. Do not use the generator's
hidden `latent_daily` values to fill missing observed data.

## Code and tests

- [Observed local run](evidence/synthetic-import-run.md): first import, replay, HTTP checks
  and one assistant connectivity check against the imported history.
- [Importer](../src/api-service/healthops/synthetic_import.py): checksums, selection, conversion,
  account provisioning and replayable batches.
- [HTTP smoke check](../src/api-service/scripts/smoke_synthetic_import.py).
- [Importer tests](../src/api-service/tests/test_synthetic_import.py): generated temporary
  parquet, provenance/validation failures, limits, dates, isolated PostgreSQL, replay and
  authenticated account isolation. No dataset contents or credentials are committed.

```bash
# In src/api-service: optional dependencies are required to exercise importer tests.
uv run --extra synthetic pytest -q tests/test_synthetic_import.py
# From the root: real PostgreSQL tests use only the separate healthops_test database.
docker compose --profile test build test
docker compose --profile test run --rm test
```

Large-cohort evaluation, cloud ingestion, fine-tuning and automatic model deployment are
separate future increments. This job imports observed records only, not clinical profiles,
context events, latent trajectories or RAG reference documents.
