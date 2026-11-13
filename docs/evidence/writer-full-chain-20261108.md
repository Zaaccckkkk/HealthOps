# Production-derived synthetic writer evaluation

Later follow-up: the user authorized opening and reviewing the historical 79-question corpus.
See the [SQL-grounded writer replay](writer-corpus-replay-20261108.md) for that separate run
and retirement of the twenty legacy holdout questions. Counts below describe this earlier run.

## Outcome

The new development run uses persisted synthetic records, the authenticated API, real tools,
the trained reconciliation predictor and Qwen3-8B. No evidence cards or model quantiles were
authored for the writer. This replaces the input method used by the
[historical handcrafted writer diagnostic](writer-input-provenance-audit-20261108.md);
it does not rewrite that diagnostic's prompts, answers or eleven unapproved candidates.

| Stage | Original development suite | Separate explicit-date probes |
| --- | ---: | ---: |
| Scenarios / actual conversation turns | 50 / 56 | 8 / 8 |
| Expected user-scoped tool result obtained | 42 | 8 |
| Exact final writer prompt and raw completion captured | 39 | 6 |
| Generated prose accepted by basic checks | 37 | 6 |
| Writer completion rejected by basic checks | 2 | 0 |
| Did not reach final writer | 11 | 2 |
| Silver preference candidates selected | 4 | 1 |

These are trace and workflow counts, **not answer-accuracy scores**. Basic checks do not prove
correct interpretation, and the coding assistant's review is not independent ground truth.
All five candidates remain `pending_independent_human_review`, `training_eligible=false`.
No training occurred. The twenty reserved writer questions received no model inference and
were not changed; static corpus regression checks still validate their schema and hashes.

## Execution boundary

- Three manifest-verified `synthetic-v0.2` cohort accounts, one per device cohort, contributed
  16,276 records. A fourth isolated fixture account received 34 invented boundary records
  through `/v1/observations/batch`, using the normal ingestion contract.
- PostgreSQL 17 + pgvector ran in a new loopback-only Docker database, `healthops_test`.
  No personal database, Cloud SQL instance, production account or real health record was used.
- Requests used `/v1/assistant/ask` with actual bearer authentication via an in-process ASGI
  HTTP client. Router, persistent clarification/resume, LangGraph tools, verification and
  final narration were real. This did **not** exercise Cloud Run ingress or an iPhone client.
- The existing V100-SXM2-16GB VM was restarted; no VM was provisioned. Ollama was reached by
  a localhost-only IAP SSH tunnel. Its two-hour automatic stop policy was verified and retained.
- Qwen3-8B Q4_K_M digest:
  `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`;
  temperature 0, thinking disabled, loaded context 32768, output limit 800.
  Router prompt metadata was `2026-11-08.2`; writer prompt was `grounded-answer-v2`.
- Actual CPU predictor: `20261107T231608Z/w14/transformer`, trained on `synthetic-v0.2`.
  Its artifact checksums and outputs are captured; successful invocation does not establish
  accuracy on real people or calibration of its nominal intervals.
- The original suite had 42 cohort scenarios plus 8 boundary scenarios. Six cohort scenarios
  used an actual clarification and subsequent reply. Eight later probes rewrote the boundary
  questions with explicit ISO dates and a summary intent; they are adaptive diagnostics,
  not replacements for failures or independent held-out results.

## Evidence checks and remaining gaps

[eval_writer_chain.py](../../src/api-service/scripts/eval_writer_chain.py) records every model
call's exact messages, tool schemas, raw completion, parsed calls and token counts. The per-turn
trace also retains the request, response, resolved scope, evidence cards and full tool results.
Run metadata freezes case content, model/predictor identity and production-source hashes.
The executed runners were saved locally and verified against their captured hashes before
later CLI and formatting changes. Credentials are separate from request traces.

For each final response, the runner checks that cards reconstruct from tool results, the
expected scoped tool query matches an actual result, the writer input equals the production
packet of those cards, and the confirmed request matches the resolved scope. The expected
query uses the same production tool: it detects scope/query disagreement, not every possible
aggregation bug. Eight independent controls against the explicitly authored upload fixtures
also passed, covering partial-day values, gaps, mixed bases, both overlap directions,
nonmonotonic values, an empty window and a selected source.

The observed production path does supply `not full-day coverage`, `includes_today`, missing
values, row aggregation bases and source-specific warnings. Mixed bases omit an unsupported
cross-basis mean. Overlapping intervals make only the affected source unusable. Empty windows
have a provider-null card with empty rows, rather than fabricated per-day values. Single-channel
reconciliation withholds model quantiles from the writer and states comparison is unavailable.

Three input-design issues remain important:

1. Two source-filtered empty-history cases lose the selected provider in the writer payload:
   the card's provider is null and the confirmed request does not include it. Do not train
   the writer to guess the missing source.
2. A one-row daily summary includes a zero first-to-last change. Two raw answers interpreted
   this as within-source/intraday stability. Review the summary semantics before mining these
   as writing-only negatives.
3. Reconciliation warning codes can refer to observations or contextual metrics without
   conveying that scope in the compact packet. An answer's reference to an interval crossing
   midnight can be confused with the prediction interval. Enrich warning context before
   treating a clearer explanation as something the writer could infer reliably.

These findings limit the claim: captured inputs are production-derived, but not all inputs
are complete or unambiguous, and synthetic ingestion does not validate live provider adapters.
No retrieval/RAG route or general medical-reference response was exercised in this suite.

