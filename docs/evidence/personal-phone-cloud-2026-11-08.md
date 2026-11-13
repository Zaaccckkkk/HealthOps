# Personal phone cloud acceptance preparation — 2026-11-08 UTC

Source commit: `5aaedff` (`milestone3`). Work occurred on November 7 in New York.

## Deployment and isolation

- New `healthops-personal-api` Cloud Run service, revision `healthops-personal-api-00001-4s9`,
  with a dedicated runtime service account and database secret.
- Separate `healthops_personal` PostgreSQL database in the existing Cloud SQL instance;
  migration and runtime roles are separate. No existing personal history was copied.
- Shared V100/Ollama model service retained with its existing two-hour automatic STOP.
  This setup does not provide confidentiality from team project administrators.
- Both the personal service and synthetic revision `healthops-ms3-api-phone-boundary-1107`
  use the same immutable image:
  `sha256:18212a3d4a4582f4952486e4c5bc6309c1b2c0dda88b274be519862fa41a6bd0`.
- Cloud Build: `497033fd-a978-4675-b22b-42d4c3846ce9`.
- Personal invocation allows HTTPS end users; all identity, health-data, tool and assistant
  routes still require an expiring HealthOps bearer token. Synthetic invocation retains IAM.
- Public reference ingestion: 12 sources, 570 chunks. No health observations were inserted by
  provisioning or smoke checks. No credentials, health values or device identifiers are included here.

## Observed checks

| Check | Result |
|---|---|
| Swift package suite | 64 passed; fake network and synthetic fixtures |
| API and repository tests | 466 passed, 3 existing Transformer warnings; isolated `healthops_test` |
| Signed iOS build and installation | Succeeded; owner confirmed the app opens after developer trust |
| Personal `/health/ready` | HTTP 200 |
| Missing/invalid token, or synthetic token on personal service | HTTP 401 |
| Personal token on synthetic service | HTTP 401 after Cloud Run IAM authentication |
| Synthetic/missing upload declaration on personal service | HTTP 403 before ingestion |
| Personal upload declaration on synthetic service | HTTP 403 before ingestion |
| Evaluation date override on personal service | HTTP 422 |
| Personal SQL runtime reading synthetic tables | Denied |
| Synthetic SQL runtime connecting to personal DB | Denied |
| Personal SQL runtime creating schema objects | CREATE privilege absent |
| Empty-account chart | HTTP 200, no records |

The test run includes a pre-existing local addition in `tests/test_tools.py`; that addition was
not included in this commit. Unrelated training changes were also left untouched. Python lint,
diff whitespace, local Markdown links and the public-content policy passed. Docker rebuild/full
container tests and data/training service suites were not rerun for this boundary change.

## Model and clarification smoke

The fixed `qwen3:8b` model ran on V100 with full observed GPU offload
(`size_vram = size = 9,969,650,892` bytes, 32,768 context). This was a deployment smoke, not a new
accuracy benchmark or validation on real health data.

Both initial questions with English month names requested time clarification, despite an explicit
date. This is an observed date-understanding limitation; successful HTTP responses are not counted
as correct first-turn answers. Replies using an explicit ISO date range resumed persisted state:

- Empty personal account: `daily_summary → state_findings`, `insufficient_data`, `verified`, complete
  coverage with `no_records`. The observation table was still empty after the probe.
- Existing v0.2 synthetic account: `daily_summary → state_findings`, `answered`, `verified`, complete
  coverage; the answer retained the recorded-interval coverage qualification.

The synthetic candidate was tested at zero default traffic, then promoted to 100%. Current live
and boundary tags point to this revision; obsolete candidate tags were removed without deleting
historical revisions.

## Remaining device acceptance

Installation and the owner's confirmation that the app opens are not evidence of HealthKit upload
or answer correctness. The owner still needs to enter the private endpoint/token, verify `personal`,
grant read access and explicit upload consent, sync a small steps window, and compare the displayed
chart/answer privately against accessible Apple Health records. Counts/status may be recorded;
real health values must not enter public evidence or synthetic evaluations.

See the [personal pilot guide](../personal-phone-pilot.md) for the interaction and known limits.


## Google connector configuration follow-up

The owner subsequently completed a HealthKit sync; a read-only availability query confirmed
records reached the personal account. No measurement values were fetched for this check.
The previous empty-account result above describes the earlier provisioning smoke, not current data.

The existing Google OAuth Web client now includes the personal Cloud Run callback while retaining
the old callback. The client secret and a new personal token-encryption key are held in two
separate Secret Manager resources, accessible to the dedicated personal runtime and pinned at
version 1. No existing user refresh tokens were copied. The Google Health API was already enabled.

Revision `healthops-personal-api-00002-bs4` serves 100% of personal traffic, with the same image
and SQL identity. Observed checks: connector `configured=true`, `connected=false`; anonymous
OAuth session creation returns 401; the authenticated authorization URL has the correct client,
HTTPS callback and three read-only scopes; an unknown callback state redirects to `invalid_state`
without a token exchange. Actual Google authorization and provider sync still require the owner's
phone interaction. These checks are not evidence of a completed Google data import.

Deployment/policy tests: 42 passed. The renderer rejects raw secrets in deployment JSON,
unpinned secret versions and invalid callback origins, and preserves the database secret mapping.

Google connection/mapping regression: 12 passed with fake Google and the isolated local test DB,
covering encrypted token storage, one-time state, denied/unlinked accounts and mapping/sync behavior.

## Source-coverage follow-up

The owner confirmed Google authorization on the phone. A subsequent read-only check confirmed
`configured=true`, `connected=true` and imported Google metric availability. Only source/metric
presence was inspected; no personal measurement values are included in this evidence.

The connector imports daily resting heart rate but not high-frequency heart-rate samples.
The assistant now explicitly explains this boundary on `heart_rate` summary/comparison answers
and evidence cards when Google Health or All channels is selected. It never substitutes
`resting_heart_rate`, drops existing stored records, or claims the upstream Fitbit account has
no heart-rate data. The notice survives failed claim verification without upgrading that result.

Local verification: 293 assistant, source-boundary, Google connector, tool, knowledge, schema,
evaluation-contract and iOS-contract tests passed; database tests used only `healthops_test`.
The initial sandboxed attempt could not access the local database/Swift caches; the rerun with
those permissions passed. The pre-existing local tool test remained outside this change.
The focused 58-test run includes the two-source completeness/repair regression. These are scripted
model/control-flow tests, not a new real-model accuracy evaluation. Full Docker and data/training
service suites were not rerun. Cloud rollout verification is recorded separately when complete.

Source-coverage rollout: commit `d9b6e60`, clean archived API source (unrelated local changes
excluded), Cloud Build `0b55253f-14e6-4923-8e4e-52d473ad9dad`, image
`sha256:5f361d9fd0129afccf75f1462ab980c07f6fc129137ca299dc21d2ba510ff532`.
Personal revision `healthops-personal-api-00003-nt6` is Ready and receives 100% of traffic.
Post-rollout checks: readiness 200; anonymous identity 401; authenticated identity 200 with
personal mode; Google connection 200 with configured/connected both true. The existing OAuth
secret versions and dedicated database identity were retained. The synthetic service was not
redeployed. No live-model accuracy evaluation or personal health-value comparison was run for
this fix; phone re-questioning remains the end-user acceptance step. Fitbit high-frequency
heart-rate ingestion remains unsupported.
