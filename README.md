# HealthOps
Harvard APCOMP215 Course Project

## Current status (2026-10-05)

A single-tester development system, verified end to end on a physical iPhone against a
single-GPU cloud VM. It is not a publicly deployable or multi-user health platform.

- **Server** (`services/ingestion-api/`): FastAPI + PostgreSQL ingestion with replay-safe
  deduplication and conflict detection; three deterministic, user-scoped history tools
  (`availability`, `daily-summary`, `period-comparison`) that never fill missing data with zero
  and refuse requests above explicit limits (93-day windows, 10000 records) instead of
  truncating; a single-turn assistant (`POST /v1/assistant/ask`) in which a local open-weight
  model may only call those tools, the server resolves dates and injects identity, and the
  model never writes numbers: Python builds data cards, the model cites them in three finding
  types, the server verifies each finding and renders fixed templates (unverifiable
  explanations degrade to a fixed fallback; refusals are fixed sentences by category); a
  server-side **Google Health connector** (OAuth on the server, encrypted refresh tokens,
  user-triggered sync of Fitbit steps, resting heart rate, HRV RMSSD and sleep; continuous
  heart rate is not synced; Apple Health copies inside Google Health are skipped).
- **iOS** (`ios/HealthOpsUploader/`): HealthKit upload with consent and replay-safe
  incremental sync; an Overview tab with one deterministic chart per metric and separate
  Apple Health / Google Health lines; an Ask tab that decodes only display fields and treats
  unknown statuses as unverified; a Setup tab for server, Health access, consent, sync and
  the Google Health connection.
- **Deployment** (`infra/cloud/`, `scripts/cloud/`): one GCP L4 VM running Caddy (automatic
  HTTPS), the API, PostgreSQL and Ollama (`qwen3:8b`) in Compose, with a dedicated network
  exposing only 443, per-user rate limits and two-hour auto-stop. Stopped between sessions.
- **Evaluation** (`ml/eval/`): synthetic question sets and in-process evaluation runs;
  observed numbers and failures are in [ml/eval/RESULTS.md](ml/eval/RESULTS.md).
- **Reference library** (`knowledge/`): licence-checked public sources and project notes,
  chunked and embedded with `nomic-embed-text` into pgvector; the assistant's
  `lookup_reference` tool quotes passages verbatim within each source's quotation limit, and
  reference material is never combined with the person's values into a judgement.
- **Not included**: conversation storage, background or scheduled sync, deletion sync,
  production identity, vLLM or Kubernetes.

The debug page `GET /debug/assistant` is a local verification interface, not the iOS feature.
The existing `AppleHealthKitMVP` prototype outside this repository was inspected for mapping
and API requirements but was not copied or modified. All committed sample data is synthetic.

## Development history (the status described in each paragraph is the status at that time)

