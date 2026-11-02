# Natural-language exploratory probes

Observed 2026-10-27: six questions, one frozen paired synthetic account, one run per question.
The model, prompt and tools were unchanged from cohort-baseline-v2; temperature 0, thinking
false, Qwen3:8b Q4_K_M, context 32768, sequential execution. The reference library remained
unavailable, as in the routing-only baseline. No reference tool was called in these six cases.
The evaluation date was fixed at 2026-08-10 in America/New_York, not the wall-clock date.

All six returned `answered` and `verified`. This is **not 100% answer accuracy**. Manual review
found relevance, scope, completeness and language problems despite supported individual claims.
No single expected tool was imposed on ambiguous questions. This is exploratory evidence, not
a representative benchmark or a causal comparison between languages.

| Probe | Observed behavior | Review |
|---|---|---|
| How did I sleep last night? | Queried sleep_duration for the previous date; cited both providers | Useful duration summary, but did not explain the limit of assessing overall sleep quality |
| Chinese equivalent of the same question | Same metric/window and English final template | Meaning recognized, language preference not reflected in the renderer |
| Explicitly request yesterday's sleep duration and resting heart rate | Queried both; resting heart rate returned zero records; cited only sleep | Requested missing metric silently omitted |
| Did I sleep better last night than usual? | Compared two seven-day windows ending on the previous date | Neither establishes a user-approved meaning of usual nor compares last night as requested |
| How have I been doing lately? | Chose past seven days and heart_rate, steps, sleep_duration; emitted 14 findings | No clarification of scope; long list, incomplete provider/date coverage rather than an overview |
| Was today's tiredness caused by poor sleep last night? | Compared yesterday with today; reported insufficient data twice | Does not establish causation, but also fails to explain the causal limitation or usefully address the request |

## Mechanism

`claims.verify_claim` constructs sentences from validated cards with Python string templates;
`render_explanation` joins the sentences only when all submitted claims are valid. It is an
internal deterministic renderer, not another language model. The current check asks whether
the referenced facts are supported; it does not establish that the query plan matches the
user's intent or that all requested aspects were answered.

A natural question does not corrupt database records. It can lead to an inappropriate metric,
wrong comparison window, unannounced scope assumption or omission of missing data. Also, the
existing resting_heart_rate metric must not be described as heart rate during a sleep session:
that relationship would require explicit temporal alignment that this evaluation did not perform.

## Reproduce and evidence

From `src/api-service`, with the frozen v2 set, local API/Ollama and synthetic import available:

```bash
uv run python scripts/probe_natural_questions.py \
  --dataset .local/eval/cohort-baseline-v2 \
  --questions .local/eval/natural-probes-v1.jsonl \
  --output .local/eval/natural-probes-run-v1
```

Use a new output directory for a new run. Input JSONL uses `id`, `source_user_id`, `question`.
The bilingual question list, full synthetic responses and raw model turns remain local. Each
run records its input hash and checks frozen model/artifacts and all baseline golds before and
after execution. The database connection is read-only. Original baseline results are unchanged.

Shared preparation/scoring tests: 18 passed, one existing Starlette/httpx warning. The new probe
script was exercised end to end with six real requests; it does not assign automated pass/fail
labels to ambiguous intents. Lint, format, public-content and Markdown-link checks passed.

## Proposed next experiment, not implemented

Add intent/window/metric coverage review separately from factual verification. Predeclare which
ambiguities require clarification and which allow an explicit default, then test acceptable
alternative plans. An explicit multi-metric request should mention unavailable requested metrics.
Improve presentation through localized, intent-specific summaries or constrained paraphrasing
of verified facts, with separate semantic checks. Do not assume a freer prose generator alone
fixes wrong query plans, incomplete coverage, or causal overinterpretation.
