# Writing references and evaluation splits

**Status update, 2026-11-08:** the user authorized a
[79-question SQL-grounded replay and preference review](../../../../docs/evidence/writer-corpus-replay-20261108.md).
The twenty formerly reserved questions were opened and used for candidate screening.
They are no longer an independent holdout. The JSONL files and manifest remain frozen as
historical artifacts; their `reserved_test` labels describe the original split, not current
eligibility. Use a new unseen evaluation for future training/model-selection claims.

The [merged human-review update](../../../../docs/evidence/writer-preference-review-merge-20261108.md)
applies saved feedback to 23 production-input candidates and retains 13 initially unreviewed
candidates separately. The returned review has 29 wording approvals; seven supplied replacements
have now been applied and synchronized with local draft outputs, pending confirmation. Five
arithmetic-answer pairs still conflict with the captured writer's no-new-statistics instruction.
No approved training export exists yet. A [new-account 20-scenario test](../writer-holdout-v1/README.md)
has now completed its [first baseline](../../../../docs/evidence/writer-holdout-baseline-20261108.md):
5/20 strict full-chain passes and 6/11 raw writer semantic passes on valid inputs, using assistant
review rather than independently reviewed gold. Its captured inputs stay local and excluded from
training; the test is not a source of preference candidates. A local
24-train/7-development-validation proposal groups related cases; it is not an independent
validation benchmark. The five blocked candidates remain excluded.

These are **assistant-authored silver references, not gold ground truth**. Every record is
synthetic, has `review_status=pending_human_review`, and has `training_eligible=false`.
No model evaluation or training was performed when this corpus was created.

**Input provenance limitation:** cards were authored directly, not captured from the cohort
database and tools. The [production input audit](../../../../docs/evidence/writer-input-provenance-audit-20261108.md)
found material mismatches as well as minor label differences. Preserve this frozen stress suite,
but capture a separately versioned production-derived suite before using its failures for
training. Human review of prose does not certify the upstream evidence.

| File | Prompts | Purpose |
| --- | ---: | --- |
| [known_failures.synthetic.jsonl](known_failures.synthetic.jsonl) | 9 | Eight writing candidates and one checker-only regression from observed v2 failures/partial answers. Previously inspected, never independent test data. |
| [development.synthetic.jsonl](development.synthetic.jsonl) | 50 | Development evaluation and possible future training-candidate mining after review. |
| [reserved_test.synthetic.jsonl](reserved_test.synthetic.jsonl) | 20 | Former reserved evaluation, now exposed for review. No longer eligible as an independent final test. |

The original split design below is retained for provenance. The 70 prompts represent
**35 scenario groups**, with two paraphrases per group. Development
has 25 groups and reserved evaluation has 10. Group IDs, synthetic cohorts, dates and question
strings do not cross the split. All ten failure families appear on both sides, but templates
and label authorship are shared. This is a reserved development-project test set, not an
independently authored or statistically independent benchmark. The new cases are English only;
the known-failure file preserves two Chinese cases. They do not evaluate Router or tool calling.

## What an ideal answer means

Each record includes:

- `question`, `today`, `cards`: the model's task and supplied evidence.
- `reference_answer`: one proposed concise, supported answer; other wording can also be correct.
- `required_facts`, `forbidden_claims`, `manual_rubric` where present: semantic review criteria.
- `label_provenance`, `review_status`, `training_eligible`: label origin and review state.
- `scenario_group`, `synthetic_cohort`, `split` for new cases: leakage-control metadata.
- `observed_run`, `observed_failure`, `disposition` for old cases: why the sample was selected.

Arithmetic, explicit dates, missingness, units and source ownership can be checked against the
fixture. A fluent paragraph is not automatically ground truth, even when a stronger model wrote
it. Current automated tests check schema, basic numeric/date consistency, split hygiene and
input isolation; they do not certify medical truth or all semantic assertions.

Example: if today's step recording is lower, a reference should explain that today's data may
be incomplete and cannot establish an actual activity drop. The required fact is that limitation,
not an exact sentence. Do not grade by exact string matching or treat any particular style as
the only correct answer. A warning appended after a contradictory assertion is still defective.

## Review rubric

Review the answer independently against the cards, then compare with the proposed reference:

1. **Factual fidelity:** correct values, dates, sources, metric and statistical basis. Unsupported
   numbers, device winners or claims about actual health/activity are critical errors.
2. **Question coverage:** directly addresses the requested comparison/analysis, not merely
   availability or an endpoint difference when the question asks about a trend.
3. **Uncertainty:** preserves gaps, partial days, mixed bases and experimental-model limits.
4. **Language and usability:** concise, understandable, appropriate language; avoids unnecessary
   technical warnings in the body without omitting limitations that affect the answer.

Record pass/partial/fail with a reason and evidence pointer. Numeric/date tests are necessary
checks, not substitutes for this review. Prefer independent human review for final labels and
adjudicate disagreements; an LLM judge can assist, but cannot certify its own reference.

## Review approval and training size

Human review should check evidence and reasoning, not just endorse the author's wording.
A project-approved reference needs a reviewer, evidence pointers, a decision and rationale.
Check arithmetic against the fixture or deterministic tools and adjudicate ambiguous claims.
Approval is an operational evaluation label, not proof of clinical truth; several answers can
satisfy the same rubric. All references in this version remain unapproved silver labels.

