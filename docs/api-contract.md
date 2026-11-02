# Canonical observation API v1

Status: local-development contract, not a deployed wearable integration.
OpenAPI at `/docs` is generated from [schemas.py](../src/api-service/healthops/schemas.py) and
[main.py](../src/api-service/healthops/main.py). Dates in this document are illustrative.

## Authentication and ownership

All `/v1/` endpoints require `Authorization: Bearer TOKEN`. An operator issues a random token
using the local CLI. The database stores only its SHA-256 digest, user association, expiry
and revocation flag. This is not Google OAuth and not a password-login service.

The server obtains `user_id` from the token. The upload schema explicitly rejects `user_id`.
Read/delete operations always filter by the authenticated user. There is no administrative
list-all endpoint. SQL row-level security is not implemented; isolation is in application queries.

## Endpoints

| Method / route | Meaning |
|---|---|
| GET `/health/live` | Process responds; no DB guarantee |
| GET `/health/ready` | Database reachable and `users` table query succeeds |
| GET `/v1/me` | Current user's ID and display name |
| POST `/v1/observations/batch` | Atomic ingestion of 1-500 observations |
| GET `/v1/observations` | User-scoped history, oldest start time first |
| DELETE `/v1/observations/{id}` | Delete one owned observation; unknown/other user's ID returns 404 |
| GET `/v1/tools/...` | Deterministic history tools; see below |
| POST `/v1/assistant/ask` | Single-turn assistant over the tools; see below |

`ready` is an initial schema check, not proof that every migration or external provider works.

## Input

See the complete [synthetic JSON request](../src/api-service/examples/observations.synthetic.json).

| Field | Contract |
|---|---|
| `provider` | `healthkit`, `google_health`, or `manual`; acquisition channel, not guaranteed hardware |
| `source_record_id` | Stable upstream identifier, 1-300 characters; manual events need a stable client UUID |
| `source_device` | Optional source provenance label, not verified device identity |
| `metric` / `unit` | Exact canonical pair below |
| `value` | Finite nonnegative decimal, at most 20 digits and 6 decimal places; returned as a JSON string |
| `started_at` / `ended_at` | Offset-aware timestamps; normalized to UTC; end must not precede start |
| `source_timezone` | Valid IANA timezone, preserved for future local-day interpretation |
| `aggregation` | `sample`, `interval_total`, `daily_total`, or `daily_summary` |

| Metric | Unit |
|---|---|
| `heart_rate`, `resting_heart_rate` | `bpm` (must be greater than zero) |
| `steps` | `count` (integer) |
| `hrv_sdnn`, `hrv_rmssd` | `ms` (different metrics; never interchangeable) |
| `sleep_duration`, `exercise_duration` | `s` |
| `active_energy` | `kcal` |

Missing data is absence of an observation, not a zero or a string such as `—`.
Input units are validated, not auto-converted: client adapters must perform explicit conversion.
This version validates representation, not physiological plausibility or vendor algorithm accuracy.
Timezone names are preserved but not checked against every vendor's local-day boundary conventions.

## Replay and updates

Unique source identity: `(user_id, provider, source_record_id, metric)`.

- New identity: insert a row.
- Same identity and canonical payload: skip as a duplicate.
- Same identity with changed payload: HTTP 409, entire batch rolls back.
- Invalid observation anywhere in a batch: HTTP 422, no batch writes.

Content includes provenance, timezone and aggregation, not just numeric value. UTC-equivalent
timestamp encodings and equivalent decimal representations have the same fingerprint.
Database uniqueness enforces the rule under concurrency. SQLAlchemy's PostgreSQL
`ON CONFLICT DO NOTHING` is followed by a hash comparison; this is not a last-write-wins upsert.

Successful response: `{"received": 2, "inserted": 2, "duplicates": 0}`.
Response is 200 for both new ingestion and exact replay. A crash after commit may leave the
client unsure whether a write succeeded; replaying the same stable IDs is safe.