The first slice was the **Python FastAPI + PostgreSQL** backend, with Pydantic validation,
SQLAlchemy persistence, Alembic migrations, a locked uv environment and Docker Compose,
accepting canonical observations and retrieving the authenticated user's history.
The second slice added the three deterministic history tools in the same process, verified
with synthetic records only; at that time no device-to-tool path had been exercised.
The third slice added the iOS upload adapter; the loop (HealthKit read -> consent -> FastAPI ->
PostgreSQL -> tools) was first exercised in the iOS Simulator with synthetic samples.
The fourth slice added the assistant with a scripted-model test suite and a separate model
evaluation; the cards-and-claims increment then removed model-written numbers from answers.
Later increments added the user-facing iOS screens, the cloud VM deployment (first with
synthetic accounts, then with the owner's own HealthKit data under explicit consent) and the
Google Health connector, which was verified once against the real service.

Start with the [API contract](docs/api-contract.md) and
[architecture and repository boundaries](docs/architecture.md).

## Quick start (Mac/Linux)

Prerequisites: Docker Engine/Desktop with Compose, `uv`, and Python 3.12 (uv can install it).
Commands below run from the repository root. Ports 8000 and 55432 must be available.

```bash
uv sync --locked
docker compose up -d --build
docker compose ps
```

Compose starts PostgreSQL, runs `alembic upgrade head` as a one-shot migration, then starts
the API. PostgreSQL and API ports bind to **127.0.0.1 only**. The fixed database password
in Compose is local-only; never expose this stack on the internet or a shared network.

- API documentation: http://127.0.0.1:8000/docs
- Process liveness: http://127.0.0.1:8000/health/live
- Database/schema readiness: http://127.0.0.1:8000/health/ready

Bootstrap one local demo user (no public registration endpoint):

```bash
PYTHONPATH=services/ingestion-api/src uv run healthops create-user --name demo --output .local/demo-credentials.json
uv run python scripts/smoke.py
```

The credential file is created with permissions `0600`, ignored by Git, and never printed by
the smoke script. Creating the same output path twice fails instead of overwriting credentials.
The script inserts two synthetic observations, replays them, queries history, and calls the
three tool endpoints. Each run uses fresh synthetic IDs and leaves its records in the demo database.

To explore protected routes in Swagger, read your local credential file yourself and paste
its `token` value into **Authorize**. Do not share the file or paste credentials into issues/chat.
The seven-day expiry is our local development policy, **not** Google's OAuth token lifetime.
After expiry, issue a new token for the same `user_id` to retain access to existing history:

```bash
PYTHONPATH=services/ingestion-api/src uv run healthops issue-token --user-id YOUR_LOCAL_USER_UUID --output .local/renewed-credentials.json
uv run python scripts/smoke.py --credentials .local/renewed-credentials.json
```

Issuing a new token does not revoke earlier tokens. This CLI is a trusted local operator tool,
not a production login or token-refresh endpoint.
Revoke a token using the `token_id` from that file:

```bash
PYTHONPATH=services/ingestion-api/src uv run healthops revoke-token --token-id YOUR_LOCAL_TOKEN_UUID
```

The host-side CLI defaults to `localhost:55432`; containers use `db:5432`. Optional local
overrides are described in `.env.example`. `.env` is never committed or copied into the image.

Host commands explicitly set `PYTHONPATH=services/ingestion-api/src`: on this Mac, the generated editable-install
`.pth` file was repeatedly marked hidden, which made Python ignore it. This local workaround
loads the checked-out source without relying on that file. Pytest config sets the same path.
The Linux container installation was verified independently and does not need this workaround.

## Reproducing this project (for teammates)

Everything needed is in this repository except secrets and private data, which are never
committed: `.env`, `.env.cloud`, `.local/` (credential files, VM settings, evaluation raw
outputs), and the local learning notes under `docs/learning/`. Real health data lives only on
the owner's cloud disk and phone. Start from a Git clone, not from a copied folder, so the
ignore rules and the policy tests apply.

| Goal | What you need | Steps |
|---|---|---|
| Server, tools and tests | Docker with Compose, `uv` | Quick start above; then "Tests and checks" (host suite, Docker suite). |
| Assistant with a real model | Ollama with `qwen3:8b` on your machine | Set the assistant variables from `.env.example`; run `scripts/eval_assistant.py --set protocol` to see the orchestrator work on synthetic data; read `ml/eval/RESULTS.md` for what to expect. |
| iOS app in the Simulator | Xcode 26, optional XcodeGen | `ios/HealthOpsUploader/README.md`; debug builds seed synthetic HealthKit samples; the Simulator talks to the local stack on `127.0.0.1:8000`. |
| iOS app on a phone | An Apple developer account (a free personal team works for seven days), a phone in Developer Mode | Pass your own `DEVELOPMENT_TEAM` to `xcodebuild`; nothing about teams is committed. |
| Cloud GPU deployment | Your own Google Cloud project with billing, GPU quota and, for Fitbit, a Google Health API OAuth client | `docs/cloud-dev-deployment.md`, section "First-time setup"; copy `infra/cloud/vm.env.example` to `.local/cloud/vm.env`. Costs money; the scripts refuse billable actions without `--yes`. |

Expected results are documented, not promised: test counts are in this file, model behaviour in
`ml/eval/RESULTS.md`, and the deployment's observed numbers in the deployment document.

## Tests and checks

```bash
# Fast schema/tool/unit tests, no database required:
uv run pytest -q tests/test_schema.py tests/test_tools.py tests/test_period_comparison.py tests/test_assistant.py tests/test_assistant_model.py tests/test_assistant_toolbox.py tests/test_assistant_cards.py tests/test_assistant_limits.py tests/test_assistant_fixtures.py tests/test_knowledge.py tests/test_knowledge_manifest.py tests/test_debug_page_dom.py tests/test_eval_sets.py tests/test_ios_contract.py tests/test_public_content.py
uv run ruff check .
uv run ruff format --check .
python3 scripts/check_public_content.py

# Application suite against a separate real PostgreSQL database:
docker compose --profile test build test
docker compose --profile test run --rm test
docker compose --profile test stop test-db
```

The `test-db` service has no host port and uses ephemeral tmpfs storage. Tests migrate it with
Alembic and create fresh synthetic users; they never truncate the demo database. Outside
Compose, database tests skip unless `HEALTHOPS_TEST_DATABASE_URL` is set to a dedicated database
named `healthops_test`. Do not interpret skipped integration tests as a full pass.

Tests cover timezone/unit validation, SDNN vs RMSSD, authorization, cross-user reads/deletes,
expired/revoked tokens, exact and concurrent replay, conflict rollback, filters/pagination,
safe validation errors, credential-file permissions, and migration/model consistency.
Tool tests cover DST day lengths, local-vs-UTC dates, null-not-zero missing days, unweighted
sample means, unsupported metric/aggregation pairs, interval overlap across days and outside
the window (same verdict regardless of window size), zero-length intervals, vendor daily record
alignment, separate providers, identity injection, date-range overflow and explicit limits.
Period-comparison tests cover mean-of-daily-means versus pooled means, daily-total preference
without fallback on conflicts, zero totals, unused vendor summaries, provider union with null
sides, non-overlapping windows and the shared unique-record budget.
Assistant tests use a scripted model: identity and timezone injection, server-resolved dates,
one retry on invalid arguments, unknown tools, a wall-clock deadline that includes queueing,
whole-request context budget, evidence budget, per-tool insufficient-data judgement, decline
handling, model errors and the today override; the HTTP adapter has protocol tests with a mock
transport; the evaluation sets are checked against the time semantics and the synthetic
history; the integration test runs real tools under the token identity. `tests/test_ios_contract.py` validates the Swift adapter's encoded output against the Python
schema; on a host with Swift it regenerates the fixture, elsewhere that half is skipped.
The Swift package has its own tests (`cd ios/HealthOpsUploader && swift test`) for mapping and
the sync engine (first sync, no-op incremental sync, replay, failure with retained pending page,
lost response, conflict, consent filter, deletions, scope isolation).
Repository-policy tests run on the host with Git, separately from the application tests in
the container (counts at the time of writing: host 181 passed, container 221 passed, Swift 51).
The application image does not install Git for repository checks.

## Local development without rebuilding the API

```bash
# Stop only the container API to free port 8000; keep the database running.
docker compose stop api
PYTHONPATH=services/ingestion-api/src uv run alembic upgrade head
PYTHONPATH=services/ingestion-api/src uv run uvicorn healthops.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log
```

Use the same database URL for the migration, CLI and API. Schema changes require a reviewed
Alembic migration; application startup does not silently create tables.

## Persistence and stopping

```bash
docker compose stop
# Start again, retaining the named PostgreSQL volume:
docker compose up -d
```

The named volume `healthops-local_postgres_data` survives container restarts/recreation.
Do not remove volumes (`down -v`) unless you deliberately intend to erase local history.
No cloud resources are created by these instructions. The current database is not encrypted
at the application layer and uses a local admin database account; use only synthetic data
until the security/deployment milestone is complete.

## Repository layout

```text
services/
  ingestion-api/
    src/healthops/    Python API, validation, authentication, storage, operator CLI, tools
    migrations/      Service-owned PostgreSQL schema history
ios/HealthOpsUploader/  Swift package (core + tests), SwiftUI app, XcodeGen spec and project
infra/docker/        Container build definition
infra/cloud/         Compose, Caddy, startup and env templates for the single-GPU development VM
tests/               Unit and real-PostgreSQL integration tests
examples/            Synthetic request fixture and tool response examples
ml/eval/             Synthetic assistant evaluation sets, rubric and history generator
knowledge/           Reference-source manifest (licences, checksums), project-authored reference notes, ingestion into pgvector; raw texts stay local
scripts/             Local HTTP smoke test, publication checks, assistant evaluation, cloud VM wrappers
docs/                Public English API contract and architecture decisions
compose.yaml         Local database, migration, API, isolated test services
pyproject.toml       One Python package/environment for this first service
uv.lock              Resolved dependency versions
```

All commands run from the repository root. Python imports remain `healthops.*`;
`services/ingestion-api` is a repository directory, not a Python import name.
Future service/client boundaries are documented in the architecture plan; empty services
are not presented as implemented components.

## Publication policy

Public source, comments, documentation, filenames and configuration must be English.
Local learning notes under `docs/learning/` are excluded from Git and Docker build contexts.
Public documents must not link to those unpublished notes. Keep credentials in `.local/`
and never force-add ignored files.

Run `python3 scripts/check_public_content.py` before committing. It checks tracked files
and non-ignored untracked files for Han characters and forbidden local-only paths, including
their staged versions. The repository-content workflow checks tracked files after a push
or pull request; that workflow is a backstop, not protection against publishing before CI runs.
Git ignore rules do not untrack previously committed files. No local learning notes were
tracked when this policy was introduced. This language/path check is not a secrets scanner
or a full English-language detector; manually review the staged diff before pushing.

Planned, not implemented: background or scheduled sync, deletion sync, further tools (workout
history, window averages as a claim type), user default timezones, production identity, RAG,
conversation storage, vLLM serving, Kubernetes or Terraform. To try the assistant locally, run Ollama with `qwen3:8b` and set the assistant variables from
`.env.example` (native Ollama backend with thinking off is the verified configuration; the
OpenAI-compatible endpoint ignores the thinking flag and Qwen3's reasoning then exhausts the
output budget). Observed results and the exact configuration are in
[ml/eval/RESULTS.md](ml/eval/RESULTS.md); they are small-sample numbers for a local Q4 model,
not deployment measurements.
