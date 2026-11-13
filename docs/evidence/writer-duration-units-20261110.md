# Writer duration conversions and supplemental evaluation

## Change

The writer checker previously compared displayed numbers with evidence numbers.
Correctly converting seconds into hours and minutes introduced new numbers, so a
valid duration could receive an `unsupported_number` diagnostic.

`checked_duration_text` in
[duration_checks.py](../../src/api-service/healthops/assistant/duration_checks.py)
now checks each explicit duration as a whole quantity against seconds-valued
measurement fields. It supports English and Chinese unit labels with Arabic
numerals. Exact conversions pass; rounded hours/minutes require an approximation
or rounding marker. Incorrect conversions, malformed composite durations and
durations backed only by count/bpm data still fail. Matching a duration does not
whitelist its component digits elsewhere in the answer.

For example, the unit-test fixture `25680 s` permits `7 hours 8 minutes` but not
`7 hours 9 minutes`. Dates and evidence IDs retain their existing checks. This is
an arithmetic diagnostic: it does not independently verify source attribution,
trend language or clinical meaning. The existing one-check/one-editor workflow
is unchanged; the edited answer receives no second content check.

## Preference data

One previously captured Chinese development pair was added to a new local bundle:
25 training pairs and the unchanged seven validation pairs. Its rejected answer
is the exact captured model output; its preferred answer converts recorded sleep
durations to hours/minutes, labels rounding and preserves coverage limitations.
The input, evidence and negative answer were not rewritten. The inclusion was
authorized by the owner and audited by the assistant, not independently scored
by another human reviewer.

The original bundle remains unchanged. The new bundle passes the existing exact
pair/hash/provenance checks. **It is not launch-ready:** 25 rows are not divisible
by the configured global batch size of four. The partial-batch guard remains;
three additional reviewed training pairs are needed with that configuration.
No training job was launched and the previous cloud bundle was not replaced.
Dataset contents and local DVC snapshots remain outside Git.

## Supplemental test baseline

[writer_duration_cases.py](../../src/api-service/eval/writer_duration_cases.py)
defines six additional test-only scenarios: Chinese and English questions for
each of three synthetic accounts excluded from preference training/validation.
The original twenty-scenario holdout was not modified.

Execution used the authenticated ASGI API, an isolated PostgreSQL database with
the original twenty test accounts, and the existing Qwen3-8B model on a V100 VM.
It was not a Cloud Run or phone run. Temperature was zero, thinking disabled,
context 32768 and maximum writer output 800 tokens. The Ollama Q4_K_M digest was
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.

| Observation | Cases |
| --- | ---: |
| Correct summary path with captured, SQL-audited writer evidence | 2 |
| Availability selected instead of the requested duration summary | 2 |
| Request refused upstream | 2 |
| Writer answers honoring the explicit hours/minutes request | 0 of 2 |

Neither successful writer invocation honored the requested units, so no case
met the full supplemental rubric. These are six deliberately targeted probes,
not an estimate of general assistant accuracy. Upstream failures remain in the
full-chain denominator and must not be presented as writer failures.

Two test-only preference pairs preserve the actual writer prompts and outputs,
with assistant-reviewed silver reference answers. They are not training data or
independent human ground truth. Frozen replay inputs are SHA-256 pinned; the
four upstream failures have no fabricated writer input. A sidecar corrects the
run's initially inherited twenty-case suite label without changing raw traces.

To run the supplemental full chain, use `scripts/eval_writer_chain.py
--duration-holdout` with the existing isolated holdout database/import manifest.
To compare a future writer checkpoint, add
`--duration-supplement .local/eval/writer-duration-holdout-v1` to
`scripts/eval_writer_checkpoint.py` from the API service directory. This appends
two inputs to the original eleven writer replays: thirteen writer inputs from
twenty-six full-chain scenarios. The thirteen upstream failures still require
separate full-chain evaluation. Mechanical validity is not a semantic pass.

## Verification and deployment boundary

- Duration/narrative, checkpoint replay, case definitions and chain regressions:
  94 tests passed after the final implementation changes.
- Broader selected API regression run: 363 passed and two Swift subprocesses
  initially failed due to sandbox cache access. The four-test iOS contract file
  subsequently passed with the required filesystem access (365 unique checks).
- Writer DPO preparation contracts: 25 passed; new bundle verification passed.
- Repository policy tests: 43 passed; public-content, formatting and changed
  document link checks passed.
- Original eleven and supplemental two frozen writer inputs loaded successfully.
- No personal health records were used. The checker source was locally tested;
  this change was not deployed to Cloud Run or installed on the phone.

Full Docker integration, data-service suites and training on GPUs were not run
for this bounded checker/data update.

## Follow-up: owner-approved tolerance and multi-metric budget

The owner subsequently selected an inclusive **120-second absolute tolerance**
for hour/minute conversions. This supersedes the stricter rounding-marker rule
above. The tolerance does not apply to seconds-only measurements, missing data,
zero/nonzero substitutions or sign changes. An approximation marker does not
permit errors larger than the two-minute conversion tolerance.

The previous default of four tool calls could not finish five metric queries
plus `state_findings`. Defaults are now twelve tool calls and fourteen model
turns, allowing up to eight confirmed metrics, finalization, bounded correction
and writer/editor turns. Explicit lower configuration overrides still apply;
wall-clock, context, evidence size and correction limits remain unchanged.
The synthetic five-metric regression verifies all five missing-data results,
finalization and a writer call. Existing exhaustion tests still reject loops.

## Multi-metric request budgets

The phone exposed two distinct application limits: four tool calls and 24,000
serialized characters. Defaults now allow 12 tool calls, 14 model turns and
48,000 Unicode characters. The existing wall-clock deadline and evidence byte
limit remain enforced. `assistant/context.py` shares only identical definitions
and repeated hints across tool messages; changed definitions, observations,
zeros, dates, providers, warnings and evidence references remain present. It
works on a request copy and does not mutate stored evidence or cards. Unicode
characters are counted directly rather than charging six characters per JSON
ASCII escape. This is still a character proxy, not a tokenizer guarantee.

Targeted API verification on 2026-11-10: 157 tests passed across duration,
context, scope/repair, assistant, narrative and limit modules. The five-metric
scripted test checks five queries plus final findings under default limits.
The compaction test checks that repeated definitions shrink while changed
semantics and every observation/warning remain. These unit results do not prove
real-model accuracy or imply that the phone service has been redeployed.
