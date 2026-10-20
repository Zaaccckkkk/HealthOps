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
| `services/ingestion-api/src/healthops/tools/` | Implemented, locally tested | Deterministic, user-scoped history tools served by the same process |
| `services/ingestion-api/src/healthops/assistant/` | Implemented; engineering tested with a scripted model | Single-turn tool-calling orchestration; model quality evaluated separately |
| `ml/eval/` | Implemented (synthetic question sets, rubric, history generator) | Assistant evaluation data; raw outputs stay local |
| `infra/docker/` | Implemented, locally tested | Local container image build |
| `infra/cloud/`, `scripts/cloud/` | Created and exercised once (2026-10-03); stopped between sessions | One GPU development VM: Caddy HTTPS edge, API, PostgreSQL and Ollama in Compose; persistent disk; two-hour auto-stop |
| `tests/` | Implemented | Schema, API, database and repository-policy tests |
| `ios/HealthOpsUploader/` | Implemented; verified on a physical iPhone against the cloud deployment, including one consented upload of the owner's own HealthKit history | Consented HealthKit upload with replay-safe incremental sync, plus the Ask tab over the cards contract; old prototype is outside this repo and unchanged |
| `services/ingestion-api/src/healthops/connectors/` | Implemented in the API process; tested against a fake Google | Google Health connector: server-side OAuth, windowed sync, per-type watermarks. A separate worker service is not justified while one tester syncs on demand |
| `services/api-service/` | Planned; not created | Future home of orchestration and tools if a second runtime is justified; today both live in the ingestion API process |
| `services/ingestion-api/src/healthops/knowledge/`, `knowledge/` | Implemented in the API process; pgvector in the same PostgreSQL | Licence-checked reference corpus, chunking, nomic-embed-text embeddings, cosine retrieval, quotation limits per source; a separate retrieval service is not justified for one tester |
| `services/llm-serving/` | Planned; not created | vLLM GPU inference. Development uses Ollama (Mac, and the scripted GPU VM); switching servers needs protocol tests and re-evaluation |
| `schemas/` | Planned; not created | Versioned cross-language contracts; today the Swift adapter is checked against the Python schema by `tests/test_ios_contract.py` |
| `infra/kubernetes/`, `infra/terraform/` | Planned; not created | Deployment/infrastructure definitions when authorized |

Analytics tools, the agent router and a transparent recovery indicator start as Python
modules, not three separate network services. The first tools (availability, daily summary and
period comparison) live in `healthops/tools/` and are exposed by the existing FastAPI process,
because only one runtime exists today; a second empty service would not be a boundary. Period
comparison is a pure reduction over two daily-summary results and adds no storage. The package is
layered so it can move with the future API service: routes in `main.py` handle authentication
and parameters, `tools/queries.py` enforces user isolation and bounded reads, and the remaining
modules compute from explicit inputs without database access. Recovery remains auxiliary;
preserve vendor scores when available. Device connectors remain in Swift for HealthKit. On an
iPhone, HealthKit is the only live channel: Health Connect exists on Android only, and
third-party wearables (Fitbit, Garmin, Oura) reach an iPhone through their own apps writing
into Apple Health. The `google_health` provider therefore stays a server-side channel concept
for a future Android client or server-side connector; it is kept out of phone demo data
(`scripts/seed_synthetic_user.py --healthkit-only`) and the evaluation history is unchanged so
earlier runs stay comparable. The Swift client does not imply a Python worker.

The current Pydantic `schemas.py` remains service-owned. Do not duplicate it in a top-level
`schemas/` folder merely to match a diagram. The Swift adapter keeps its own mirror of the
contract in `HealthOpsCore`; a fixture generated by the Swift code is validated by the Python
schema on every host test run. Export a versioned OpenAPI or JSON Schema when a third consumer
appears or the two mirrors start to diverge.

Public documentation is English. Local learning notes are ignored by Git and Docker and
must not be linked from public documentation. `AGENTS.md` records the maintenance policy.

## Implemented boundary

