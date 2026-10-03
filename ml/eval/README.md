# Assistant evaluation sets (synthetic)

Two question sets for the single-turn assistant. All questions, users and data are synthetic.

| File | Purpose |
|---|---|
| `assistant_dev.synthetic.jsonl` | Development set: used while writing prompts and the orchestrator. |
| `assistant_heldout2.synthetic.jsonl` | Second held-out batch written after the first became a regression set (includes clarification cases). Run once on 2026-10-02 and read to decide two verification-rule fixes, so it is now a regression set too. |
| `assistant_test.synthetic.jsonl` | Former held-out set. Its outputs were read to decide fixes after the 2026-10-02 run, so it is now a regression set; a new unseen batch is needed to report generalisation again. |
| `synthetic_history.py` | Deterministic history for a dedicated synthetic evaluation user. |
| `build_sets.py` | Single source of truth: regenerates both JSONL files from the server's time semantics and the synthetic history (`expected_data_days`). `tests/test_eval_sets.py` fails on drift. |
| `rubric.md` | Manual grading sheet for answer correctness. |
| `RESULTS.md` | Observed numbers from the latest local-model runs, with configuration and a provisional grading. |

Each line: `id`, `question`, `timezone`, `today`, `expected` with `tool` (or `decline`),
`arguments` (fields that must match exactly; `timezone` is injected by the server and is not
part of the expectation), `expected_data_days` (days with data in each window, computed from
the synthetic history) and `notes` for the grader. Relative phrases in questions are limited to
the windows the server lists (last week, week before last, three weeks ago, past/previous 7 and
30 days, yesterday, today); unsupported phrases are expected to be declined. `today` is fixed so relative phrases resolve reproducibly; the server
accepts it only in evaluation mode (`HEALTHOPS_ASSISTANT_ALLOW_TODAY_OVERRIDE=true`).

Run `uv run python scripts/eval_assistant.py --set dev` (or `test`) against a configured model.
Raw outputs go to `.local/eval/<timestamp>/` and are never committed. Reported metrics:

- tool selection accuracy (expected tool, or decline, was the first executed tool);
- argument semantic correctness, reported twice: on the executed arguments (after server
  adjustments such as dropped provider filters or reordered windows) and on the model's
  original arguments; the count of runs with server adjustments is reported alongside;
- argument validity rate (the tool accepted the call);
- numeric consistency distribution (`consistent` / `unmatched` / `not_applicable`), a
  diagnostic, not fact verification;
- warning propagation rate: when the executed tool returned warnings, the answer contains one
  of the warning's keywords (approximate; the rubric is the final word);
- refusal correctness on out-of-scope questions, and clarification correctness when a question
  names a channel without an interface selection;
- explanation status distribution (`verified` / `unverified` / `not_applicable`) and the share of
  valid claims; an unverified explanation still leaves the cards intact;
- latency: first request separately (cold start), then p50/p95 over the warm remainder. With
  about 40 questions these are small-sample descriptive numbers.

Answer correctness (metric, unit, window, provider, direction of change, caveats) is graded by
hand with `rubric.md`; the script writes a grading sheet with one row per question.

## Reporting

`uv run python ml/eval/summarize_runs.py` prints one Markdown row per run directory under
`.local/eval/` from its `summary.json`. Tables in `RESULTS.md` are copied from that output, not
typed by hand, so the counts can be re-derived from the raw summaries at any time.
