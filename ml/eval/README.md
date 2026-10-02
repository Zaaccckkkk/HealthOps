# Assistant evaluation sets (synthetic)

Two question sets for the single-turn assistant. All questions, users and data are synthetic.

| File | Purpose |
|---|---|
| `assistant_dev.synthetic.jsonl` | Development set: used while writing prompts and the orchestrator. |
| `assistant_test.synthetic.jsonl` | Held-out set: run only to report results, never used to tune. |
| `synthetic_history.py` | Deterministic history for a dedicated synthetic evaluation user. |
| `build_sets.py` | Single source of truth: regenerates both JSONL files from the server's time semantics and the synthetic history (`expected_data_days`). `tests/test_eval_sets.py` fails on drift. |
| `rubric.md` | Manual grading sheet for answer correctness. |

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
- argument semantic correctness (every expected argument field matches exactly);
- argument validity rate (the tool accepted the call);
- numeric consistency distribution (`consistent` / `unmatched` / `not_applicable`), a
  diagnostic, not fact verification;
- warning propagation rate: when the executed tool returned warnings, the answer contains one
  of the warning's keywords (approximate; the rubric is the final word);
- refusal correctness on out-of-scope questions;
- latency: first request separately (cold start), then p50/p95 over the warm remainder. With
  about 40 questions these are small-sample descriptive numbers.

Answer correctness (metric, unit, window, provider, direction of change, caveats) is graded by
hand with `rubric.md`; the script writes a grading sheet with one row per question.
