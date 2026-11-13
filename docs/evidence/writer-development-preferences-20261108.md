# Writer development evaluation and preference candidates

**Subsequent provenance audit:** [the input audit](writer-input-provenance-audit-20261108.md)
found production-card mismatches, including extra mixed-basis guidance and invented model
quantiles. These results remain a handcrafted-input writer diagnostic. All 11 candidates must
remain ineligible until production-derived inputs and new completions are captured and reviewed;
prose approval alone is insufficient. Historical inputs and outputs remain unchanged.

## Scope and setup

Run `answer-quality-20261108T055242Z` evaluated all 50 development prompts (25 scenario
groups, two paraphrases each). The 20 reserved prompts were not run or modified. This is a
writer-only synthetic evaluation: inputs are questions plus prepared evidence, without a
database, Router, clarification dialogue, tool execution or personal records. Some questions
explicitly cue the limitation being tested. Shared templates and authoring limit independence;
these results are not a production accuracy estimate or directly comparable to other suites.

The existing V100-SXM2-16GB VM was running. A localhost-only SSH tunnel reached its internally
bound Ollama endpoint; no new VM was provisioned, started or reconfigured. The tunnel was closed
after the run; the VM and its existing stop policy were left unchanged.

- Model: Ollama `qwen3:8b`, Q4_K_M, digest
  `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
- Prompt: `grounded-answer-v2`; temperature 0; thinking disabled; output limit 800 tokens;
  timeout 30 seconds; warmup followed by sequential requests, no retries.
- Loaded context: 32768; advertised model maximum: 40960.
- Observed prompt/output ranges: 672-1126 / 20-72 tokens. Summed request time 32.57 seconds,
  median 0.62 seconds. These warmed, short writer requests are not full-app latency benchmarks.
- Corpus hash: `8046b823873725f7a569e3ef0fdb387739face4a268d41fb6f8d77f7b12fd620`.
- Reserved corpus remains `58cbe48d6203019175049d63ec10116d0fe4e776aed76811896ce21ed27bf98b`.

## Observed results

| Measurement | Result |
| --- | --- |
| Completed model responses | 50/50 |
| Basic protocol/citation/date/number acceptance | 46/50 (92%) |
| Raw prose rubric: pass / partial / fail | 28 / 15 / 7 |
| Accepted answer after server qualifications: pass / partial / fail | 36 / 4 / 6 |
| Blocked by checker, no writer answer delivered | 4 |

Semantic review was performed by the coding assistant against each question, evidence packet
and existing rubric; it is **not independent human adjudication**. All 50 reviews have reasons
and evidence pointers. Raw prose is scored separately from the JSON protocol. The raw rubric
requires the experimental-accuracy limitation for comparison cases: ten otherwise correct
comparison drafts omitted that standalone limitation and are partial. The server adds it,
which makes those ten qualified answers pass; that does not mean the model wrote the warning.
Two raw-prose passes were blocked for incomplete evidence-ID coverage, explaining why qualified
passes equal 28 + 10 - 2 = 36. Four blocked outputs have no delivered-writer score. The evaluator
does not render the application's template fallback, so this is not an end-to-end success rate.

Key failures and boundary cases:

- Partial today: one answer asserted activity did not crash; another asserted lower actual
  activity. Incomplete recordings establish neither conclusion, even after a warning is added.
- Sparse history: two answers generalized matching observed values to every day despite gaps.
- Mixed statistical bases: two answers gave daily averages across daily totals and interval
  sums. The proposed numbers also occurred as individual row values, so numeric membership
  checks accepted them. Numeric presence does not validate statistical meaning.
- Overlap: one answer explained no usable total as absent recordings instead of overlapping
  records. Another only restated `unusable` metadata and omitted the cause.
- Four overlap drafts omitted an evidence ID required by the current exact-coverage protocol.
  Two of these had correct, focused prose; those two are excluded as writing negatives. No
  checker policy was relaxed for this evaluation.
- Other partials: a correct conclusion supported only by a decrease rather than the relevant
  increase; a generic definition of a mean instead of an answer; a point estimate described
  as an interval; and borderline activity-level wording from partial recordings.

## Preference preparation

The 50 reviewed cases yield:

| Disposition | Count | Treatment |
| --- | ---: | --- |
| Preference candidate | 11, from 10 scenario groups | Seven clear failures and four actionable partials; minimally corrected chosen answer versus exact raw model completion. |
| Retain successful behavior | 26 | Do not manufacture rejected answers from adequate responses. |
| Exclude from this preference batch | 13 | Ten server-covered caveat omissions, two prose-correct protocol rejections, one borderline wording case. |

All candidates remain `coding_assistant_reviewed_silver`, `pending_independent_human_review`,
`training_eligible=false`. Seven chosen answers are longer than their rejected counterpart;
review for length/style confounding before training. This small, error-mined batch does not
represent all successful behavior and is insufficient evidence of training coverage or benefit.

The runner records the exact input messages, evidence packet, original case snapshot and corpus
hash. [build_writer_preferences.py](../../src/api-service/scripts/build_writer_preferences.py)
checks the run is development-only, verifies the snapshot hash and input/prompt identity, requires
exactly one explicit review per case, and validates proposed chosen answers with basic checks.
The rejected completion is the original JSON string, **not** the server-qualified answer.
Those checks establish provenance and consistency, not semantic truth. Candidate data, raw
outputs and review files remain in ignored `.local/eval/`; no training dataset is committed.

Reproduce the candidate assembly, from `src/api-service`, after reviewing the private run:

```bash
PYTHONPATH=. uv run python scripts/build_writer_preferences.py .local/eval/answer-quality-20261108T055242Z
uv run pytest -q tests/test_writer_preferences.py tests/test_writer_corpus.py tests/test_assistant_narrative.py
```

The run-specific private directory is not included in a clone. Tests use synthetic fixtures
and do not require it. The builder deliberately rejects inputs after a prompt implementation
change: do not pair a newly reconstructed prompt with an old completion; retain the frozen
implementation or version the preparation workflow explicitly.

## Training decision

DPO remains a candidate, not a measured improvement. It fits reviewed `prompt/chosen/rejected`
data and does not require an online sampling loop during training. See
[TRL DPO documentation](https://huggingface.co/docs/trl/dpo_trainer).

GRPO can instead generate groups of completions and score them with reward functions. See
[TRL GRPO documentation](https://huggingface.co/docs/trl/grpo_trainer). It is not the next choice
here: the observed false acceptances show that our current numeric/format checks are not a
sufficient reward for answer correctness. Optimizing them risks rewarding unsupported averages
or generic refusals. This is a project-specific recommendation, not a universal ranking.

Next: independently review these candidates, broaden development evidence/question diversity,
separate training and validation by scenario group, and compare the frozen writer with a small
DPO experiment (optionally a matched SFT baseline). Keep the reserved set for final comparison.
The planned tuned writer should have a separate model selection from the unchanged Router/tool
model; that serving split and any training are not implemented by this increment. End-to-end
regressions remain necessary to verify model selection, evidence flow and final-answer behavior.

## Verification

53 targeted preference/corpus/writer tests passed. They check untouched holdout hashes, split
and prompt isolation, raw-response provenance, rejection of missing/duplicate/unknown reviews,
rejection of unavailable inference as a negative, and chosen-answer basic consistency. Ruff
lint/format, changed Markdown links, public-content policy and staged diff checks passed.
No training, Cloud Run deployment, production data access or reserved inference occurred.
Database, Docker, Swift and model-training suites were not run for this evaluation-only change.
