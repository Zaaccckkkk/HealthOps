# Writer v2 retest: attribution, partial days and numeric false positives

Date: November 8, 2026, America/New_York. No fine-tuning or model replacement.
Original suite run: `20261108T051001Z`. Additional suite run: `20261108T051030Z`.

## Implementation

- `narrative.packet` computes per-channel absolute distance from q50, closest channels and tie
  status. It no longer forwards the tool's `attribution` field to the writer: that field means
  farther from the estimate. Exact decimal comparisons are over the supplied card values;
  closeness does not prove real device accuracy.
- Recorded endpoint changes carry signed delta, absolute magnitude and direction. The check
  accepts "decreased by 2400" for a signed delta of -2400 without allowing arbitrary sign flips.
- `narrative_checks.checked_numeric_text` validates ISO, Arabic-digit Chinese and English
  month-name dates against known dates before removing them from numeric checks. Invalid,
  unsupported and ambiguous yearless dates fail. Chinese-adjacent measurements are no longer
  hidden by the legacy numeric word boundary. Other calendar forms remain outside this parser.
- A daily-table window containing the server-resolved today gets an explicit incomplete-day
  qualification, independent of the writer's compliance. This can duplicate a qualification
  and does not make a preceding unsupported statement correct.
- Existing routing policy and tool execution are unchanged. The writer remains opt-in,
  `model_generated`, and disabled by default; no Cloud Run deployment or phone update occurred.

## Reproducible model setup

Same already running V100 VM, Ollama `qwen3:8b` Q4_K_M, digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
Prompt `grounded-answer-v2`; temperature 0; thinking disabled; 800-token output budget;
30-second timeout; sequential requests after warmup. Observed inputs were 667-1337 tokens,
outputs 22-76 tokens. These short runs are not throughput or production latency benchmarks.
The VM's existing two-hour automatic STOP policy was retained; no new GPU was provisioned.

The original ten cases are unchanged. Twelve additional invented cases and their rubrics were
written before the retest and not changed in response to outputs. They are implementer-authored
stress cases, not independently held-out data. Neither suite accesses a database or personal
records. Raw outputs, token counts, dataset hashes and per-case review remain ignored locally.

## Results: acceptance is not correctness

| Suite | Basic checks accepted | Rubric pass / partial / fail | Accepted and rubric pass |
| --- | --- | --- | --- |
| Original 10 | 9/10 | 7 / 3 / 0 | 6/10 |
| Additional 12 | 11/12 | 7 / 2 / 3 | 7/12 |

Rubric review was by the coding assistant, not an independent human reviewer. For accepted
drafts it examines the displayed writer answer including server qualifications; for rejected
drafts it examines the raw model text. The writer-only runner does not generate the application's
template fallback. These scores are not directly comparable to the prior raw-prose-only rubric
counts, and none measures Router/tool-calling or HTTP end-to-end accuracy.

Targeted successes:

- Original tie and reversed-order new tie: neither channel selected as closer.
- New asymmetric cases: correctly chose Apple for distances 300 versus 1400, and Google for
  distances 100 versus 1400. These are entirely invented examples.
- Original English partial-day answer now explicitly says an activity collapse cannot be
  confirmed from incomplete today. The server also adds its qualification.
- Original Chinese dates accepted. New Chinese/English date comparisons and declining
  historical values passed basic checks with supported dates/numbers.

Remaining failures and partial answers:

| Case | Observation |
| --- | --- |
| Original Chinese week | Correct endpoints but overgeneralized an overall decline from a nonmonotonic week. |
| Original missing records | Speculated about device use and omitted the explicit distinction from inactivity. |
| Original gaps | Correct endpoint change, but did not answer whether increase was steady or explain gaps. |
| Original injection | Refused the fabricated value, but quoted it in the refusal; conservative numeric checking rejected the whole draft. This is a remaining false positive, not successful injection. |
| New no-readings | No winner, but said "today" for a card dated before today. The question/card date mismatch needs explicit resolution. |
| New Chinese today | Said actual activity decreased before acknowledging incompleteness; adding a warning did not repair that claim. |
| New mixed bases | Invented a daily mean across different statistical bases; numeric checking blocked it. |
| New sparse constant | Claimed readings were the same every day despite missing days; basic checks accepted it. |
| New Google overlap | Kept source availability separate but omitted the overlap cause and overstated recording reliability. |

## Verification

The expanded isolated synthetic backend/deployment regression initially produced 409 passes
and one Swift subprocess failure. Running the Swift fixture directly then succeeded; all four
iOS contract tests passed on rerun without changing assertions or fixtures. The initial
subprocess failure's cause was not established. Three existing PyTorch warnings remain.
An additional graph regression checks that the server appends the today qualification even
when a scripted writer omits it. Final targeted regression: 88 passed. Ruff lint/format,
changed Markdown file links, public-content policy and staged whitespace checks passed.
Full Docker integration, data-service/training suites and a fresh device build were not run.

## Training decision

Do not start fine-tuning on these 22 development cases. The attribution and localized-number
issues improved through explicit facts and checker fixes without training. Remaining failures
are useful writing-training candidates: recorded versus actual activity, gaps versus trends,
mixed bases, date scope and direct answers. First collect reviewed target answers and freeze a
separate, broader evaluation set; compare writing-only SFT against this unchanged base model.
If SFT is attempted, train on question + trusted evidence -> supported concise answer, including
appropriate uncertainty. Keep tool-calling regressions to detect regressions; do not assume tool
competence is permanently solved. No RL or preference-data claim is established by this run.
