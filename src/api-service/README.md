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

## Endpoints and example calls

Every data route is user-scoped by a bearer token; the full contract (request and response
formats, limits, error codes) is [docs/api-contract.md](../../docs/api-contract.md), and the
interactive OpenAPI page is `/docs` on a local instance.

| Method / route | Meaning |
|---|---|
| GET `/health/live`, GET `/health/ready` | Liveness; readiness (database reachable) |
| GET `/v1/me` | The caller's user ID and display name |
| POST `/v1/observations/batch` | Ingest 1-500 canonical observations atomically (replay-safe) |
| GET `/v1/observations`, DELETE `/v1/observations/{id}` | History; delete one owned record |
| GET `/v1/tools/availability`, `/v1/tools/daily-summary`, `/v1/tools/period-comparison` | Deterministic history tools |
| GET `/v1/tools/reconcile` | Optional CPU Transformer: single-day cross-device estimate, interval, readings, attribution and warnings |
| POST `/v1/assistant/ask` | The assistant: routes a question to the tools, verifies claims, returns a structured answer |

Local instance (`make app` from the repository root; a token comes from the operator CLI,
`uv run healthops issue-token <user>` in this folder, or from `make synthetic-import`):

```bash
curl -s http://127.0.0.1:8000/health/ready
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/v1/me
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/v1/tools/daily-summary?metric=steps&timezone=America/New_York&start_date=2026-08-01&end_date=2026-08-07"
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question": "How many steps did I take on August 1, 2026?", "timezone": "America/New_York"}' \
  http://127.0.0.1:8000/v1/assistant/ask
```

