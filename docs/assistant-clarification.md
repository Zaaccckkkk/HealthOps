# Assistant intent routing and clarification

Implementation increment: 2026-10-30. The cloud deployment is unchanged until explicitly
migrated and redeployed. Real Qwen multi-turn accuracy has not been evaluated for this version.

## Flow

1. Authenticate the bearer token; apply the existing per-user rate limit.
2. If a continuation is present, load its owner-scoped, unexpired PostgreSQL row.
3. Ask the same chat model for one `route_request` structured decision. This intake model call
   cannot execute data tools. Pydantic validates allowed intents and metrics. Metric names or recognized aliases must also
   appear in user text; proposed options alone do not confirm metrics.
4. Python grounds time phrases in user messages (not assistant suggestions), resolves relative
   dates, and validates windows. Missing fields produce fixed clarification questions. Generic
   wellbeing requests should clarify metrics; medical advice and unrelated requests are refused.
5. Save pending dialogue and return HTTP 200 with `needs_clarification`. No HTTP request,
   database connection or GPU generation remains active while the user decides.
6. A subsequent request carries the ID and version. Route the combined dialogue again. When
   ready, atomically consume the version, then pass dialogue and `confirmed_request` to the
   existing model/tools/verify LangGraph. The response includes the resolved `request_spec`.

## Synthetic example

First request (normal bearer authentication is required):

```json
{"question":"My steps recently?","timezone":"UTC"}
```

A correct router decision produces a question about the time range and a response containing
`conversation_id`, `conversation_version: 1`, and `request_spec.unresolved_fields: ["time_range"]`.
Reply with the actual returned ID, not an example UUID:

```json
{
  "question":"past 7 days",
  "timezone":"UTC",
  "conversation_id":"<returned UUID>",
  "conversation_version":1
}
```

For a frozen test date of 2026-08-10, the resolved window is 2026-08-04 through 2026-08-10.
Production requests cannot set the evaluation-only `today` override. A missing-data result is
still a legitimate executed result; `ready` does not mean the database contains records.

## State, concurrency and privacy

- Migration `0005` creates `assistant_conversations`; it is additive and applies to the selected
  database only. Do not apply it to a cloud or personal database as part of testing.
- State contains user dialogue, request scope, timezone/provider, original reference date and
  clarification count. It may contain sensitive user-entered text. No raw health observations,
  tool evidence, bearer tokens or model reasoning are stored in this table.
- ID alone is not authorization. Every load and conditional update includes the authenticated
  user UUID. Stale, foreign, closed and expired requests share a generic HTTP 409 response.
- Each version can be consumed once. Two concurrent replies can both run intake, but only one
  can proceed to data tools. Retry after a lost response is not response replay: it may receive
  409. Start a new question instead. Execution crash recovery is not implemented.
- A continuation expires one hour after creation; replies do not extend it. Expired rows are
  deleted on later state saves; this is logical expiry, not a scheduled physical-deletion SLA.
  Closing a conversation clears its payload immediately. Account deletion cascades to its rows.
- At most three clarification prompts; another unresolved reply ends the continuation. Context
  is bounded. The overall configured deadline includes routing and execution. Intake has one
  model call; existing execution call/turn budgets remain unchanged.
- Timezone, provider and reference date cannot be silently changed mid-continuation. Use a new
  question to change scope settings. A changed natural-language task can replace the old task
  through the router. Supported phrase matching is deliberately conservative; unfamiliar time
  wording asks for explicit ISO dates rather than guessing.
- `cancel: true` with the ID/version closes the state. The iOS "New question" action discards
  its local ID; the abandoned server row remains subject to expiry. App restart loses the local
  continuation; server persistence supports instance restarts, not conversation discovery.

## Boundaries

The next increment now enforces execution scope, checks evidence/answer coverage, and allows
one bounded verification correction; see [execution guards](assistant-execution-guards.md).
Classification and selection among grounded metric mentions still rely on the model and need
held-out evaluation. Fixed clarification questions are localized; final templates remain
English. General chat memory and unconstrained natural-language rewriting are not implemented.

## Verification

`tests/test_assistant_routing.py` covers ambiguous time, assistant suggestions not counting as
consent, partial replies, changed intent, fixed reference dates, cancellation and bounds with a
scripted model. PostgreSQL tests cover owner/version/expiry checks and concurrent consumption.
`tests/test_assistant_api.py` exercises HTTP resumption across fresh app instances and a verified
missing-data answer using real tools against the dedicated synthetic test DB. Swift tests
cover optional field compatibility, encoding the continuation and dropping it on reset.

Run from the repository root:

```sh
docker compose --profile test build test
docker compose --profile test run --rm test
uv run --directory src/api-service pytest -q tests/test_assistant_routing.py
```

Run `swift test` inside `src/frontend-app/HealthOpsUploader`. These are protocol and integration
tests with scripted decisions, not an observed model-quality improvement or a deployed result.


The existing `scripts/eval_assistant.py` evaluates the core execution assistant directly;
it does not exercise this HTTP intake or supply simulated clarification replies. Do not
interpret its existing 30+6 results as router evaluation. A future multi-turn evaluation
runner must use the routed entrypoint and explicitly scripted user replies.

Observed local verification for this increment: isolated Compose suite 277 passed / 5 skipped
(Node/Swift unavailable in that image); host DOM tests 3 passed; host policy/router/iOS-contract
selection 37 passed / 2 skipped (PostgreSQL cases run in Compose); Swift package 53 passed;
iOS Simulator build succeeded. No cloud migration/deployment or real-model evaluation was run.