Provider corrections, event revisions, cursor checkpoints and deletion tombstones are deferred.
Deleting a local row does not delete upstream data, and later replay can re-create it. Do not
treat DELETE as a complete account-erasure or provider synchronization mechanism.

## History and errors

Optional filters: `metric`, `provider`, `start`, `end`. A time window is `[start, end)` based on
the observation's **start timestamp**, not interval overlap. A sleep interval starting before
the window is excluded; duration aggregation/clipping is future analytics work.

`limit`: 1-200 (default 50). `offset`: 0-100000. Ordering is `(started_at, id)`. The response
includes `items` and nullable `next_offset`. This is simple offset pagination, not a stable
snapshot during concurrent inserts/deletes and not an incremental sync cursor.

- 401: missing, invalid, expired or revoked token.
- 404: owned observation not found; does not disclose another user's ownership.
- 409: source identity reused with different content.
- 422: invalid input; errors omit payload values to avoid reflecting private health data.
- 503: storage error; no SQL parameters or connection URL returned. Blind retries will not
  fix schema bugs; inspect database state before treating all 503s as transient.

## Deterministic history tools v1

Status: implemented and tested locally with synthetic data. These read-only endpoints are the
first typed tools intended for a future tool-using LLM. The server injects the authenticated
`user_id`; a `user_id` query parameter is ignored. No endpoint accepts or executes generated SQL.
Both tools require an explicit IANA `timezone` and an inclusive local date window; one
statistical timezone applies to the whole query. A record's `source_timezone` is preserved as
provenance and does not assign local days. Supported dates are 2000-01-01 to 2100-12-31; other
dates return 422 instead of overflowing timezone arithmetic.
Timezone names are checked by Python's `zoneinfo`; PostgreSQL also resolves the name for
`availability`, and a name unknown to the database returns 422 rather than 503.

| Method / route | Meaning |
|---|---|
| GET `/v1/tools/availability` | Which `(provider, metric, aggregation)` series exist for the user, with counts and first/last start |
| GET `/v1/tools/daily-summary` | One metric, one provider group per channel, one row per local day in `[start_date, end_date]` |
| GET `/v1/tools/period-comparison` | One metric, two non-overlapping windows, one window value per provider and side plus the difference |

### Availability

Parameters: `timezone`, `start_date`, `end_date` (all required; at most 366 local days).
Each series reports `record_count`, `first_started_at`, `last_started_at` (UTC) and
`local_days_with_records`, the number of distinct local dates containing at least one record
start. The response carries `notes` stating that absence of records means no accessible records
in this system, not inactivity, no device wear, or denied authorization, and that the day count
is not a coverage ratio. Counting happens in SQL over records starting inside the window; the
scan is bounded by the user/time index range, not by a limit on aggregated rows. Whole-history
availability is not offered.

### Daily summary

Parameters: `metric`, `timezone`, `start_date`, `end_date` (inclusive, at most 93 local days),
optional `provider`. A local day is `[local midnight, next local midnight)`, so DST days have 23
or 25 hours. Records are selected and assigned by **start time**; an interval starting outside the
window is never summarized (see overlap below). More than 10000 records, including overlap
context, returns 422 asking the caller to narrow the request; results are never truncated. Every day in the window appears for every
provider that has at least one record; days without data carry counts of 0 and `null` values.

| Metric class | `sample` | `interval_total` | `daily_total` | `daily_summary` |
|---|---|---|---|---|
| Point: `heart_rate`, `resting_heart_rate`, `hrv_sdnn`, `hrv_rmssd` | count, min, max, `sample_mean` | unsupported | unsupported | vendor value if aligned |
| Cumulative: `steps`, `active_energy`, `exercise_duration`, `sleep_duration` | unsupported | `interval_total_sum` | vendor value if aligned | unsupported |

- `sample_mean` is the unweighted arithmetic mean of the samples recorded that day, rounded
  half-even to six decimals. It is **not** a time-weighted all-day average heart rate.
- `interval_total_sum` adds reported values of intervals that start on the day. An interval that
  ends on a later day is assigned wholly to its start day with warning
  `interval_crosses_day_boundary`; nothing is split or allocated proportionally. Durations are
  never derived from timestamps: a sleep record's value is the vendor's reported seconds, not
  `ended_at - started_at`.
