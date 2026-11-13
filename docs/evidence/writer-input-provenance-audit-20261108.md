# Writer input provenance audit

**Follow-up:** [the full-chain synthetic run](writer-full-chain-20261108.md) now captures
production-derived inputs and new completions. It preserves the historical artifacts below
and separates upstream failures from reviewed writing candidates.

## Finding and training disposition

The existing 50 development prompts use handcrafted cards, not evidence captured from the
synthetic-v0.2 database/tool pipeline. The model calls and recorded completions were real, and
the runner used the production packet/message formatter, but that does not establish upstream
evidence fidelity. **Keep all 11 preference candidates unapproved and ineligible for training.**
Human approval of their prose alone will not resolve this input-provenance gap.

The production interval label really is `sum of recorded intervals (not full-day coverage)`.
It expresses the aggregation's lack of a full-day coverage guarantee. It does not establish
that wear time was measured, that a particular number of hours is missing, or why data is absent.

## Observed audit

[audit_writer_evidence.py](../../src/api-service/scripts/audit_writer_evidence.py) audited all
50 development cases. For 34 daily-table cases it constructed invented in-memory observations
and executed the real `summarize_days` -> `build_cards` -> `narrative.packet` path. This is a
reachability probe, not recovery of an original record history or a synthetic-v0.2 cohort replay.
For 16 reconciliation cases it inspected mandatory production fields only; no predictor was run
and the handcrafted quantiles were not certified. The reserved suite was not loaded or changed.

| Cases | Difference from production construction | Interpretation |
| --- | --- | --- |
| 18 partial-day, sparse, nonmonotonic | Semicolon in the handcrafted statistic versus parentheses in production | The reconstructed packet differs only in that label; the represented values, gaps, dates and derived facts are reachable through the daily aggregator. |
| 6 mixed-basis | Handcrafted `not comparable daily coverage` versus production `mixed statistics; see each row` | A substantive extra hint was given to the evaluated writer. Production supplies per-row bases and omits the cross-basis mean, but does not supply this extra wording. |
| 6 overlap | An all-unusable card has statistic `no data` in production, alongside `quality=unusable` and the overlap warning; the handcrafted card claims an interval-sum statistic | Record conflicts and their source ownership are reproducible, but the label is not faithful. Two cases also have different provider ordering under a single all-provider tool call. |
| 4 all-missing | Production returns a provider-null card, empty rows and `no_records_in_window`; the handcrafted card names HealthKit and enumerates null rows | This is a material structural mismatch for a window with no records. |
| 16 reconciliation | Handcrafted `q50` statistic and absent unconditional `context_defaulted` warning | Production uses `synthetic-trained q50 estimate` and always adds that warning. Quantiles are invented, not outputs observed from the trained reconciliation model. Additional history/sparsity warnings require an actual input history to determine. |

The 11 candidates comprise five label-only daily cases, three mixed-basis cases, two overlap
cases and one reconciliation case. These distinctions matter: the audit does not show that
every historical failure is caused by input mismatch, nor that all evidence was fictitious in
meaning. It does show that exact production-input equivalence was not established.

The original cases, run snapshots, raw model outputs, preference pairs and review workbook are
historical artifacts and were not rewritten. A corrected prompt must receive a new model
completion; never attach an old rejected completion to new evidence or changed evidence IDs.

## Actual production path

1. [daily_summary.py](../../src/api-service/healthops/tools/daily_summary.py):
   `daily_summary_tool` queries authenticated-user-scoped records and passes them, including
   relevant overlap context, into `summarize_days`. The audit runs only the latter computation.
2. [cards.py](../../src/api-service/healthops/assistant/cards.py): `build_cards` builds labels,
   missing/unusable rows and source-specific warnings from actual tool results. `STATISTIC_LABELS`
   supplies the interval coverage wording; `_daily_cards` selects mixed/no-data labels.
3. [reconcile.py](../../src/api-service/healthops/tools/reconcile.py):
   `reconcile_from_summaries` invokes the predictor, computes coverage/attribution and adds warnings.
   Neither the old writer corpus nor this audit establishes observed predictor output provenance.
4. [narrative.py](../../src/api-service/healthops/assistant/narrative.py): `packet` copies card
   statistics and warning codes, retains each row's date/value/quality/basis, and computes permitted
   descriptive facts. It does not forward record counts or each row's individual warning list.
   `includes_today` is a date comparison, not a completeness measurement. `messages` serializes
   this packet into the writer input.
5. [orchestrator.py](../../src/api-service/healthops/assistant/orchestrator.py): `narrate_node`
   builds that packet only after verification is accepted and narration is enabled, within budget.
   It supplies the clarification dialogue context when available, not always just the latest
   question. Reference/availability-only cards are not supported by this writer packet. The old
   50 cases do not test those routing, dialogue, eligibility or fallback decisions.

## Required before preference training

Create a new version from isolated, identified synthetic records. Run the actual authenticated
API flow, preserving resolved scope and dialogue, selected tool arguments, full tool results,
cards, exact writer messages, model/prompt versions and raw completions. Reconciliation cases
must record the pinned predictor artifact and its actual output. Keep unavailable/unusable data
and upstream failures; do not silently supply an ideal card when the tool chain fails.

Check captured writer evidence equals the production packet of the captured cards. Label failures
by layer: routing/query/aggregation/predictor/card/formatting versus final writing. Only cases
with trustworthy evidence belong in a writer preference batch. Have the user review chosen
answers against those inputs, then allocate train/validation by scenario group. The legacy
20-question suite remains frozen as a writer stress suite; it cannot by itself establish an
end-to-end production improvement. A separate production-derived held-out suite is needed.

## Reproduction and limits

From `src/api-service`:

```bash
PYTHONPATH=. uv run python scripts/audit_writer_evidence.py > /tmp/healthops-writer-evidence-audit.json
uv run pytest -q tests/test_writer_evidence_provenance.py tests/test_writer_corpus.py tests/test_writer_preferences.py tests/test_assistant_narrative.py
```

59 tests passed, including six new provenance checks: the real interval label reaches the
serialized writer message; mixed bases omit an unsupported mean and the authored hint; overlapping
records remain unusable only for their own source; an empty history uses the real empty-card shape;
all 50 cases remain explicitly uncertified; and reserved split metadata is rejected by this audit.
No SQL query, Router, live model, cloud deployment, real health record or training was used.
Database, Docker, Swift and training suites were not run for this audit-only change.
