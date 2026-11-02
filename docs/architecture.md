# Application architecture

HealthOps is an application-focused extraction from the AC215 team project; see
[provenance](provenance.md) for ownership and the frozen upstream source.

## Runtime boundaries

| Boundary | Implementation | Responsibility |
| --- | --- | --- |
| Phone | SwiftUI + HealthKit | Consent, upload, charts, question and clarification UI |
| API | FastAPI in one service | Authentication, ingestion, connectors, tools and LangGraph orchestration |
| Database | PostgreSQL 17 + pgvector | Account-scoped observations, tokens, pending conversations and reference chunks |
| Model server | Ollama on host locally or GPU VM in GCP | Qwen3-8B chat and nomic-embed-text embeddings |
| Cloud API hosting | Cloud Run | Runs API container independently from Cloud SQL and model VM |
| External connector | Google Health / Fitbit OAuth | Optional user-authorized sync; not required for synthetic demo |

The application does not require separate tool or RAG microservices. `migrate` is a one-shot
schema job, not a continuously running server. Local Compose contains a separate ephemeral
test database. Cloud templates require explicit operator configuration and are not evidence
that the repository provides a running service.

## A request through the code

1. `healthops/main.py` authenticates the bearer token and obtains the user ID server-side.
2. `assistant/routed.py` runs intake; `routing.py` validates typed intent, metrics and time
   phrases. Python resolves supported dates. Ambiguous scope causes clarification.
3. `assistant/conversations.py` persists pending clarification with user ownership, expiry
   and optimistic version checks. Resume does not replay an already consumed continuation.
4. `assistant/orchestrator.py` runs the request-local LangGraph model/tool loop. The model can
   select registered data tools or `lookup_reference` and inspect evidence cards.
5. `assistant/guards.py` limits execution to the approved request specification and checks
   whether all requested targets were covered. `claims.py` validates submitted findings;
   a bounded correction can return to the same model without resetting budgets.
6. Verified templates render the answer. Missing records, unavailable references, unsupported
   requests, connection errors and empty model output retain explicit statuses.

All paths above are relative to `src/api-service/`. Clarification is durable, but in-flight
model/tool execution is not a durable LangGraph checkpoint. Tracing is explicitly disabled
for the application; this repository does not claim a production observability deployment.

## Data and modeling limits

The deterministic tools retain provider and metric semantics. SDNN is not interchangeable
with RMSSD; missing records are not zero. The separate team discrepancy-model pipeline is
not yet connected to the assistant. No automatic learned device-trust ranking is claimed.
Reference screening and evidence checks are defenses with known limits, not a general proof
of security. This is a synthetic-tested prototype, not validated medical advice.

See [quickstart](quickstart.md), [API contract](api-contract.md),
[clarification](assistant-clarification.md), [execution guards](assistant-execution-guards.md)
and [cloud runbook](../src/deployment/cloud-run/README.md).