- Overlap is checked per provider over the complete context, not per day and not only inside
  the window. Half-open intervals that overlap make `interval_total_sum` `null` on the start day
  of **every** record involved, with warning `overlapping_intervals`, because the overlap cannot
  be deduplicated. The tool loads every `interval_total` record of the same user, metric and
  provider filter that starts outside the window but can intersect an in-window interval
  (ends after the window starts and starts before the last in-window interval ends), on either
  side of the window. Context records take part in overlap detection only and are not counted
  in `record_count`. The verdict for a given day therefore does not depend on the window chosen.
  Context records share the 10000-record budget; exceeding it returns 422 rather than skipping
  the check. Migration `0002` adds an index on `(user_id, metric, ended_at)` to bound that scan.
- An `interval_total` with `started_at == ended_at` has no duration and cannot be checked for
  overlap; it is counted in `unsupported_count` with warning `zero_length_interval` and never
  summed.
- `daily_total` / `daily_summary` are used only when the record interval equals the local day
  exactly. Otherwise the record is counted in `unaligned_count` with warning
  `unaligned_daily_record`; it is not moved to another date. Several aligned records for one
  provider and day produce `null` with warning `multiple_daily_records`.
- `daily_total` and `interval_total_sum` are never added together. When both exist, both are
  reported with warning `mixed_total_kinds`.
- Unsupported combinations are counted in `unsupported_count` with warning
  `unsupported_aggregation`; they never contribute to a statistic.
- `provider` is an acquisition channel, not one device. Grouping by provider does not deduplicate
  several devices reporting through the same channel; the overlap and multiple-record warnings
  are the only protection, and no cross-provider or cross-device merging is performed.
- `hrv_sdnn` and `hrv_rmssd` are different metrics and are summarized separately.
- The response includes `window` (requested dates and the actual UTC bounds), `record_count`,
  per-day `warnings`, aggregated `warnings` (including `no_records_in_window`), and `definitions`,
  a dictionary explaining every statistic and warning code for downstream interpretation.

Synthetic example response: [daily-summary.synthetic.json](../src/api-service/examples/daily-summary.synthetic.json).
Values are JSON strings carrying decimal precision. Zones with a DST transition exactly at local
midnight follow Python's `fold=0` rule and are not specially handled.

### Period comparison

Parameters: `metric`, `timezone`, `baseline_start`, `baseline_end`, `current_start`,
`current_end`, optional `provider`. Each window obeys every daily-summary limit (93 local days,
supported calendar range). The windows must not overlap; adjacent windows are allowed and either
may come first. Both windows share **one** 10000-record budget counted by unique record id across
summarized records and overlap context. The budget is applied while reading: the second window
may read at most what the first left over, records already loaded for the first window are
reused rather than read again, and every query reads at most one record beyond its remaining
budget. Exceeding the budget returns 422 and nothing is truncated.

The tool runs the daily-summary rules on each window and reduces each provider's days to one
`WindowSummary` per side. Providers are the **union** of both windows: a provider present on one
side only is still returned, its missing side has `value: null`, `days_without_records` equal to
the window length and warning `provider_missing_in_window`, and the difference is `null`.

| Metric class | `method` | Day value | Valid day |
|---|---|---|---|
| Point | `mean_of_daily_sample_means` | the day's `sample_mean` | `sample_count > 0` |
| Cumulative | `mean_of_daily_totals` | the single aligned `daily_total` when the day has no other daily_total record; otherwise `interval_total_sum` only when the day has **no** daily_total record at all | a usable value exists |

- The window value is the unweighted mean of day values over valid days, rounded half-even to
  six decimals. Every valid day weighs the same. This is **not** the pooled mean of all samples,
  which would weigh densely sampled days more, and not a time-weighted average.
- A **valid day is not a fully covered day**: it only means a usable value exists.
  `days_from_interval_total` counts days whose total is an interval sum, which covers only the
  recorded intervals; such windows carry warning `interval_totals_used`. Using both bases inside
  one window adds `mixed_bases_within_window`; using different base sets in the two windows adds
  `basis_differs_between_windows`.