## Failure diagnosis and preference review

The eleven original cases without a writer comprise six clarification outcomes, two refusals,
two cases where `state_findings` was emitted as prose instead of a tool call, and one case
with invalid reconciliation claims after the bounded repair attempt. The explicit-date probes
reached the expected query in all eight cases, but two overlap cases still cited an unknown
evidence ID during `state_findings`, including after repair. No invented final prompt was
substituted for these failures.

The four original-suite preference candidates address:

- A single-day maximum incorrectly described as both the last value and the period total.
- Unsupported truncated heart-rate values and omission of a supplied channel.
- Mean-daily differences described without their statistical basis, resembling period totals.
- A resumed response omitting a channel and inferring increased activity from endpoint change.

The supplemental candidate clarifies mixed aggregation bases rather than vaguely asserting
an accuracy problem. Two original raw answers were caught by numeric/citation checks; the
maximum-as-total semantic error passed those checks. A validator pass is therefore insufficient
as a training reward. Other cases remain available for review rather than manufacturing more
negative examples from borderline wording or server-covered caveat omissions.

[build_chain_preferences.py](../../src/api-service/scripts/build_chain_preferences.py) pairs
the **exact captured prompt and raw rejected JSON** with a reviewed chosen JSON answer. It
requires one explicit review per scenario, unchanged frozen cases, matched cards/tool results,
the actual dialogue, a tool-free writer and a supported selected provider. Proposed answers
must pass basic evidence-ID/date/number checks. Neither those checks nor a silver review grant
training approval. Raw completions remain separate from server fallbacks and appended caveats.

Original-suite review dispositions: 4 candidates, 11 upstream-blocked, 2 missing-source-context,
3 input-design review, 1 ambiguous wording review and 29 retained for further review. The latter
is not a claim of 29 independently adjudicated correct answers. Supplemental dispositions:
1 candidate, 2 upstream-blocked and 5 retained. Candidate answers are generally longer; human
review should check concision and avoid teaching that verbosity alone deserves preference.

## Reproduction

Prerequisites: an isolated migrated loopback `healthops_test` database, locally available
manifest-verified `synthetic-v0.2`, a matching three-account import/credential directory,
the pinned serving predictor artifacts, and an authorized reachable Qwen model. No private
run directory, model checkpoint, credential or dataset content is included in Git.

From `src/api-service`, after setting `HEALTHOPS_TEST_DATABASE_URL` to the isolated database:

```bash
PYTHONPATH=. uv run python -c 'import os; from scripts.eval_writer_chain import guard_database; guard_database(os.environ["HEALTHOPS_TEST_DATABASE_URL"])'
export HEALTHOPS_DATABASE_URL="$HEALTHOPS_TEST_DATABASE_URL"
export HEALTHOPS_SYNTHETIC_DATABASE_URL="$HEALTHOPS_TEST_DATABASE_URL"
export HEALTHOPS_ENVIRONMENT=local
export HEALTHOPS_DATA_MODE=synthetic
uv run alembic upgrade head
uv run --extra synthetic python -m healthops.synthetic_import \
  --dataset ../../data/synthetic/synthetic-v0.2 \
  --manifest ../../data/manifests/synthetic-v0.2.yaml \
  --user-id synthetic-00000 --user-id synthetic-00001 --user-id synthetic-00003 \
  --output-dir .local/eval/writer-chain-import --apply
PYTHONPATH=. uv run python scripts/eval_writer_chain.py \
  --import-dir .local/eval/writer-chain-import \
  --model-url "$HEALTHOPS_EVAL_MODEL_URL" --model-dir .local/reconcile/w14 \
  --output .local/eval/writer-chain-new
```

Use a fresh output directory for every run. `--edge-diagnostics` runs only the eight separately
labeled probes. Review the completed traces and author `review.json` with an entry per case;
only supported writing defects get `disposition=candidate`, `quality=fail|partial`,
`evidence_review=approved_silver`, `reason`, `evidence_support` and `chosen_answer`.
Other dispositions still require a reason. Then assemble unapproved candidates:

```bash
PYTHONPATH=. uv run python scripts/build_chain_preferences.py .local/eval/writer-chain-new
```

The builder exports local JSONL and a readable Markdown review. Subsequent production prompt
or card changes require a new capture or the frozen implementation; never rewrite an old
prompt underneath its rejected answer. Reserve independent production-derived scenarios for
a future training comparison; the legacy handcrafted holdout alone cannot measure the full chain.

## Verification

320 selected API, tool, writer, ingestion-contract and repository-policy checks passed across
the initial run and the iOS-contract rerun. Two initial Swift subprocess checks failed because
the sandbox blocked compiler-cache access; all four iOS-contract checks passed with that
access restored. The selected tests include 58 capture/preference/provenance/narrative checks.
New guards exercise exact raw-output preservation, no-writer rejection, changed packet/dialogue
rejection, missing provider context, unreviewed labels and unsupported chosen numbers.

The actual evaluation used isolated PostgreSQL and real model inference as described above.
It does not substitute for the complete Docker integration suite, live Cloud Run/iPhone tests,
data-service tests or model-training tests; those were not run for this evaluation-only change.
Production prompts, routing rules, tools, model weights and app behavior were not changed.
The temporary database container and its anonymous volume were removed after capture. The
SSH tunnel is closed; the existing GPU VM remains under its verified two-hour stop policy.
