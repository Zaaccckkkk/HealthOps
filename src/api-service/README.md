# API Service

FastAPI + PostgreSQL ingestion of canonical health observations, three deterministic history
tools, the assistant with resumable clarification and the Google Health connector. Runs on port 8000.

| Path | Contents |
|---|---|
| `healthops/` | Python package: routes (`main.py`), schemas, models, repository, auth, tools, assistant, connectors, operator CLI |
| `migrations/`, `alembic.ini` | Service-owned PostgreSQL schema history |
| `tests/` | Unit tests and real-PostgreSQL integration tests |
| `examples/` | Synthetic request and tool response examples |
| `eval/` | Synthetic assistant evaluation sets, rubric, history generator, observed results |
| `scripts/` | Local smoke test, assistant evaluation, synthetic seeding |

From the repository root: `docker compose up -d --build api` (database, migration, API).
Host commands run in this folder, e.g. `uv run pytest -q`. Contracts and run instructions are
in the root `README.md` and `docs/api-contract.md`.

The optional `synthetic` extra adds the operator cohort importer (`healthops.synthetic_import`)
and its parquet dependencies. Run `make synthetic-import` from the root or preview with
`uv run --extra synthetic python -m healthops.synthetic_import --help` in this folder.
See [synthetic import](../../docs/synthetic-import.md) for dry-run, identity mapping, replay,
local credentials and authenticated HTTP verification. API-only images omit the extra.

For the three-account diagnostic evaluation snapshot and deterministic gold preparation, see
[cohort baseline preparation](../../docs/cohort-baseline.md). Model evaluation has not run as
part of this preparation step.

## LangGraph assistant orchestration

`healthops/assistant/orchestrator.py` executes a request-local LangGraph `StateGraph`:
`model -> tools -> model`, with `state_findings` routed to a separate deterministic `verify`
node. The same chat model chooses both deterministic tools and `lookup_reference`; RAG is not
restricted to a fallback after data tools. Registered schemas, authenticated identity injection,
context/tool/turn budgets, deadlines and claim verification remain authoritative.

Graph state holds messages, the latest model turn, model-turn count, invalid-argument retry state,
pending findings and the next route. Evidence and response state remain in a request-local
`_RunState` captured by the graph; this is intentionally not a durable/serializable checkpoint.
Each execution gets its own graph and identity closure. Tool evidence is not carried across
requests and failed tool side effects are not automatically re-executed. The separate intake
graph persists pending clarification dialogue, as described below. `tracing_context(enabled=False)` prevents
LangSmith tracing even when the host enables it. LangSmith is a dependency for this control,
not an external service configured for the application.

A malformed tool call still receives one correction opportunity; timeouts/errors terminate with
existing response statuses. Cancelling a request releases its concurrency slot. Final user-visible
sentences still come from verified claim templates. This migration does not implement free-form
answer rewriting or in-flight execution crash recovery. It does not establish better model accuracy.

Dependency versions are pinned in `uv.lock`. Regression checks include concurrent account requests,
cancellation, graph routing, existing tool/claim/timeout tests and the isolated Compose test DB.
Cloud deployments require a new image and revision; editing this code does not change an already
running Cloud Run service. See [LangGraph's graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).


### Intent routing and resumable clarification

The HTTP endpoint now runs `assistant/routed.py` before the evidence graph. Its LangGraph
intake routes `ready` to execution and ends `needs_clarification` / `unsupported` requests.
`routing.py` validates typed intent, metrics and grounded time phrases, resolves dates in
Python, and returns `request_spec`. A model proposal cannot turn `recently` into seven days.
See [the clarification contract](../../docs/assistant-clarification.md).

Migration `0005` adds PostgreSQL clarification state. The same authenticated user can resume
on another API instance with `conversation_id` and `conversation_version`. State expires after
one hour; three clarification prompts are allowed. This stores user questions/replies and
scope, not tool evidence or model reasoning. Execution is still request-local. The router
adds one model call; the overall HTTP deadline includes intake and execution.

This increment requires a migration and a new deployment image. It has not been deployed by
editing these files, and scripted tests do not measure real Qwen routing accuracy.


### Execution scope and completeness

The routed path passes a typed `RequestSpec` to the tool graph. `assistant/guards.py` checks
intent, metrics, dates and source context before executing tools. The verify node checks all
required metric/source cards and may return to the same model for one correction, without
resetting budgets. `window_summary` and `available_series` are deterministic claim renderers.
Responses include coverage and both verification attempts for evaluation. See
[execution guard semantics](../../docs/assistant-execution-guards.md).
