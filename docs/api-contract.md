# Canonical observation API v1

Status: local-development contract, not a deployed wearable integration.
OpenAPI at `/docs` is generated from [schemas.py](../services/ingestion-api/src/healthops/schemas.py) and
[main.py](../services/ingestion-api/src/healthops/main.py). Dates in this document are illustrative.

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

`ready` is an initial schema check, not proof that every migration or external provider works.

## Input

See the complete [synthetic JSON request](../examples/observations.synthetic.json).

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

Synthetic example response: [daily-summary.synthetic.json](../examples/daily-summary.synthetic.json).
Values are JSON strings carrying decimal precision. Zones with a DST transition exactly at local
midnight follow Python's `fold=0` rule and are not specially handled.

## Adapter contract for the existing Swift prototype (not implemented here)

The inspected prototype has `HealthRecord.value: String` and `GoogleHealthRecord.value: String`
for display. These are unsuitable as ingestion DTOs. A later adapter should use typed raw samples:

- HealthKit: preserve `HKSample.uuid`, numeric value, requested HKUnit, original start/end,
  and actual source/device metadata. HealthKit is not exclusively Apple Watch data.
- Google Health: preserve the data point resource `name`, interval/sample timestamps, numeric
  fields and returned device provenance. Do not substitute random IDs on each sync.
- Keep SDNN and RMSSD separate based on actual returned fields. Do not infer a metric from
  the UI label alone. Daily totals may be revised, requiring a version/update policy before
  live continuous synchronization.
- Require an explicit explanation/consent for uploading health data beyond on-device reading.

No prototype secrets, Xcode user state, health exports or personal data are copied into this repo.
