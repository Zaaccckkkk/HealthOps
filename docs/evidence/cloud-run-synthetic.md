# Cloud Run synthetic deployment evidence

Observed during the October 29-30, 2026 deployment session. All application records used here
are synthetic. The old personal-health database was not copied or queried.

## Deployed configuration

- Project: `example-healthops-project`; API and Cloud SQL region: `us-east4`.
- API service: `healthops-ms3-api`, initial revision `healthops-ms3-api-00001-2cb`.
- Image digest: `sha256:9d50832fc6b3cf5fc7418f842d17e35a02dbc4bae930e4af55ac16823b654f37`.
- Successful Cloud Build: `0487066a-ff35-4495-915d-fa4d2cf651ef`.
- Separate private SQL instance: `healthops-ms3-synthetic`, database `healthops_synthetic`.
- Alembic revision `0004`; pgvector `0.8.5`; runtime role has no superuser/createdb/createrole rights.
- Model VM: new `healthops-ms3-model`, `us-east4-c`, NVIDIA L4, driver `580.178.04`.
- Ollama `0.35.1`; Qwen3-8B digest prefix `500a1f067a9f`; nomic-embed-text prefix `0a109f422b47`.
- Existing model prompt `2026-10-17.1`, temperature 0, thinking false; no prompt or behavior changes.
- Ollama is bound to the VM private interface; Cloud Run clients use a restricted network tag.

The attempt to start the old `healthops-gpu` failed with
`ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS`. A fresh model-only VM was created in another zone;
its disk was not cloned from the old VM. The old VM remained stopped.

## Observed application checks

| Check | Observed result | Meaning / limit |
| --- | --- | --- |
| No Google identity | HTTP 403 | Cloud Run IAM protects the service |
| IAM identity, readiness | HTTP 200 | API can read the database schema |
| IAM identity, no application token | HTTP 401 | Platform identity does not replace user identity |
| Three application tokens | All `/v1/me` IDs matched their assigned cohort IDs | Synthetic account bootstrap succeeded |
| Account A deletes account B's observation ID | HTTP 404; B's record remained | Cross-account mutation refused |
| Daily-summary tool | HTTP 200 | Deterministic tool can query cloud records |
| First cohort upload | 16,276 inserted, 0 duplicates | All selected source rows traversed authenticated HTTP ingestion |
| Full replay | 0 inserted, 16,276 duplicates | Replay did not duplicate observations |
| RAG ingestion | 570 chunks across 12 sources | Existing licensed/project corpus embedded into new SQL database |

Account totals: `synthetic-00000` (Apple Watch only) 5,780; `synthetic-00003` (Fitbit only) 219;
`synthetic-00001` (paired) 10,277. The database contains three application users. Reference chunks
are separate data, not additional users or health observations. Source dates were preserved.

## Actual model request, not a baseline rerun

For the Apple Watch synthetic account, ask:
“How many steps did I take on August 1, 2026?” with timezone `America/New_York`.

Observed: HTTP 200; `status=answered`; `explanation_status=verified`; 7.52 seconds for this single
request. Trace: `daily_summary` -> `state_findings`, with valid `value_on_date` claim referencing
`e1.healthkit`. The answer reported 2,665 steps and explicitly described a sum of recorded intervals,
not full-day coverage. This is a synthetic fixture result, not a real person's record or a latency benchmark.

A preceding probe asked about September 1, outside this account's recorded dates. The tool correctly
returned `no_records_in_window`, but Qwen selected `decline_request(unsupported_time_range)`.
The server therefore displayed an inaccurate explanation of the missing-data condition. This remains
an observed model failure, not a deployment success criterion or evidence of improved accuracy.
No grading rules, prompt or runtime logic were changed to hide it.

## Local checks and limits

- `cd src/api-service && .venv/bin/python -m pytest -q ../../scripts/tests`: **22 passed**.
- Target tests include import target refusal, separated account batches, max batch size 500,
  independent IAM/application headers and deployment security flags.
- `python3 scripts/check_public_content.py`: passed during deployment; rerun before publication.
- Existing API unit/integration, data-pipeline and iOS suites were not rerun for this deployment-only
  change. No iOS IAM integration, load test, full 30-question baseline rerun, backup restore exercise,
  fine-tuning, Vertex Pipeline or W&B experiment was performed.
- The model VM stops automatically after two hours. Cloud SQL and storage remain billable.
- Teachers have not been granted IAM access; the URL alone is not a public demonstration link.
- Raw reports and credentials remain in ignored `.local/cloud/ms3/`; no credentials are published.

See [deployment procedure](../../src/deployment/cloud-run/README.md) for access and lifecycle commands.
