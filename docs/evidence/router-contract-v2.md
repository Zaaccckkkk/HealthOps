# Router contract revision: 2026-10-31.2

Update: the later live retest completed 35/63 scenarios. See the
[completed retest and diagnosis](router-contract-v2-retest.md). Capacity-blocked attempts
below are retained as historical evidence.

## Change

The previous live Cloud Run diagnostic passed 3/63 scenarios; 60 requests failed before
execution with model errors. Separate direct reproductions showed missing routing decisions,
confusion between decision and task, and incomplete nested date objects.
See [the original report](cloud-routed-evaluation.md).

[Router implementation](../../src/api-service/healthops/assistant/routing.py) now exposes seven
required fields: `decision`, `task`, `metrics`, `time_phrase`, `baseline_phrase`, `language`,
and `reason`. Field descriptions distinguish classification from task selection. The schema
inlines enum definitions, and nine complete examples cover supported tasks, clarification,
continuation, comparison and refusals. Python parses quoted relative/ISO time expressions;
existing user grounding, date bounds and comparison checks remain enforced.

Invalid arguments still fail validation. Missing decisions are not defaulted to `ready`.
Ambiguous scope is not silently expanded. The frozen 63 scenarios and their gold are unchanged.
This remains a development diagnostic, not a held-out evaluation or a fine-tuning result.

## Local verification

- Targeted Router, guards, evaluator and repository tests: 83 passed, 2 skipped without a DB.
- Full isolated Compose test run: 325 passed, 5 skipped for unavailable host tools.
- Host iOS-contract and evaluation-set checks: 17 passed; browser DOM checks: 3 passed.
  These include the five host-dependent tests skipped in Docker, not additional unique coverage.
- Ruff lint/format, public-content policy, whitespace and changed Markdown links passed.

Tests assert required fields, inline enums, rejection of incomplete decisions, supported and
ambiguous dates, complete prompt examples, and rejection of dates absent from user messages.
Existing tests exercise clarification state and API behavior with scripted model outputs.
None of these test counts measures Qwen accuracy.

## Cloud build

Build `a9d77a8c-69d9-449f-bdff-4678d2725435` succeeded with image digest
`sha256:7577cf42554281c0a0199711d4ff3460fbde58fa0b135be7768f3066f21766df`.
The source was the uncommitted Router revision on top of `66fe763`; the image digest identifies
the built code. Public documentation added afterwards is not part of that service image.

## Live retest status

The live retest was blocked by GCP capacity. Starting the original dedicated model VM in
`us-east4-c` failed. Temporary L4 attempts in `us-central1-c`, `us-east1-d`, `us-west1-a`, and
`us-west4-a` also failed. A balanced-disk capacity failure in `us-west1-a` was followed by a
standard-disk retry, which then failed for L4 capacity. No new model requests were executed.
The previous 3/63 result must not be presented as a measurement of this revised contract.

The new image was built but not deployed. Normal Cloud Run traffic remains 100% on
`healthops-ms3-api-00002-ps7`; the earlier routed candidate retains zero normal traffic.
Original VMs remain stopped and original disks are preserved. Temporary snapshot and SSH rule deletion succeeded; inventory checks found no temporary VM
or disk remaining. No data import, migration, commit or push was performed.

Resume with the same model digest, frozen question set and grading rules once a GPU is
available: inspect direct routing smoke cases, deploy a zero-traffic evaluation revision,
run HTTP smoke cases, then all 63 scenarios. Report stage reach and end-to-end pass rate
separately. The prepared container image alone is not evidence of deployed behavior.


## Renewed recovery attempt (October 31, 2026, afternoon America/New_York)

The user requested another live retest and subsequently authorized a single temporary T4
fallback. The pinned image still existed; question-set and cohort-lock hashes matched the
original cloud run. Regional Compute Engine L4 quota in us-central1 was 1 with usage 0.
This did not guarantee available GPU capacity.

Observed allocation failures:

| Hardware | Location | Result |
| --- | --- | --- |
| Original L4 model VM | us-east4-c | Resource pool exhausted |
| Temporary L4, g2-standard-4 | us-central1-c | Exhausted; suggested us-central1-a |
| Temporary L4, g2-standard-4 | us-central1-a | Resource pool exhausted |
| Temporary L4, g2-standard-4 | us-east1-b | Resource pool exhausted |
| Temporary L4, g2-standard-4 | us-west1-b | Exhausted; suggested us-west1-a |
| Temporary T4, n1-standard-4 | us-central1-f | Resource pool exhausted |
| Temporary T4, n1-standard-4 | us-central1-c | Resource pool exhausted |
| Temporary T4, n1-standard-4 | us-east1-c | Resource pool exhausted |

Capacity suggestions were not reservations: the suggested central zone also failed when
requested. No model inference, new deployment, data import, migration, or scoring run took
place. The previous 3/63 score still describes only the original Router, not revision v2.
Existing local test results above were not rerun because application code did not change.

Recovery used only a snapshot of the dedicated model disk, not the personal-data VM.
Temporary machines were configured for a two-hour maximum run duration. The temporary snapshot and SSH rule were deleted, and final inventory checks found no
temporary VM or disk remaining. Original VMs remained stopped, and normal API traffic stayed
on the existing revision. Raw operation evidence remains local-only
under `.local/cloud/ms3/retest-capacity-operations-1031.json`.
