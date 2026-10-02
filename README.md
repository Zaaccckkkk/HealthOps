# HealthOps
Harvard APCOMP215 Course Project

## Milestone A: local ingestion and user history

The first implemented slice is a **Python FastAPI + PostgreSQL** backend, with Pydantic
validation, SQLAlchemy persistence, Alembic migrations, a locked uv environment, and
Docker Compose. It accepts canonical observations and retrieves the authenticated user's history.

This is a **local development foundation**, not a publicly deployable health platform.
No live wearable connector, iOS uploader, Google OAuth backend, RAG, LLM, or cloud deployment
is included in this milestone. The existing `AppleHealthKitMVP` prototype outside this repository
was inspected for mapping requirements but was not copied or modified. All committed sample
data is synthetic.

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
The script inserts two synthetic observations, replays them, and queries history. Each run
uses fresh synthetic IDs and leaves its records in the demo database for inspection.

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

## Tests and checks

```bash
# Fast schema/unit tests, no database required:
uv run pytest -q tests/test_schema.py tests/test_public_content.py
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
The eight repository-policy tests run on the host with Git, separately from the 35 application
tests in the container. The application image does not install Git for repository checks.

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
    src/healthops/    Python API, validation, authentication, storage, operator CLI
    migrations/      Service-owned PostgreSQL schema history
infra/docker/        Container build definition
tests/               Unit and real-PostgreSQL integration tests
examples/            Synthetic request fixture
scripts/             Local HTTP smoke test and publication checks
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

Next intended increment: a typed Swift upload adapter that preserves source sample IDs and
requires explicit upload consent. It must not upload UI display strings or assume the current
loopback-only server is reachable from a physical phone. Production identity, TLS, network
access, sync cursors/deletions and cloud provisioning remain separate work.
