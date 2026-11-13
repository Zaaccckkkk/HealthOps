# Frozen writer holdout: pre-training baseline

The first complete run of [writer-holdout-v1](../../src/api-service/eval/writer-holdout-v1/README.md)
finished on 2026-11-08: 20 new synthetic accounts, 20 scenarios and 22 conversation turns.
This is an assistant-reviewed synthetic baseline, not independently reviewed gold or a
real-user accuracy estimate. No training, application changes or test-driven tuning occurred.

## Execution and freeze

The source revision was `18517e2`. Manifest verification and a dry-run preceded importing
92,308 records into a new loopback-only PostgreSQL 17/pgvector database named `healthops_test`.
The runner checked the exact 20 identities and record counts and rejected extra accounts.
No personal database, Cloud SQL instance or production API was queried for this evaluation.

Requests traversed the authenticated FastAPI ASGI endpoint, real routing and server grounding,
SQL tools, persisted clarification, claims/coverage checks and final writer. The transport was
in-process, not Cloud Run HTTP or an iPhone. The existing V100 VM served Ollama through an IAP
SSH tunnel; no new cloud resource was created. Its two-hour automatic stop remained configured.

| Frozen component | Value |
| --- | --- |
| Model | Qwen3-8B, Q4_K_M |
| Model digest | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` |
| Temperature / thinking / output budget | 0 / disabled / 800 tokens |
| Loaded model context | 32,768 tokens |
| Router version / execution prompt / writer prompt | `2026-11-08.1` / `2026-11-08.2` / `grounded-answer-v2` |
| Dataset | `synthetic-v0.2`, checksum-verified against its frozen manifest |
| Predictor | `20261107T231608Z/w14/transformer` |
| Question freeze | Canonical hash in the unchanged holdout manifest |

Raw prompts, tool results, model outputs, source hashes, import credentials and review details
remain in ignored local artifacts. The local `baseline-freeze.json` hashes the run, all scenario
traces, pre-inference scoring protocol, review and 11 exact writer inputs. The question freeze
manifest retains its historical preparation status; this report records the subsequent run.

## Scoring and observed results

Before inference, the scoring protocol required the expected tool scopes, successful
clarification/resume where specified, and a grounded, useful displayed answer satisfying the
frozen scenario rubric. A partial answer is a strict nonpass. We separately reviewed raw writer
text only where the expected evidence and captured writer input checks all passed. Numerical
membership and production checker acceptance do not establish correct meaning.

| Measure | Result |
| --- | ---: |
| Strict full-chain scenario pass, including displayed answer | **5/20 (25.0%)** |
| Expected evidence available and writer input provenance checks passed | 11/20 (55.0%) |
| Raw writer semantic pass, conditional on those 11 inputs | **6/11 (54.5%)** |
| Raw writer partial / factual failure | 3/11 / 2/11 |
| Production checker accepted / fell back | 9/11 / 2/11 |
| Scenarios stopped before writer | 9/20 |
| Planned clarification conversations resumed and reached writer | 2/2 |
| HTTP responses received | 22/22 returned HTTP 200 |

HTTP 200 includes clarification and insufficient-data responses; it is not task success.
The writer-only denominator is 11, not 20. The two clarification transitions succeeded, but one
resulting explanation was partial. This harder new-account suite and its semantic criteria
must not be directly compared with earlier diagnostic or interaction-suite percentages.

### Per-scenario review

Case names below are the suffixes of `writer-holdout-v1-<name>`. Raw writer partials count as
nonpasses. Terse but correct answers are not failed solely for style preferences.

| Case | Raw writer | Full chain | Main observation |
| --- | --- | --- | --- |
| unfinished_steps | Not reached | Fail | Comparison intent lacks a baseline; server also rejects the singular step wording. |
| sleep_pattern | Not reached | Fail | Bare sleep and normalized date wording fail server grounding. |
| apple_hrv_missing_definition | Fail | Fail | Extrema are assigned to endpoint dates; fallback supplies an availability inventory. |
| heart_definitions | Fail | Fail | A weekly RHR mean is assigned to the last recorded date; checker accepts it. |
| empty_window | Not reached | Fail | Date normalization violates the exact-quote check. |
| period_basis | Not reached | Fail | Router reverses current and baseline windows. |
| single_source_reconcile | Pass | Pass | Correctly withholds comparison without a second usable source. |
| resting_pattern | Pass | Pass | Supported mean and net change; correctly distinguishes variation from a consistent trend. |
| sleep_resting | Not reached | Fail | Router identifies both metrics, but server rejects bare sleep. |
| selected_rmssd | Not reached | Fail | Exact-quote grounding rejects normalized date wording. |
| hrv_definitions | Not reached | Fail | Reference intent, invalid data-tool arguments, then irrelevant reference lookup. |
| steps_plain_summary | Pass | Pass | Supported available-day statistics and an incomplete-trend qualification. |
| clarify_wellbeing | Pass | Pass | Resumes correctly and explains the sparse/absent requested recordings. |
| short_sleep_estimate | Partial | Fail | Correctly reports no readings, but implies an estimate exists and inadequately explains short history. |
| paired_resting | Not reached | Fail | Cross-source request becomes an overlapping period comparison. |
| paired_hrv | Pass | Fail | Useful supported raw answer is rejected by calendar-number checking; fallback misses the main question. |
| paired_sleep_steps | Not reached | Fail | Server rejects bare sleep after the model identifies both metrics. |
| paired_selected_google | Partial | Fail | Correct selected-source values, but omits the missing final day required by the rubric. |
| paired_reconcile | Pass | Pass | Only one channel is usable on the actual date; comparison is correctly withheld. |
| clarify_reconcile | Partial | Fail | Resumes and reports no readings, but implies a synthetic estimate exists when none was produced. |

## Failure attribution

The nine upstream failures are not nine writer errors. Five include otherwise plausible router
proposals blocked by deterministic grounding alone. In
[`route`](../../src/api-service/healthops/assistant/routing.py), `grounded_metric` rejects bare
`sleep` outside the narrow reconciliation exception. `resolve_window` requires an exact user
quote, so a model's `A to B` normalization does not match a user's `between A and B`. Three
other cases have comparison-intent/window problems, one also a singular-step grounding issue.
The remaining case selects reference intent and issues two data-tool calls without timezone.
This isolated database has no RAG corpus; its empty reference result is not a retrieval-quality
benchmark. Even a populated reference library would not satisfy the expected personal-data scope.

Writer failures include binding a real evidence number to the wrong date, substituting extrema
for endpoints, omitting an explicitly required missing-day qualification, and implying an
estimate exists when tool quantiles are null. The latter also exposes an input-contract issue:
[`packet`](../../src/api-service/healthops/assistant/narrative.py) retains a generic estimate
statistic label even when no target reading exists. These are not all problems that final-writer
training alone can solve.

One raw paired-HRV answer meets the semantic rubric but is rejected by
[`checked_numeric_text`](../../src/api-service/healthops/assistant/narrative_checks.py): the
calendar phrase `September 1 to 7, 2026` leaves the range end and year as unmatched measurement
numbers. This was diagnosed without changing the checker or rerunning the held-out question.
The displayed fallback no longer answers whether a larger HRV value indicates a better source.

## Comparison after training

Keep the current model for routing and tool selection when evaluating a separately trained
writer. Replay the **11 frozen, exactly captured message payloads** against the base and trained
writers for a paired writer comparison. Record model and prompt hashes, raw semantic labels,
production-checker acceptance and any fallback separately. Do not hand-create writer inputs for
the nine cases that did not reach that stage and call them observed full-chain captures.

Separately rerun all **20 unchanged scenarios** through the full chain and compare per-case
outcomes. If a changed upstream component enables additional writer inputs, report that change
and its denominator rather than attributing it solely to writer training. Use development and
development-validation data for tuning; do not turn these test failures into preference pairs or
use repeated holdout runs to choose checkpoints. Record post-training results only after execution.

## Limits and verification

- One run on a small, assistant-authored synthetic suite; semantic labels are assistant review
  and remain open to human correction. They are not human-approved gold or clinical validity.
- The expected SQL reference uses the same deterministic tool at the expected scope, not an
  independent numerical oracle. Provenance checks verify capture fidelity, not all tool math.
- Neither paired reconciliation target actually had two usable readings. This run does not
  establish closer-source attribution or interval-writing accuracy; do not swap in easier dates
  after seeing outcomes. Supplementary coverage belongs in a separately versioned suite.
- Isolated import completed with 92,308 inserts and zero duplicates. All 20 cases and 22 turns
  completed without a transport error. The database was stopped after capture, preserving it
  locally; GPU retention follows the existing two-hour automatic-stop policy.
- 36 focused writer/capture/holdout tests and 43 repository-policy tests passed. These verify
  frozen cases, exact account isolation, real-input capture and exclusion from preference mining.
  They are separate from the model success rates above. Public-content and Markdown link checks
  passed. No training, full Docker integration suite or iOS tests were run for this evaluation-only
  increment; production code was not changed.
