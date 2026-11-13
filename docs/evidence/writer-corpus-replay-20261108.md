# SQL-grounded replay of the historical writer corpus

## What was rerun

All 79 historical questions received fresh Qwen completions after regenerating evidence
with production SQL tools. The original JSONL files and their silver reference answers
were not rewritten. The user's annotations on eleven older preference candidates were
preserved in a versioned local workbook. The workbook also includes the five candidates
from the [separate authenticated full-chain run](writer-full-chain-20261108.md).

This replay is **writer-only, not end-to-end accuracy**. It deliberately bypasses the router,
clarification, tool selection and claim verification. Its `user_request` is the original
question string; unlike a routed API capture, it does not include a router-produced
`user_dialogue` / `confirmed_request` envelope. The evidence packet itself comes from the
real tool result through `build_cards` and `narrative.packet`, not an edited legacy card.
Training against the exact routed dialogue distribution still requires full-chain capture.

| Input source | Questions | Evidence construction |
| --- | ---: | --- |
| Reconstructed synthetic daily histories | 54 | Normal authenticated ingestion into isolated PostgreSQL, then `daily_summary` |
| Manifest-verified synthetic-v0.2 accounts | 25 | User-scoped `reconcile_devices` with the real pinned CPU predictor |
| Total | 79 | 37 distinct evidence packets, with multiple question variants |

The 54 daily histories are **authored boundary fixtures reconstructed from historical
scenarios**, not observations sampled from the generated cohort. They exercise production
storage and aggregation, but do not prove the statistical realism of the histories.
Reconciliation uses existing cohort histories instead of fabricating q05/q50/q95 values.
The old tie and Google-closer labels are not preserved: all paired histories selected in this
run put Apple Health closer to the model estimate. Old questions asserting equal distances
therefore become false-premise challenges, not successful reproductions of a tie condition.
Single-channel and no-reading cases remain separately represented. Do not claim balanced
coverage of both winning channels or genuine ties from this replay.

The user explicitly authorized opening the twenty formerly reserved questions for review.
They are now **exposed review material**, not an independent future holdout. The original
file and split labels remain as historical provenance. This report and run metadata supersede
their former holdout eligibility. Create a new unseen, group-separated evaluation before
using these candidates for model-selection claims.

## Observations and candidate review

| Observed result | Count | Meaning |
| --- | ---: | --- |
| Fresh writer completions | 79 | No missing completion replaced with an invented answer |
| Accepted by basic checks | 74 | JSON, evidence IDs and supported numeric/date checks only |
| Rejected for missing evidence IDs | 4 | Not all supplied cards cited |
| Rejected for unsupported number | 1 | A denial repeated the user's unsupported number; retained as checker regression |
| Historical questions selected for human preference review | 24 | Eleven older IDs with new inputs/outputs, plus thirteen additional IDs |
| Existing full-chain candidates added | 5 | Four cohort-derived and one uploaded boundary-fixture case |
| Combined review rows | 29 | All pending new human review, not approved training data |

Assistant review of the 79 outputs marked 17 semantic-error candidates, 4 citation/wording
candidates, 3 explanation/wording candidates, 8 other observations, 1 checker regression
and 46 unselected cases. These are **silver triage categories**, not independently adjudicated
accuracy scores. Unselected does not mean fully correct. Optional qualifications appended
by the application are separate from the raw writer completion under review.

Recurring problems include inferring actual activity from today's incomplete recording,
extending matching observed values across missing dates, averaging incompatible recording
coverage, confusing unavailable totals with absent records, and accepting a question's
false tie premise despite unequal supplied distances. Some pass every basic check.

The user's earlier feedback requested natural date wording and plain explanations of recording
coverage and overlap. New proposed answers incorporate those preferences. For example,
overlap is explained as records covering the same time, so summing them could count steps
twice. Candidate prose is still an assistant-authored proposal, not gold truth.

## Pair integrity and preserved review

- Original prompts, rejected answers and human decisions remain together as history.
- Rebuilt evidence is paired only with its newly captured rejected answer. Old completions
  are never relabeled as outputs from revised prompts.
- Each new chosen answer passed the existing basic validator against that exact packet.
  Passing does not establish semantic correctness or grant training permission.
- Previous approvals apply to the old version only. Every new review decision starts pending.
- Raw JSON, credentials, observations and the Chinese review workbook remain in ignored local
  directories. No personal account, real health record, Cloud SQL data or secret was published.

## Code and reproduction

[replay_writer_corpus.py](../../src/api-service/scripts/replay_writer_corpus.py) loads the
frozen 79-case inventory only with explicit `--open-holdout`. `daily_observations` in
[audit_writer_evidence.py](../../src/api-service/scripts/audit_writer_evidence.py) reconstructs
valid synthetic records. Each daily case receives a separate synthetic user; ingestion tokens
stay outside traces. SQL tools receive the server-selected UUID, not a model-supplied identity.
`completion_input` allowlists the question, language and freshly constructed packet, excluding
reference answers, labels and rubrics. Raw output and basic-check results are saved separately.

Use a fresh migrated loopback `healthops_test` database containing only the three verified
accounts imported by `healthops.synthetic_import`, and a fresh output directory. Follow the
import prerequisites in the linked full-chain report, then run from `src/api-service`:

```bash
PYTHONPATH=. uv run python -m scripts.replay_writer_corpus \
  --database-url "$HEALTHOPS_TEST_DATABASE_URL" \
  --import-dir .local/eval/writer-replay-import \
  --model-dir .local/reconcile/w14 \
  --model-url "$HEALTHOPS_EVAL_MODEL_URL" \
  --output .local/eval/writer-replay-new --open-holdout
```

This command is not resumable; after interruption preserve the partial output as incomplete
and use a fresh isolated database and output directory. One initial attempt was stopped
after five completions to correct a runner tool name; it was excluded from reported results.
The completed run contains all 79 responses from the corrected runner.

Execution used PostgreSQL 17/pgvector, the already running V100, Qwen3-8B Q4_K_M with digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`, temperature 0,
thinking disabled, context 32768 and output limit 800. The writer prompt was
`grounded-answer-v2`; the CPU predictor was `20261107T231608Z/w14/transformer`.
Model identity, source hashes, corpus hashes, tool queries/results and exact prompts are
captured locally. No model training or production application behavior change occurred.

## Verification boundary

Tests cover explicit holdout retirement, all 54 reconstructed histories passing ingestion
schemas, overlap remaining unusable, reference-label exclusion, and real reconciliation
parameter mappings independent of historical invented quantiles. The capture and preference
regression suites also ran: 105 selected tests passed, including repository-policy tests.
For all 54 daily cases, SQL-derived card values, missingness, bases and warnings matched the
in-memory aggregator controls. This checks persistence/query equivalence, not an independent
implementation of the aggregation mathematics. Spreadsheet verification preserves the old eleven review rows,
checks exported raw inputs/completions against captures, and renders changed views.

This does not replace the full Docker integration suite, data-service/model-training tests,
Cloud Run deployment verification, phone interaction testing, or independent human review.