- Conflicting (`multiple_daily_records`) or unaligned (`unaligned_daily_record`) daily totals
  make the day **invalid**, even when one aligned total is also present; the tool never falls
  back to interval sums when any daily_total record exists for that day.
  Overlapping intervals also make the day invalid. Invalid days are counted in `days_invalid`.
- A zero total is a valid value. Missing data stays `null`.
- Point metrics use `sample_mean` only. A day with a vendor `daily_summary` but no samples is not
  a valid day, but it is not a day without records either: it is counted in
  `days_with_unused_daily_summary` with warning `daily_summary_not_used`.
- Each side reports `days_in_window`, `days_with_data`, `days_without_records`, `days_invalid`,
  `days_from_daily_total`, `days_from_interval_total`, `days_with_unused_daily_summary`,
  `record_count` and `warnings` (its own codes plus the daily-summary codes of its days).
  Fewer valid days than half the window adds `sparse_window`; no valid day adds
  `insufficient_data` and makes the value `null`.
- `difference` is `current.value - baseline.value`, `null` when either value is `null`. No
  percentage change is reported, because a small or missing baseline would magnify noise.
- No significance test, trend judgement, cross-provider merging or SDNN/RMSSD comparison is
  performed. `details` carries both full daily-summary results for traceability, and
  `definitions` explains every statistic and warning code.

Synthetic example response: [period-comparison.synthetic.json](../src/api-service/examples/period-comparison.synthetic.json).

## Assistant (intent routing, clarification and bounded tool execution)

Status: engineering implemented and tested with a scripted model; **model quality is measured
separately** by `src/api-service/scripts/eval_assistant.py` against a local model and reported as observed.
Disabled unless `HEALTHOPS_ASSISTANT_MODEL_BASE_URL` is set. Two adapters exist behind one
`ChatModel` interface, selected by `HEALTHOPS_ASSISTANT_MODEL_BACKEND`: `ollama` (default) uses
Ollama's native `/api/chat`, the only backend with verified reasoning control
(`HEALTHOPS_ASSISTANT_THINKING=false` suppresses Qwen3's thinking; verified by the absence of
the `thinking` field and a much smaller generated-token count); `openai_compatible` targets any
OpenAI-style endpoint (vLLM later) and refuses a thinking setting because that endpoint ignores
it. The orchestrator does not depend on a specific model server, but a compatible HTTP shape
does not guarantee identical behaviour: changing the server requires rerunning the protocol
tests and the evaluation.

| Method / route | Meaning |
|---|---|
| POST `/v1/assistant/ask` | One question or clarification reply; structured response; 503 when no model is configured |

**Clarification.** The endpoint first routes the request to `ready`, `needs_clarification`, or
`unsupported`. Responses add nullable `routing_status`, `request_spec`, `conversation_id` and
`conversation_version`. To reply, send both continuation fields along with the new question
and unchanged timezone/provider. Missing, foreign, expired or stale versions return 409.
An authenticated `cancel: true` with both fields closes the pending state. See
[resumption semantics and limits](assistant-clarification.md).

**Input.** `question` (1-2000 characters), `timezone` (required IANA name; the server injects it
into every tool call and the model cannot choose another), optional `today` (evaluation mode
only, otherwise 422; the response echoes the date actually used). Identity comes from the bearer
token, verified in a short-lived session that is closed before the request waits for the
model, so no pooled connection is held while the model thinks.

**Time semantics.** The server resolves relative phrases into concrete dates and gives them to
the model; the model never computes dates. Supported phrases: today, yesterday, this week
(Monday through today), last week (previous Monday to Sunday), the week before last, three
weeks ago (calendar weeks), past 7 and past 30 days (ending today, inclusive), the previous
7 and previous 30 days (immediately before those), and "last night" for sleep (yesterday,
because sleep intervals are assigned to their start day). Dates written in the question are
used as written. The intake router also supports past 15 days. Ambiguous or unrecognized
time phrases produce a clarification instead of guessed dates. On resume, the original
reference date and timezone are retained. The response returns the resolved dates.

