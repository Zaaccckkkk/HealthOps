# Synthetic cohort import: local evidence

Date: 2026-10-26. Branch: `milestone3`. Local Docker Compose, PostgreSQL 17 with pgvector.
Input: the existing `synthetic-v0.1` DVC version, restored from a local backup; no dataset
was regenerated. SHA-256 checks passed for the users file and both observation files.

| Selected cohort | Synthetic source user | Imported observations | Source-local date range |
|---|---|---:|---|
| Apple Watch only | synthetic-00000 | 5,780 | 2026-06-30 to 2026-08-18 |
| Fitbit only | synthetic-00003 | 219 | 2026-07-31 to 2026-09-13 |
| Paired | synthetic-00001 | 10,277 | 2026-05-14 to 2026-08-09 |

`make synthetic-import`, first execution: 16,276 inserted, 0 duplicates, completed.
Second execution: 0 inserted, 16,276 duplicates, completed.

The read-only `scripts/smoke_synthetic_import.py` passed for all three accounts against the
running local API: authenticated identity, exact per-series observation counts and a
daily-summary response. It passed again after rebuilding the API.

One local assistant smoke question, using only the Apple Watch synthetic account:
"Show my steps for 2026-08-18." HTTP 200, `status=answered`,
`explanation_status=verified`; calls were `daily_summary` and `state_findings`.
The pre-existing API initially returned HTTP 503 because no model endpoint was configured.
It was rebuilt with `ASSISTANT_MODEL_BASE_URL=http://host.docker.internal:11434`, using the
already available local `qwen3:8b`. This is one connectivity check, not a model accuracy score.

Verification:

- Full isolated PostgreSQL suite: 255 passed, 5 skipped (Node and Swift absent in container).
- Host Node DOM tests: 3 passed; host iOS contract tests: 4 passed. The two Swift execution
  tests initially failed due to sandbox compiler-cache permissions, then passed with cache
  access; no assertions were changed.
- Required host API/policy selection: 191 passed before the two Swift permission failures
  above were resolved. Optional importer unit run: 13 passed, database tests skipped on host
  and exercised by the isolated container suite.
- Ruff, formatting, changed-document local links and public-content policy checks passed.

Local reports, UUID mappings, credentials and dataset contents are excluded from Git. Only
synthetic accounts were created; no real records were read, copied or uploaded. No cloud
resource was created, and no large-cohort model evaluation or training was performed.

Instructions: [synthetic import](../synthetic-import.md).
