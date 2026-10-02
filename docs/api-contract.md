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