Development examples are not automatically training examples. There is no fixed rule requiring
training data to outnumber evaluation data by a particular ratio. Diversity, reliable labels
and split independence matter more than duplicating prompts. Before training, allocate a
validation subset **by scenario group**, separately from the training subset. Account/history
overlap and shared evidence must also remain within one split. Use validation for
checkpoint/hyperparameter selection. The original 20 reserved prompts have since been exposed
for candidate mining: create and freeze a new independent test before training. The corpus
currently has no separate validation split or approved training export.

## Proposed preference-training experiment

DPO is a reasonable candidate for improving directness, uncertainty and evidence-grounded
writing in an instruction-tuned model. It is not established as better than SFT for this corpus.
SFT learns conditional behavior from reviewed targets; it does not necessarily mean memorizing
one author's style. DPO also optimizes the likelihood of specific answer strings, contrasting
preferred and rejected answers. It can still learn unwanted style or verbosity biases.

Standard DPO is offline preference optimization, rather than an online PPO/GRPO rollout loop.
It avoids fitting a separate reward model and usually compares against a fixed reference
policy. That **reference model** is distinct from this corpus's written `reference_answer`.
See the [original DPO paper](https://arxiv.org/abs/2305.18290) and the
[TRL DPO dataset documentation](https://huggingface.co/docs/trl/dpo_trainer).

A proposed pair has the same complete prompt (system instructions, question, evidence, date
and output protocol), a `chosen` answer, and a `rejected` answer. Both answers must address the
same evidence. For example, with matching values on recorded days and gaps on other days:

- Chosen: "The recorded days match, but missing dates prevent a conclusion about every day."
- Rejected: "Your steps were the same every day."
- Preference reason: the rejected answer converts missing observations into a claim of stability.

This example is illustrative, not an approved training pair. Actual exports must preserve the
production JSON format, including `answer` and `evidence_ids`. A stronger model's rewrite is
only a candidate chosen answer: independently review it and the observed model answer. Exclude
ambiguous preferences, two bad answers, tool-data bugs and checker-only failures. Prefer focused
corrections and control length/style differences when possible so factual quality, not length,
drives the preference. Keep successful behavior represented as well.

First evaluate the development pool and collect reviewed pairs. If training is then authorized,
compare the frozen base model with DPO; a small SFT baseline can test whether the extra preference
machinery helps. Select settings on development validation only. Measure semantic answer quality
and tool-calling regressions, because updating the shared model can affect both. Nine known
failures alone do not establish enough preference coverage or predict a training gain. Local
draft preference files support review, but are not approved training exports. No DPO training
was performed by this review workflow.

## How to use failures for SFT

1. Freeze a new group-based split and a model/prompt version before training. The historical
   reserved file in this directory is now development material, not an independent test.
2. Run only the development suite while iterating. Inspect poor responses and their failure
   causes; exclude tool-data bugs, ambiguous target labels and checker-only false positives.
3. Review and correct a candidate reference. Keep a separate approval record including reviewer,
   evidence checks and rationale. Only then create a versioned training export from approved
   development/known-failure cases. No training exporter is supplied in this increment.
4. For writing SFT, the input is question/context + trusted evidence; the target is the corrected
   answer. **The failed model answer is not the target.** Keep some successful examples too, so
   failure mining does not teach only refusals or uncertainty. Respect the production JSON
   protocol when eventually formatting targets, including evidence IDs.
5. Compare base and trained writers on exactly the same reserved set after selecting the model.
   Do not tune on its errors and continue calling it untouched. If those errors become training
   examples, retire this test version and create another independently reviewed holdout.

The quoted-number case is `checker_regression_only`: the model rejected an injected fabrication
but quoted the unsupported number, which triggered the conservative validator. Do not mislabel
that as successful injection or automatically train the writer to fix a checker limitation.

## Commands and boundaries

From `src/api-service`:

```bash
PYTHONPATH=. uv run python eval/build_writer_corpus.py
uv run pytest -q tests/test_writer_corpus.py
# Later, against an authorized model endpoint:
PYTHONPATH=. uv run python scripts/eval_answer_quality.py --model-url http://127.0.0.1:11434 --suite development
# Historical reserved-suite replay; this file is now exposed development material:
PYTHONPATH=. uv run python scripts/eval_answer_quality.py --model-url http://127.0.0.1:11434 --suite reserved --include-reserved
```

Version 2 appends 20 development prompts in ten new scenario groups; the original 30 development
prompts and 20 reserved prompts remain byte-identical. A hardcoded reserved-file hash also stops
the generator from silently changing the holdout. Reconstruction is deterministic. [manifest.json](manifest.json) pins the two new JSONL files by
SHA-256; Git records the freeze/version history. Version the corpus if its content changes.
The generator does not rewrite the separately authored known-failure references. The evaluator
allowlists question, cards, today and language: references, rubrics and review metadata are never
sent to the model. It requires explicit reserved-suite selection; it does not run it by default.
Raw outputs remain in ignored `.local/eval/`. No real records, database, cloud provisioning or
training are needed to validate this corpus.

Expansion verification (November 8, 2026): 41 corpus/writer regression tests passed, together
with Ruff lint/format, Markdown file links and public-content checks. No inference was run on
the expanded 70 prompts, no human labels were approved, and no new accuracy result is available.
Database, Docker, Swift and training suites were not run for this dataset-only increment.

## Development run and preference review

The 50-prompt development run and 11 unapproved preference candidates are documented in
[the development evaluation report](../../../../docs/evidence/writer-development-preferences-20261108.md).
The reserved set was not run in that initial development experiment; the later 79-question
replay did open it, as recorded in the current status above. Candidate contents and per-case
reviews stay local. Saved human feedback has now been applied to revised drafts, but final
confirmation and training eligibility remain separate. No training was started.