**Tool calls.** Intake uses only the structured `route_request` decision. The execution model may call `availability`, `daily_summary`, `period_comparison`
(schemas generated from the tool parameter models, with no identity, timezone or provider
field), `lookup_reference`, `state_findings`, and `decline_request`. In the routed HTTP path, a conflicting model-written provider is rejected before execution.
The legacy unscoped toolbox drops model filters as `provider_filter_ignored`. Scoped HTTP calls must match the confirmed comparison windows exactly. Legacy unscoped
comparison windows given in reverse order are reordered so that baseline is earlier, reported as
`windows_reordered_baseline_is_earlier`, and the model is told. Arguments are validated with the same Pydantic models, the
authenticated `user_id` and the request `timezone` are injected, and the tool's own limits
(date range, window overlap, record budget) still apply. Tools run in a worker thread with a
short-lived database session. Execution budgets: at most 4 tool calls and 6 model turns, plus one intake model call; a wall-clock
deadline that covers queueing behind the concurrency gate, every model call and every tool
call (a tool already running in a worker thread finishes on its own, its result is discarded);
a character budget checked against the **whole** request sent to the model before every call
(system prompt, question, tool schemas, model output and tool results; characters are a
conservative proxy, not tokens); and a byte budget for evidence returned to the caller.
Exceeding a budget is an explicit status, never silent truncation. One invalid tool call is
returned to the model as a structured error; a second one stops the request.

**Output.** Two independent statuses. `status` is the data query status: `answered`,
`insufficient_data` (no executed tool returned usable data: an empty availability series, a
daily summary with no records, or a comparison with no window value on either side), `refused`
(the model called `decline_request` with a category: `medical_advice`, `other_person`,
`unsupported_time_range`, `invented_value_requested` or `out_of_scope`; unknown categories map
to `out_of_scope`; `answer` is the fixed sentence for that category and the model's free-text
`detail` is only returned as `model_text`), `needs_clarification` (the question names a device or
channel but no `provider` was chosen in the interface; names in the question are never applied
as filters), `no_tool_answer` (the model replied in text with no successful tool result behind
it, whether it called no tool or only failed ones), `tool_error`, `budget_exceeded`,
`model_error`. `explanation_status` is `verified`, `unverified` or `not_applicable`.

**Cards and claims.** `cards` are built deterministically from the evidence: one daily table
per provider (date, value exactly as the tool returned it, a display form such as
"6 h 45 min (24300 s)" produced by integer arithmetic, the kind of statistic, and a per-day
`quality` with a `records` count: `value` (a recorded value, zero included), `unusable`
(records exist but no reliable value could be derived, for example overlapping intervals;
displayed as "no reliable value") or `none` (no records; displayed as "no data"). A missing
value is never displayed as zero), one comparison card per provider (both windows, values, days with
data, the difference and a direction computed by the server), or one availability card. Cards
never sum across days or merge providers; interval sums are labelled as not full-day coverage.
The model finishes by calling `state_findings` alone with the complete findings, each a plain string
`<type> <evidence_id> [<ISO date>]` with type `value_on_date`, `window_difference` or
`missing_data`, citing a card's `evidence_id`. Parsing is tolerant (commas, repeated findings),
verification is strict: Python checks every claim against the cards (the date must be a real
calendar date inside the window and hold a value, a difference must exist, missing data must
really be missing) and renders fixed template sentences; the direction of change comes from
the card, never from the model. A `missing_data` sentence distinguishes "no records" from
"records exist but no reliable value", per day and for a whole window; a date such as
`2026-09-31` is rejected as "not a calendar date" and degrades the explanation rather than
raising. If a scoped finding is invalid or coverage is incomplete, one bounded correction is offered.
If the corrected finding is still invalid/incomplete, or the model answers in prose instead of findings,
`explanation_status` is `unverified` and `answer` is the fixed fallback text while the cards
still show the data. Model prose is returned only as `model_text` for debugging; clients must
not display it as an answer or execute JSON embedded in it. `answer` is therefore always
template text or a fixed message.

