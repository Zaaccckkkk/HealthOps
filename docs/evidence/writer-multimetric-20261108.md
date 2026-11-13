# Multi-metric writer development probes

## Purpose and scope

The previous 50 full-chain development scenarios contained 44 steps questions,
three sleep questions and three resting-heart-rate questions. The 16-row preference
review therefore reflected question-design and selection bias, not evidence that
steps was uniquely difficult. This follow-up adds 18 development scenarios and
22 actual conversation turns using the existing three synthetic-v0.2 cohorts.
It is not a held-out benchmark or a training run.

All new scenarios use manifest-verified cohort records. No evidence cards, model
answers or new observation values were invented for the questions. The existing
runner also uploads its 34 compatibility edge observations to a separate synthetic
account; none of these 18 scenarios queries that account. No personal records,
Cloud SQL database or phone installation was used or changed.

## Coverage and execution

The scenarios cover resting heart rate with sleep, resting heart rate with SDNN and
RMSSD, ordinary versus resting heart rate, single-provider selection, generic HRV,
wellbeing wording, three metrics and persisted clarification/resume. SDNN and RMSSD
remain separate metric identifiers; this evaluation does not merge their values.

| Synthetic account | Relevant available records in the imported cohort |
| --- | --- |
| synthetic-00000 | Apple Health resting heart rate, heart rate, sleep and HRV SDNN |
| synthetic-00001 | Both providers; Apple HRV SDNN and Google HRV RMSSD |
| synthetic-00003 | Google Health resting heart rate, heart rate, sleep and HRV RMSSD |

The 16,276-record import ran in a disposable loopback PostgreSQL `healthops_test`
database. Requests used the authenticated FastAPI ASGI endpoint, real routing,
SQL tools, clarification persistence, verification and final narrative generation.
Qwen3-8B ran through Ollama on the existing V100 VM. HTTP transport was in-process,
not Cloud Run or an iPhone. Model digest:
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
Temperature was zero, thinking disabled, and output budget 800 tokens. The application
source hashes, settings and frozen questions are recorded with the local run.

## Observed results

| Observation | Count |
| --- | ---: |
| Scenarios / actual turns | 18 / 22 |
| Every expected SQL query result present | 14 / 18 |
| Final writer called | 13 / 18 |
| Captured writer evidence exactly matches generated cards | 13 / 13 writer calls |
| Writer answer accepted by basic checks | 9 / 13 |
| Writer fallback | 4 / 13 |
| Writer preference candidates after silver review | 7 |
| Other retained review/regression cases | 6 |
| Cases stopped before writer | 5 |

These counts are not semantic accuracy. Six accepted answers were still selected
for preference review. Query checks compare with the same deterministic tool run
independently for the expected scope; they are not an independent arithmetic oracle.
The 13 successful provenance checks establish where evidence came from, not that
the generated explanation is correct.

## Findings

- **Source means mistaken for a time trend.** A paired RHR/sleep answer described
  Google and Apple weekly RHR means, 74.38 and 72.88, as a decline over time. It also
  flattened opposite first-to-last sleep directions into a single increase.
- **Extrema mistaken for window endpoints.** Several HRV/RHR answers used maxima
  and minima as undated start/end values. A Google-only answer placed the September
  11 RMSSD minimum at the start of a September 7-12 comparison.
- **Coverage and interpretation.** An answer cited all cards but omitted Apple RHR
  from its prose. Another called an RMSSD increase an improvement without evidence
  of health improvement. One raw answer used the same weekly mean at both endpoints
  while claiming a decline; missing card IDs correctly triggered fallback.
- **Checker regression.** All three relative-time clarification cases were rejected
  solely because numeric validation treated the `7` in `last 7 days` as an unsupported
  measurement. Their raw prose also omitted the unfinished-day qualification. These
  are retained separately, not exported as clean writer preference negatives.
- **Routing/interaction.** Three cross-metric comparison requests prompted for a prior
  time window. Generic HRV prompted for metric clarification, but the options listed
  steps, sleep and RHR rather than resolving SDNN/RMSSD. These did not reach the writer.
- **Context budget.** One three-metric case retrieved all expected SQL results but
  exceeded the context budget with a 24,746-character request before a writer completion. This belongs
  to orchestration/context work, not final-answer fine-tuning.

Seven proposed pairs preserve exact observed prompts and raw rejected completions.
Chosen answers are coding-assistant silver drafts pending independent human review;
all remain `training_eligible=false`. Two candidates use identical account/window
evidence with different dialogue wording, leaving six distinct evidence packets.
They must stay in the same split and should not be counted as independent evidence.
No model, router, prompt or checker was changed to improve these results.

## Implementation and verification

- [Scenario definitions](../../src/api-service/eval/writer_multimetric_cases.py) freeze
  user turns and expected SQL scopes, without authoring evidence or ideal answers.
- [Capture runner](../../src/api-service/scripts/eval_writer_chain.py) accepts
  `--multimetric`. `expected_results_present` requires every requested query result.
  Older single-query captures remain compatible.
- [Capture tests](../../src/api-service/tests/test_writer_chain.py) reject incomplete
  metric coverage and wrong-provider results and validate all new query schemas.
- [Preference tests](../../src/api-service/tests/test_chain_preferences.py) prevent
  export when any expected metric result is missing.

Focused capture, preference, replay, narrative and repository-policy tests: **102
passed**. Ruff and public-content checks passed. New documentation links were checked.
Full service integration, iOS and training-service suites were not rerun because this
change only extends evaluation tooling. The standalone supplemental workbook was
rendered and its exported cells checked, including exact reconstruction of 13 writer
prompts, evidence packets and completions. The original annotated workbook was not edited.

Raw traces and private review materials remain in ignored local directories:
`src/api-service/.local/eval/writer-multimetric-1108/full/` and
`.local/outputs/writer-multimetric-review-20261108/`.

To reproduce after preparing a fresh isolated synthetic-v0.2 import and a private
model tunnel, run from `src/api-service/`:

```sh
uv run python -m scripts.eval_writer_chain \
  --database-url "$HEALTHOPS_TEST_DATABASE_URL" \
  --import-dir .local/eval/<run>/import \
  --model-url http://127.0.0.1:<tunnel-port> \
  --model-dir .local/reconcile/w14 \
  --multimetric --output .local/eval/<run>/full
```

The database guard requires loopback PostgreSQL and the literal database name
`healthops_test`. Provisioning or starting cloud resources is not part of that command.
