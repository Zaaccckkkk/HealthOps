# Pretraining regression and phone writer rollout

## Scope and deployment

On November 9, 2026 (America/New_York), the existing personal Cloud Run service was
updated to application source `e44d1d9`. The build used tracked API runtime files only;
local credentials, datasets, caches and evaluation artifacts were excluded.

- Cloud Build: `5a10e4c1-f910-42ab-b3c7-b413aab92833`, successful.
- Image SHA-256: `d6ba85aa9d9c77287adea5f7d6739c3e763580348c382d7e0f4c1ee02abb4c41`.
- Revision: `healthops-personal-api-writer-1109`, ready and receiving 100% of traffic.
- `HEALTHOPS_ASSISTANT_NARRATIVE_ENABLED=true`; existing personal database, runtime
  identity and Google OAuth secret references were preserved.
- Live/ready/account/connection checks returned HTTP 200; the account remained in
  personal mode and the Google connection remained configured and connected.
  Anonymous account access returned HTTP 401.
- A no-record historical query returned `ready`, `complete`, `generated` and
  `model_generated`, confirming execution of the final writer. Its response stays private.
  An initial 1900-date probe instead requested clarification; it did not validate the writer.
- The current signed iPhone build was installed on the user-confirmed test phone.
  Installation and automated tests are not a substitute for the user's visual acceptance.

The existing V100 inference VM was started with its two-hour stop policy unchanged.
No DPO training job or new cloud VM was started. The private deployment configuration
was updated to pin the image and keep the writer enabled on subsequent deployments.

## New development evaluation

[Case definitions](../../src/api-service/eval/pretraining_cases.py) add ten families
for each of three existing synthetic-v0.2 cohorts: 30 scenarios and 36 user turns.
The six extra turns come from three partial-clarification conversations.

Families cover partial clarification/resume, yesterday, today, resting heart rate plus
sleep, separate HRV definitions, selected Google source, missing recordings, short-history
reconciliation, unsupported medication advice and an ambiguous time range.

These are development probes, not the frozen 20-case holdout or an unbiased population
sample. Cohort variants within a family are correlated. No test evidence or preferred
answers are authored in the cases: the runner obtains evidence from actual SQL tools.

The execution used authenticated ASGI requests, a fresh loopback `healthops_test` database,
16,276 manifest-verified cohort observations, the pinned CPU reconciliation predictor and
Qwen3-8B through the real V100/Ollama endpoint. This evaluation does not measure Cloud Run
network transport or iPhone UI latency. No personal health values were used.

Model digest: `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
Raw prompts, tool results, model completions and source hashes remain in ignored local
artifacts. The capture includes exact final-writer inputs, including warnings and missing data.

## Observed stage results

| Measurement | Result | Interpretation |
| --- | --- | --- |
| Completed scenarios / user turns | 30 / 36 | All planned requests captured |
| Expected routing decision per turn | 27/36 | Includes clarification turns and intended refusals |
| Final ready route on answerable cases | 15/24 | Nine requests stopped at routing |
| Intended clarification/refusal final routes | 6/6 | No tool or writer execution, as expected |
| Exact evidence and writer-input audit | 14/24 answerable cases | Query results and writer packet matched their recorded scope |
| Final writer invoked | 14/24 answerable cases | One additional case failed downstream claims/completeness |
| Natural-language output accepted | 13/14 writer invocations | One completion triggered fallback |
| All mechanical stage checks | 19/30 scenarios | Not a semantic-answer accuracy score |

Nine upstream failures repeated across cohorts: "so far today" requested a time range,
"vary" with both HRV definitions requested an earlier comparison period, and a question
about whether missing steps imply inactivity was refused. The dual-source short-history
reconciliation case reached the correct route but did not produce complete validated claims.
These are upstream issues, not writer-training negatives.

Manual inspection also found errors that mechanical acceptance did not catch: weekly means
were described as last-day values; two providers' means were described as a temporal change;
and a within-window maximum/minimum was described as the week's change without source scope.
A separate sleep answer made an incorrect seconds-to-hours conversion and was correctly
blocked by the output checker. Therefore accepted natural-language output must not be reported
as semantically correct merely because its numbers appear somewhere in the evidence.

Four exact-prompt/raw-output preference candidates were prepared locally with proposed silver
answers. All remain `training_eligible=false` pending independent human review. Two dual-source
candidates share the same evidence and proposed answer; keep them in one split group or deduplicate
them. Neither the existing training split nor the frozen holdout was changed.

## Reproduce

Prepare a fresh synthetic-only database and three-account import using the
[full-chain instructions](writer-full-chain-20261108.md). Then, from `src/api-service`:

```bash
PYTHONPATH=. uv run python scripts/eval_writer_chain.py \
  --pretraining --import-dir .local/eval/pretraining-import \
  --model-url "$HEALTHOPS_EVAL_MODEL_URL" --model-dir .local/reconcile/w14 \
  --output .local/eval/pretraining-new
```

`HEALTHOPS_TEST_DATABASE_URL` must identify the isolated loopback `healthops_test` database.
Keep the manifest, model digest and captured source hashes with the run. Review upstream
failures separately from writer defects before preparing any preference candidates.

## Verification and limits

- 327 selected API, tool, database, iOS-contract and repository-policy tests passed against
  an isolated synthetic test database. This includes the four new evaluation-contract tests.
- 68 Swift tests passed, including retained history across channel changes, invalidated
  pending continuation and ignored late replies.
- Device build and installation succeeded. Real phone interaction acceptance remains pending.
- Ruff and public-content checks passed for the change. Referenced Markdown paths were checked.
- The deployed API image was built successfully in Cloud Build; local full Compose integration,
  data-pipeline tests and model-training tests were not rerun because those services did not change.
- No training, W&B integration change, checkpoint-resume experiment or new clinical claim was made.
