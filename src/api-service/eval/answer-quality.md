# Experimental answer-writing evaluation

This suite isolates the final writer from Router accuracy and tool calling. It uses ten invented
cases, no database, no real accounts and no copied health screenshots. It is a development set,
not a held-out estimate of real-world accuracy or a training dataset by default.

From `src/api-service`:

```bash
PYTHONPATH=. uv run python eval/build_answer_quality_set.py
PYTHONPATH=. uv run python scripts/eval_answer_quality.py --model-url http://127.0.0.1:11434
PYTHONPATH=. uv run python eval/build_answer_quality_additional.py
PYTHONPATH=. uv run python scripts/eval_answer_quality.py --model-url http://127.0.0.1:11434 --suite additional
```

Use an already authorized model endpoint. Raw output stays under `.local/eval/answer-quality-*`.
Model name, digest, quantization, prompt version and timing are recorded. Basic-check acceptance
is not answer correctness; the manual rubric deliberately starts as `not_reviewed`.

For each case separately review:

- Does the first sentence answer the actual question rather than repeat table availability?
- Are all claimed values, dates, units, sources and directions supported?
- Does the answer preserve missing/partial-day limitations without inventing causes?
- Does a device comparison distinguish model closeness from real accuracy?
- Is the language appropriate and the explanation useful and concise?

Report protocol acceptance, human-reviewed quality and fallback rate separately. A failed writer
can still return a valid deterministic answer. An accepted writer can still swap source attribution
or make an unsupported interpretation: regression tests explicitly demonstrate this limitation.
Do not present a higher tool-call success rate as proof of narrative quality, or vice versa.

The additional suite has twelve invented cases, frozen before the first v2 run: reversed tie,
asymmetric device distances, single/no readings, Chinese partial-day interpretation, historical
decline, localized dates, mixed statistical bases, sparse constant readings, and reversed warning
ownership. It was authored by the implementer and is not an independent held-out benchmark.
Do not alter cases or rubrics after viewing failures to increase the reported score.
The runner now records prompt/completion token counts when provided by the model server.

Observed v2 results and remaining failures: [writer v2 retest](../../../docs/evidence/answer-writing-v2-retest.md).

For reviewed-label preparation and train/test separation, see the
[writer reference corpus](writer-corpus/README.md): nine known-failure regressions plus seventy
new prompts split into fifty development and twenty reserved prompts. All target answers are
unapproved assistant-authored silver references. No SFT or new model evaluation is implied by
their creation, and labels are excluded from model input.