After enabling the optional [reconciliation model](../../docs/reconciliation.md), use the
new v0.2 paired synthetic account token for these examples. The first date has paired step
readings; the second predates that account's history and returns null estimates.

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/v1/tools/reconcile?metric=steps&date=2026-08-07&timezone=America/New_York"
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/v1/tools/reconcile?metric=steps&date=2026-01-01&timezone=America/New_York"
```

The Cloud Run deployment ([src/deployment/cloud-run](../deployment/cloud-run/README.md)) takes
the same calls with one extra header, the caller's Google identity for Cloud Run IAM:

```bash
URL=https://healthops-ms3-api-799925593771.us-east4.run.app
curl -s -H "X-Serverless-Authorization: Bearer $(gcloud auth print-identity-token)" $URL/health/ready
curl -s -H "X-Serverless-Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Authorization: Bearer $TOKEN" $URL/v1/me
```

Synthetic request and response examples are in `examples/`; the three-account cloud checks
are recorded in [docs/evidence/cloud-run-synthetic.md](../../docs/evidence/cloud-run-synthetic.md).

## LangGraph assistant orchestration

`healthops/assistant/orchestrator.py` executes a request-local LangGraph `StateGraph`:
`model -> tools -> model`, with `state_findings` routed to a separate deterministic `verify`
node. The same chat model chooses both deterministic tools and `lookup_reference`; RAG is not
restricted to a fallback after data tools. Registered schemas, authenticated identity injection,
context/tool/turn budgets, deadlines and claim verification remain authoritative.

An opt-in `verify -> narrate -> END` branch lets the same model write the final response from
verified evidence. It is disabled by default; enable `HEALTHOPS_ASSISTANT_NARRATIVE_ENABLED=true`
for the experimental writer. Output is labelled `model_generated`, since number/date/reference
checks do not verify prose semantics. Invalid output or timeout falls back to the verified
template. See the [answer-quality probe](eval/answer-quality.md) and
[API contract](../../docs/api-contract.md) for checks and limits.

The writer's v6 prompt consumes deterministic fields added by
[`evidence_facts.py`](healthops/assistant/evidence_facts.py) at the `narrative.messages` boundary.
It reuses the archived v5 transformations in
[`presentation.py`](healthops/assistant/presentation.py).
Canonical durations remain in seconds. Python supplies localized `*_display` text for daily
values, ranges, comparisons and reconciliation estimates, rounding to the nearest minute with
30 seconds rounded up. Missing values stay missing; explicit seconds requests retain their raw
values. `provider_display` supplies `Apple Health` and `Google Health`, while provider keys and
evidence IDs remain unchanged. The model is instructed to use these texts, but generation still
requires evaluation: precomputed values do not guarantee the model will select the right fact.
Dates receive locale-independent display text with a year. Per-source `computed_facts` binds
first/last values and extrema to dates, distinguishes missing/unusable/unreported days, and
computes exact changes without interpolating gaps. Continuous runs require adjacent calendar
days and a consistent recording basis. At most five longest runs are shown, with an explicit
omitted-run count; all transition counts remain available. The algorithm has no seven-day
assumption, smoothing threshold or clinical interpretation. Mixed or unknown recording bases
do not receive a combined trend. Reconciliation distances are recomputed from supplied readings
and the supplied estimate; no estimate or missing sources never establish model unreliability.
Period differences preserve their original statistic, including per-day scope for daily means.
The registered serving candidate is separate from the archived v4 training prompt. No service
deployment or retraining is implied by this local code change.

Before accepting the first writer draft or its editor response, the narrative branch applies
[`output_format.py`](healthops/assistant/output_format.py) to the answer text after adding its
existing qualifications. This final, deterministic pass formats valid ISO date literals and
provider names, and converts explicit seconds quantities only when they match typed duration
evidence. An explicit request for seconds or ISO dates keeps that representation. It does not
rewrite evidence IDs, code spans, URLs, unsupported numbers, trends, or health conclusions.
The function returns an edit list for offline review; raw model output is retained separately
in evaluation artifacts. Formatting is not semantic validation, and this boundary does not
change the archived prompts or training pairs.

The English/Chinese request path now uses
[`security.py`](healthops/assistant/security.py) before the router (including saved user turns)
and at the direct assistant entry. Bounded patterns detect known instruction overrides, role
spoofing, credential extraction, cross-account requests and unauthorized execution. Normalized
text is used only for screening; accepted user text is not rewritten. A narrow whole-request
exception permits discussion of a quoted attack phrase, not instructions to execute it.
Rejected continuations do not overwrite or consume pending clarification state. The response
is a fixed refusal; logs contain only the guardrail version, stage and reason code.
The input-only script gate rejects non-Latin/non-Han letters, including Korean, before model
calls, even in quoted or mixed input. Language refusals ask users to rephrase in English or
Chinese. The router also has an `unsupported_language` refusal for other languages sharing
allowed scripts; that model-based decision is not guaranteed language detection.

The writer/editor share one model and at most one edit opportunity. `narrative.finalize_draft`
screens the actual formatted candidate, including server qualifications, for credential
requests/leaks and protected prompt disclosure. The first draft checker still reports evidence
coverage, date/number and markup findings to the single Editor. These quality findings do not
trigger a second veto; edited answers can retain factual errors and remain `model_generated`.
An invalid response schema, unexpected tool call or sensitive-output failure preserves the
template with `narrative_status=fallback` and a reason. No additional model call is made. `tests/test_assistant_security.py` covers bilingual attacks, benign requests, saved
dialogue, safe repairs, sensitive editor output and intentionally permitted factual mistakes with scripted models. This is
not a live-model jailbreak-resistance score. Rules can miss paraphrased/encoded attacks and
can reject legitimate wording; they are not authorization or semantic verification. Existing
identity injection, tool allowlists, scope limits and untrusted-reference screening remain
mandatory even when input screening passes. Source labels use Apple Health and Google Health in cards and template prose; canonical
provider keys and evidence IDs are unchanged. Request-wide Google connector limitations are
shown once in the answer instead of being attached to Apple Health cards. No language expansion
or training is included. See the [guardrail contract](../../docs/assistant-guardrails.md).

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

## Cross-device model integration

The optional `reconcile` extra loads the pinned 14-day Transformer once per API process.
The assistant routes `device_reconciliation` to `reconcile_devices`; Python validates the
evidence and renders mandatory intervals and insufficiency warnings. See
[reconciliation setup, contracts, limitations and evaluation](../../docs/reconciliation.md).

### Pretraining development regression

`scripts/eval_writer_chain.py --pretraining` runs 30 development scenarios (36 turns)
through authenticated requests, real SQL tools and the configured chat model. It requires
an isolated loopback `healthops_test` database and a manifest-verified three-account import.
It captures actual writer inputs; it does not supply authored evidence or approve training data.
See the [execution report](../../docs/evidence/pretraining-regression-20261109.md) for
stage-level results, semantic failures and reproduction prerequisites.
