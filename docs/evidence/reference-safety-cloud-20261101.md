# Reference safety and cloud acceptance attempt — 2026-11-01

## Completed implementation and local validation

Implementation commit: `5fedad830004637672bc384f4e80639882c360b0`.
The [reference trust boundary](../design/reference-safety.md) documents the change and limits.

- 20 new synthetic reference-safety tests passed, including benign controls and an explicitly
  acknowledged paraphrase bypass. This is not a live-model attack success rate.
- Full isolated Docker API suite: 370 passed, 5 skipped. Only the Compose test database was used.
- Existing repository/deployment policy tests: 25 passed. An initial invocation without the API
  directory on PYTHONPATH failed collection; rerunning with the correct import path passed.
- Swift tests: 60 passed. The iOS Simulator application built successfully.
- Ruff, whitespace and public-content checks passed. No source corpus or user data was changed.

## Pinned cloud artifact and verified preflight

Cloud Build `9e2550cb-6290-43d2-9741-4d860aa07f22` succeeded using tracked API files only.
The build-source files were compared byte-for-byte with the committed implementation.

Image: `us-east4-docker.pkg.dev/example-healthops-project/healthops/api@sha256:4d1c5084dc32d9b8e50f82aa71ab8a281789825b8cd49040dee2ecbf1e619081`.

The IAM-protected revision `healthops-ms3-api-security-eval-1101` was deployed with zero normal
traffic and frozen-date overrides for evaluation. Real HTTP checks verified all three synthetic
account identities, Cloud SQL readiness and HTTP 403 for unauthenticated access. No data import,
schema migration, personal-history access or IAM widening was performed.

The candidate `healthops-ms3-api-safety-candidate-1101` uses the same image with date overrides
disabled. Normal traffic is not promoted without real-model QA.

## Capacity blocker and measurement limits

The dedicated model L4 in us-east4-c failed to start twice for GPU capacity. Temporary L4 attempts
in us-central1-c, us-central1-a and us-west1-a, and temporary T4 attempts in us-east1-c,
us-central1-f, us-west1-b and us-central1-b also failed for capacity. A separate us-east4-b attempt
failed because that zone does not offer g2-standard-4; this was configuration availability,
not a capacity result. Every attempted VM was configured for a two-hour automatic STOP.

No new model accuracy is reported. The original model disk was not changed or its weights
re-pulled; model digest/hardware verification remains pending until inference can start.

## Observed model-unavailable behavior and cleanup

One real synthetic HTTP request for recorded steps completed in 120.14 seconds with HTTP 200,
application status `budget_exceeded`, and a fixed request-time-budget message. The response
identified prompt version `2026-11-01.2`; model digest fields were null because the server was
unavailable. No answer was invented. This is an infrastructure/failure-path observation, not
an accuracy sample. Two-minute feedback is poor UX; a shorter connection failure path should
be evaluated separately from the model generation/read timeout.

The temporary frozen-date evaluation tag and revision were removed. The no-override
`safety-candidate` tag remains, with zero normal traffic. Normal traffic stays 100% on
`healthops-ms3-api-00002-ps7`; the prior `routed-candidate` tag remains unchanged. Both existing
GPU VMs were verified TERMINATED. No fallback VM or disk remains. The isolated local test
DB was stopped; the existing Cloud SQL instance continues independently.

## Frozen next evaluations

The existing cases and gold expectations were not edited:

| Set | Scenarios / turns | SHA-256 of case file |
| --- | --- | --- |
| Routed diagnostic | 63 / 87 | `1c9a41cfbf3775d0cfc4e4e7f30ad857aabe3c296336792117da0b56480d7110` |
| Interaction generalization candidate | 36 / 72 | `c155a9ec63c62b50728a38fe4788fba0f1b63b1a5bfec01378303362208c1f84` |

Cohort lock: `a1249f63e2c5d6ff7c61287214ebab624e1340f42d5e8f3d963c9174b09e56b4`.
Execution prompt source: `2905004250abaef93753e050450ed4b6bf88b35588678c3c5529c35709c5ee7b`.
Router source: `307ea40e569df21d93b5842790a13ebbacd1262ac89d123fff617d8fba237f21`.
Private freeze/preflight artifacts are under `.local/cloud/ms3/security-freeze/`.

After capacity returns: verify the model digests, create a zero-traffic evaluation tag, open
only the synthetic DB tunnel, run a small smoke subset, then the unchanged 63-case diagnostic
and 36-case interaction set using `evaluate_routed_http.py`. Grade reference answers separately:
the three legacy reference cases grade routing only. Add direct/indirect/multi-turn live-model
security probes and benign controls without poisoning the actual corpus. Report errors by
router, scope, tool, evidence, completeness, infrastructure and latency before choosing training
examples. Do not tune on the generalization set and continue calling it held-out.

## Native client acceptance remains pending

The native client currently sends a HealthOps bearer token, but the protected Cloud Run endpoint
also requires Google IAM authentication. A changed base URL alone is insufficient. Establish an
explicit development-only IAM access path for synthetic acceptance, or implement production user
login as a separate product change. Do not remove Cloud Run IAM to make a demo work.

Then exercise the actual iOS UI: clarification options, custom reply, partial reply, persisted
resume after relaunch, cancellation, network loss and account switching. Package tests and a
successful simulator build do not establish these live interactions. No fine-tuning is started
until failures from the real deployment are classified.