Synthetic JSON client -> local FastAPI -> Pydantic contract -> authenticated user -> SQLAlchemy
transaction -> PostgreSQL; user-scoped history can be retrieved through HTTP.

The iOS adapter feeds that boundary: HealthKit anchored query -> consent filter -> deterministic
mapping -> persisted pending page -> `POST /v1/observations/batch` -> anchor commit on success.
First verified in the iOS Simulator against the loopback API with synthetic samples; since
2026-10-03 also from a physical iPhone over HTTPS against the cloud development VM, with
synthetic accounts and then the owner's own HealthKit data under explicit consent. A second
channel, `google_health`, is filled by the server-side Google Health connector
(`healthops/connectors/`), which keeps Fitbit-platform points only.

The assistant sits on top of the tools: authenticated route -> server-resolved time semantics
-> model turn -> validated tool call with injected identity -> tool executed in a worker
thread with a short-lived session -> bounded result returned to the model -> structured answer
with evidence, statuses and a numeric-consistency diagnostic. The model never sees identity,
never computes dates and cannot name any tool outside the registry. Since the cards increment, the model also never writes numbers: Python
builds data cards from the evidence, the model cites them in three finding types, and the
server verifies and renders the sentences. The same-origin debug page is a local verification
interface; the iOS Ask tab consumes the same cards contract.

Deterministic tools read that history back: authenticated route -> typed tool parameters ->
user-scoped, bounded SQL -> Decimal computation in Python -> typed result with statistical
definitions and warnings. Rules are in the [API contract](api-contract.md). Migration `0002`
adds only an index on `(user_id, metric, ended_at)` so the tool's overlap-context query is
bounded by intervals ending after the window instead of a metric's whole history. Tool rules
were established with synthetic records; the same tools now serve the owner's real HealthKit
and Fitbit history on the development VM, with the same definitions and limits.

This boundary is shared by the Swift adapter, the Google Health connector and the assistant.
The server does not read wearables itself: data arrives through the phone (HealthKit) or the
connector (Google Health). No iOS sources were copied from the earlier prototype.

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
- Tool statistics in Python `Decimal` over SQL-filtered rows, with a 93-day span and 10000-record
  limit that fail explicitly: readable, exactly testable definitions for personal-history volumes.
  This is an assumption, not a measurement; push aggregation into SQL if profiling shows a need.
  Availability aggregates in SQL within a required window of at most 366 days, so the scan is
  bounded by the user/time index range; whole-history availability is deliberately not offered.
- One explicit statistical timezone per tool query rather than per-record `source_timezone`:
  a daily series must have one day boundary to be interpretable. Vendor daily records that do
  not align with that boundary are reported as unaligned, never shifted or split.
- Assistant engineering and model quality are verified separately: scripted-model tests prove
  budgets, identity injection and statuses; the evaluation script measures a real local model
  on synthetic data and reports whatever it finds. A local Q4 model's quality and latency are
  not a measurement of a future vLLM deployment.
- uv lockfile for Python dependencies. Base images use version tags, not immutable digests;
  a future deployment release should pin tested digests and establish security-update cadence.

## Deliberately deferred (as of 2026-10-05)

Production login/session management and secret management beyond environment files on one
VM; Cloud SQL/GCS provisioning; backups and full account deletion (only the Google channel
has a per-user delete action); background workers and scheduled or automatic sync (both
channels sync on user action); source update/delete sync (upstream revisions are reported as
conflicts, not applied); structured workouts/check-ins; further analytics (workout history,
window averages as a claim type); user default timezone settings; conversation
storage; streaming answers; vLLM serving; GKE/Terraform; Bayesian models.

Implemented since the earlier version of this list and documented elsewhere: the reference
library (pgvector retrieval with verbatim, licence-limited quotation), external HTTPS
exposure on the development VM (Caddy, Let's Encrypt), per-user rate limits and request-body
limits at the edge, physical-device networking, the Google Health connector with server-side
OAuth and refresh-token handling.

An eight-metric observation schema is not a complete health-domain model. Subjective check-ins,
sleep stages, workouts and sets will have their own contracts when implemented. The Swift
upload adapter now connects to this boundary without changing it.