**Scope and coverage.** Routed requests enforce the confirmed metrics, tool family, exact
windows and source context before any data query. `coverage` reports query/result/answer state
for each required target. `completion_status` is `complete`, `incomplete` or `not_checked`.
`verification_attempts` records invalid reasons and missing targets (at most two attempts for
scoped requests). New finding types `window_summary` and `available_series` cover a whole daily
card or inventory without inventing a health judgement. See [execution guards](assistant-execution-guards.md)
for exact coverage rules and limitations. Legacy direct-core calls without a request spec do
not enforce these checks and report `not_checked`.

**Reference library (RAG).** A fourth model-callable tool, `lookup_reference(query)`, runs a
semantic search (pgvector, cosine) over licence-checked reference passages embedded with
`nomic-embed-text` and returns up to `knowledge_top_k` passages above
`knowledge_min_similarity` (default 0.55), each with source title, publisher, section, URL,
licence and the quotable text already cut to the source's quotation limit. Passages become
cards of kind `reference` (`e<n>.r<k>`), and the model may cite one with the finding type
`reference <evidence_id>`; the template renders the source, section, the verbatim quote, the
URL and the sentence "This is general reference material, not an assessment of your own
values." A reference claim against a data card, or a data claim against a reference card, is
invalid. When nothing is above the threshold the tool answers `no_reference_found`; when no
embedding endpoint is configured it answers `knowledge_base_unavailable`. The tool reads
nothing about the user. Questions that ask to judge the user's values against a guideline are
still declined as medical advice. A committed end-to-end example (question, the model's `lookup_reference` call, the retrieved passages with similarity, and the verified answer) is [reference-lookup.synthetic.json](../src/api-service/examples/reference-lookup.synthetic.json); the ingest run log is [rag-ingest-run.log](../src/api-service/examples/rag-ingest-run.log).

**Request.** `provider` (optional) is the data channel chosen in the interface and is injected
by the server into every tool call (`provider_from_request`). A channel named only in the
question yields `needs_clarification`; a channel such as `healthkit` is not one device.

**Debug page.** `GET /debug/assistant` serves a same-origin static page for local verification
(token typed by the operator and kept in session storage, strict Content-Security-Policy, all
API text rendered as DOM text nodes). It is a debugging interface, not the iOS chat feature;
the `cards` and explanation contract are independent of it so a SwiftUI client can reuse them.

**Clients.** The iOS app decodes `status`, `explanation_status`, `answer`, `claims`, `cards`
and `disclaimer` only; `model_text`, `tool_calls`, `evidence`, `model` and `limits` are debug
fields that the phone does not decode. Unknown enum values decode as "unknown" and are shown as
unverified, never as success. The response fixtures the Swift tests decode are generated from
the orchestrator by `src/api-service/tests/test_assistant_fixtures.py`. The route answers `429` with
`Retry-After` when a user exceeds `assistant_rate_limit_per_minute`.

`claims` lists each finding with its parsed claim (null when the string was unparseable, with
the raw string kept), validity and reason. The debug page renders each section independently,
so a malformed section never removes the cards from the page; a Node-based DOM test replays a
synthetic response with an unparseable finding (`src/api-service/tests/test_debug_page_dom.py`).
`tool_calls` lists name, the executed arguments (after validation and server adjustments),
`model_arguments` exactly as the model wrote them, status, tool warnings and duration, so an
adjustment is always visible as a before/after pair. `evidence`
holds the full tool results within the evidence budget; a result that does not fit is replaced
by an explicit omission marker. `numeric_consistency` reports whether numbers in the answer
appear in the tool results (`consistent`, `unmatched`, `not_applicable` when the answer has no
numbers). **This is a diagnostic, not fact verification**: matching numbers can still be
attached to the wrong direction, unit, window or provider. `time_semantics`, `model` (name,
endpoint host, prompt version, sampling settings), `limits` and a fixed non-medical
`disclaimer` complete the response. The model is instructed to report values in the tool's
unit and granularity and never to average, total or convert across days itself; questions that
need such aggregates are answered with per-day values or a period comparison, because only tool
outputs may appear as numbers. `model.thinking` reports the reasoning
setting actually sent to the backend (`false`/`true` for the Ollama adapter) or `null` when the
adapter does not control it (the OpenAI-compatible adapter, which refuses a non-empty setting). Logs record tool names and durations,
never questions or health data.

**Evaluation.** Synthetic development and held-out sets live in `src/api-service/eval/`, generated from one
source of truth (`build_sets.py`) so that every expected window comes from the same time
semantics and every data condition is computed from the synthetic history; a test fails on
drift. Metrics and the manual rubric are described there. Numbers from a local quantized model are small-sample
descriptive results, not deployment measurements.

## Provider connections v0: Google Health (server-side connector)

Status: implemented and tested against a fake Google (OAuth + Health API) in the integration
suite and exercised once against the real service (2026-10-03). Enabled only when the four
`HEALTHOPS_GOOGLE_OAUTH_*` / `HEALTHOPS_CONNECTION_KEY` settings are present; otherwise every
route answers 503.

| Route | Auth | Purpose |
|---|---|---|
| `POST /v1/connections/google-health/session` `{timezone}` | bearer | Creates a one-time `state` bound to the user (10 minutes) and returns Google's authorization URL (offline access, three read-only scopes). |
| `GET /v1/connections/google-health/callback?state&code` | none (browser) | Validates the state (unknown, expired or reused states store nothing), exchanges the code with the server-side client secret, calls `users/me/identity` (an unlinked Google Account is rejected and its token revoked), stores the refresh token encrypted, and redirects to `healthops://google-health?status=<word>`. Status words: `connected`, `denied`, `account_not_linked`, `no_refresh_token`, `token_exchange_failed`, `provider_unavailable`, `invalid_state`. No token travels in any URL. |
| `GET /v1/connections/google-health` | bearer | Connection status, scopes, last sync report (counts and record identities only) and the outcome of the latest authorization attempt. |
| `POST /v1/connections/google-health/sync` `{timezone, days?}` | bearer | Pulls the last `days` local days (default 14, max 90) and ingests them as `provider = google_health`. Per data type the report lists fetched, inserted, duplicates, conflicts and an error; a type's watermark advances only when every page succeeded. 409 when the Google authorization expired (reconnect). |
| `DELETE /v1/connections/google-health` | bearer | Revokes the token at Google (best effort, reported) and deletes the stored credential. Synced records stay. |
| `DELETE /v1/connections/google-health/data` | bearer | Deletes every `google_health` observation of the user. Separate from disconnecting on purpose. |

Mapping (checked against a real account on 2026-10-03): `steps` intraday interval points to
`interval_total` (Fitbit emits one-minute intervals only for minutes with steps, a few dozen
per day); `daily-resting-heart-rate` to `daily_summary`; `daily-heart-rate-variability`
(`averageHeartRateVariabilityMilliseconds`) to `hrv_rmssd` `daily_summary`, kept apart from
HealthKit's SDNN; `sleep` logs (`minutesAsleep`) to `interval_total` seconds. Continuous heart
rate is not synced from this channel (samples every few seconds exceed the tools' budget and
`dailyRollUp` returned no points for the tested account). Only points with
`dataSource.platform = FITBIT` are kept: Google Health also stores copies of Apple Health data,
which already arrive through the HealthKit channel; skipped points are counted per type as
`skipped_other_platform`. Record identities are built from the type, its own time fields and
the platform because most Google data points have no `name`. An upstream revision (same
identity, different payload) is never overwritten: the stored value is kept and the identity
appears under `conflicts`. Active energy and active minutes are not mapped yet.
`source_device` is the Google data source's device display name when present, otherwise
null; the channel label in the app is "Google Health", never a guessed device.

## iOS upload adapter (implemented; Simulator-verified with synthetic data, then used on a physical iPhone)

[src/frontend-app/HealthOpsUploader](../src/frontend-app/HealthOpsUploader/README.md) is the first client of this
contract. It maps HealthKit samples to canonical observations and uploads them with
replay-safe incremental sync. No server behavior changed for it.

| HealthKit type (explicit `HKUnit`) | Canonical | Aggregation | Conversion |
|---|---|---|---|
| heartRate, restingHeartRate (count/min) | `heart_rate`, `resting_heart_rate` bpm | `sample` | none |
| heartRateVariabilitySDNN (ms) | `hrv_sdnn` ms | `sample` | none; never `hrv_rmssd` |
| stepCount (count) | `steps` count | `interval_total` | rounded to integer |
| activeEnergyBurned (kcal) | `active_energy` kcal | `interval_total` | none |
| appleExerciseTime (min) | `exercise_duration` s | `interval_total` | minutes x 60 |
| sleepAnalysis asleep states | `sleep_duration` s | `interval_total` | value = sample interval in seconds, computed on the device; in-bed/awake not uploaded |

- `source_record_id` = `HKSample.uuid`; `source_device` = HealthKit device or source label,
  provenance only; `source_timezone` = `HKMetadataKeyTimeZone` when present, otherwise the fixed
  `UTC` fallback so replays stay byte-identical. Values are decimal strings with at most six
  fractional digits, never display strings.
- Pages of at most 200 samples are persisted with their candidate anchor before upload; the
  anchor commits only after this API confirms the page. Failed pages are replayed byte-for-byte
  and resolve through the replay rules above. Paging ends on the device query's own count
  (samples plus deleted objects), not on the filtered count. The verified identity is bound to
  the exact server URL and credential that produced it and is cleared when either changes; a
  late `/v1/me` response for an earlier pair is ignored. Consent is bound to server URL and the
  verified user; sync state is additionally bound to the HealthKit type and the consented
  history start. A sync runs
  on a frozen context and is cancelled before its next request when consent is revoked or the
  identity changes; an in-flight request cannot be recalled. Re-consenting with a different
  history range discards pending pages from the old range instead of replaying them. Only
  samples that start at or after the history start are read (`strictStartDate`). Samples the
  adapter cannot map are recorded on the device and shown, not uploaded and not dropped.
- HealthKit deletions are counted on the device but not propagated: this API has no deletion
  sync. Sleep stage samples are summed by the daily-summary tool under its own rules; the
  adapter does not reproduce Apple's source priority or produce a "total sleep last night".
- First verified in the iOS Simulator against the loopback server with synthetic samples;
  later used from a physical iPhone over HTTPS against the cloud development VM (synthetic
  accounts, then the owner's own data under explicit consent). Google Health data arrives
  through the server-side connector above, not through the phone.

The cross-language test `src/api-service/tests/test_ios_contract.py` validates the adapter's actual encoding
([ios-upload.synthetic.json](../src/api-service/examples/ios-upload.synthetic.json)) with this schema and, where
the Swift toolchain exists, regenerates it to catch drift.

### Structured clarification UI (2026-11-01)

A pending routed response may include `clarification: {version: 1, fields: [...]}`.
Each field has `id` (`intent`, `metrics`, `time_range`, `baseline`), a localized `label`,
`selection` (`single`, `multiple`, `text`), `allows_custom`, and `options` containing
`id`, `label`, and `reply_text`. There are no selected defaults. Only unresolved fields appear.
Clients compose selected reply text and explicit custom corrections into the next `question`;
no client selection bypasses routing, grounding, scope or evidence checks.

`conversation_expires_at` is an optional ISO timestamp for conservative local checkpoint expiry;
it is fixed for the pending conversation, not extended on each reply. Older pending rows may
omit it. The database remains authoritative and may reject a locally restored version with 409.
Completed, unsupported and clarification-limit responses do not carry actionable form state.

Empty successful reference retrieval now returns `insufficient_data`, `not_applicable`, no
claims/cards, and a fixed no-matching-reference message. This is not a verified reference answer
or proof of complete evidence coverage. Retrieval exceptions still return tool errors.
