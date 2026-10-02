# Milestone A decisions

## Repository boundaries and growth plan

The implemented ingestion boundary lives in `services/ingestion-api/`. Its `src/healthops/`
package contains API routes (`main.py`), input contracts (`schemas.py`), persistence models
(`models.py`), transaction logic (`repository.py`), authentication and local operator tooling.
Its migrations belong to the same service. The image recipe lives in
`infra/docker/ingestion-api.Dockerfile`; Compose, test orchestration and dependency management
remain at the root. This move does not change HTTP contracts or database ownership.

The current repository has one application service, not a deployed microservice system.
A directory organizes code; a microservice also needs a runtime, versioned interface,
owned state, deployment lifecycle and observability. Do not split every Python function
into a network hop. Keep a single root Python project while only one service is implemented;
introduce service-specific packages/lockfiles when dependency or release isolation is needed.

| Boundary | Current state | Intended responsibility |
|---|---|---|
| `services/ingestion-api/` | Implemented, locally tested | Typed observation ingestion and user history |
| `infra/docker/` | Implemented, locally tested | Local container image build |
| `tests/` | Implemented | Schema, API, database and repository-policy tests |
| `ios/` | Planned; not created | Consented HealthKit upload and UI; old prototype is outside this repo |
| `services/wearable-connectors/` | Planned; not created | Provider adapters and sync workers when server-side sync is implemented |
| `services/api-service/` | Planned; not created | User-facing orchestration, agent router and deterministic analytics tools |
| `services/retrieval-service/` | Planned; not created | Knowledge ingestion, retrieval and citation metadata |
| `services/llm-serving/` | Planned; not created | vLLM GPU inference, independently deployed from CPU services |
| `schemas/` | Planned; not created | Versioned cross-language contracts once multiple consumers exist |
| `ml/` | Planned; not created | Evaluation datasets, experiments and model-quality checks |
| `infra/kubernetes/`, `infra/terraform/` | Planned; not created | Deployment/infrastructure definitions when authorized |

Analytics tools, the agent router and a transparent recovery indicator start as modules
inside the future API service, not three separate network services. Recovery remains
auxiliary; preserve vendor scores when available. Device connectors may initially remain
in Swift; an existing client integration does not prove a Python worker exists.

The current Pydantic `schemas.py` remains service-owned. Do not duplicate it in a top-level
`schemas/` folder merely to match a diagram. When Swift or another service consumes a shared
contract, export/version OpenAPI or JSON Schema with explicit compatibility tests.

Public documentation is English. Local learning notes are ignored by Git and Docker and
must not be linked from public documentation. `AGENTS.md` records the maintenance policy.

## Implemented boundary

Synthetic JSON client -> local FastAPI -> Pydantic contract -> authenticated user -> SQLAlchemy
transaction -> PostgreSQL; user-scoped history can be retrieved through HTTP.

This is the first durable interface for future Swift/provider adapters and Python analytics.
It does not yet collect wearable data itself. No iOS sources were copied into the new repository.

## Why these choices

- PostgreSQL rather than SQLite: test the same uniqueness, concurrency, timezone and numeric
  behavior intended for Cloud SQL. NoSQL is unnecessary for these relational queries.
- Synchronous SQLAlchemy + psycopg inside FastAPI `def` routes: simple transaction reasoning.
  FastAPI runs sync routes/dependencies in its worker thread pool. This is not an async DB
  implementation and is not a throughput claim. Connection pool size is bounded per process.
- Versioned Alembic migrations instead of `create_all()` at startup: schema history is explicit,
  reviewable and exercised by tests. Migration/model drift is checked in the integration suite.
- Immutable ingestion plus conflict detection instead of silent overwrite: preserves history
  while the vendor revision protocol is still undefined. Updated daily totals need follow-up work.
- Development opaque tokens instead of unauthenticated `X-User-ID`: enables real multi-user
  access tests now without pretending a complete login/OAuth platform exists.
- API and migration run as a non-root container user; the local PostgreSQL role is still the
  container bootstrap admin. Least-privilege DB roles/RLS/TLS are not implemented.
- Named DB volume for persistence; test DB uses independent tmpfs. Migration is one-shot and
  completes before API startup. No container data is baked into the API image.
- uv lockfile for Python dependencies. Base images use version tags, not immutable digests;
  a future deployment release should pin tested digests and establish security-update cadence.

## Deliberately deferred

Production login/session management, secret management, external network exposure, HTTPS,
rate limits/body-size gateway limits, consent UI, Cloud SQL/GCS provisioning, backups and full
account deletion, source OAuth/token renewal, background workers, source update/delete sync,
structured workouts/check-ins, analytical tools, RAG/pgvector, vLLM/GPU, GKE and Bayesian models.

An eight-metric observation schema is not a complete health-domain model. Subjective check-ins,
sleep stages, workouts and sets will have their own contracts when implemented. The next
increment should connect a typed adapter to this boundary rather than rewrite it.
