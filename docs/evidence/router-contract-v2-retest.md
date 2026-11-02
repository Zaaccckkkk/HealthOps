# Router v2 live retest: October 31, 2026

## Outcome

The frozen Cloud Run diagnostic completed all **63 scenarios / 87 turns** after restoring
the original model dependencies. **35/63 scenarios passed (55.6%)**, compared with 3/63
in the previous Router run. The 60 end-to-end scenarios passed **32/60 (53.3%)**;
the other three scenarios score reference routing only and passed 3/3.

Observed routing output matched the expected route on **84/87 turns (96.6%)**.
There were no `model_error` responses and no blocked continuation turns in the completed run.
This is a development diagnostic on an already-inspected synthetic set, not held-out accuracy,
a causal estimate of prompt improvement, or evidence that the system is ready for production.

No application code, prompt, question, gold, or grading rule was changed during this retest.
Normal API traffic was not promoted to the evaluated revision.

## Reproducibility

| Item | Value |
| --- | --- |
| Runner checkout | `475e6de430fa3dcfd595c16169d3d102812314f2` |
| Evaluation revision | `healthops-ms3-api-router-v2-eval-1031` |
| API image digest | `sha256:7577cf42554281c0a0199711d4ff3460fbde58fa0b135be7768f3066f21766df` |
| Router version | `2026-10-31.2` |
| Execution prompt version | `2026-10-30.2` |
| Model | Qwen3-8B, Ollama `0.35.1`, thinking disabled, temperature 0 |
| Qwen digest | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` |
| Embedding digest | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` |
| Hardware | One temporary NVIDIA L4, `g2-standard-4`, `us-central1-c` |
| Driver | `580.178.04` |
| Loaded Qwen context | 32,768 tokens; GPU residency observed through Ollama `/api/ps` |
| Question SHA-256 | `1c9a41cfbf3775d0cfc4e4e7f30ad857aabe3c296336792117da0b56480d7110` |
| Cohort lock SHA-256 | `a1249f63e2c5d6ff7c61287214ebab624e1340f42d5e8f3d963c9174b09e56b4` |
| Local Router prompt SHA-256 | `66c5f18eae892cc7d481bd3cfd9f9281d4856f087c304282a9bc0ca176eea502` |
| Local execution prompt SHA-256 | `c8bb11e286dbf8028a74711de22759b12591cad7173b40524607ee1b23033baa` |
| HTTP runner SHA-256 | `70cc84adb6eda960826eb99271a47cf9562579669b0c1c8a3a0fe9bf05b2ceb1` |
| Completed local run ID | `cloud-router-v2-complete-20261031b` |

The image was built before the later merge; its build provenance is recorded in
[the Router change report](router-contract-v2.md). The checkout identifies the evaluation
runner, not a new image build. Both model digests and the frozen question/cohort hashes were
checked against the previous run. Raw responses, credentials and operation logs remain local-only.

The HTTP runner verified exactly the three frozen synthetic identities, schema revision 0005,
per-account HTTP identity, frozen gold, and a tagged revision receiving zero normal traffic.
No health observations were imported and no schema migration was applied. Clarification tests
use the existing synthetic conversation store and therefore create/update evaluation conversations.

## Recovery and interrupted run

The existing L4 in `us-east4-c` remained unavailable. A model-disk snapshot remained uploading
past the bounded recovery wait. Recovery instead used the original clean GPU base image.
The first temporary L4 request in `us-central1-a` failed for capacity; `us-central1-c` succeeded.
The sequential recovery script then stopped allocating. No T4 was needed.

Five direct Router smoke cases produced the expected classifications. Four HTTP smoke
scenarios passed 3/4, including a clarification continuation and a multi-metric response.

The first full attempt stopped at its 21st scenario: the clean VM had Qwen but lacked
`nomic-embed-text`. Reference execution raised an embedding HTTP 404 and the API returned
HTTP 500. The runner stopped rather than silently retrying a possibly consumed conversation.
The missing embedding model was restored and matched the original digest; a reference smoke
case then passed. All 63 scenarios were restarted from the beginning in a new output directory.
The final 35/63 score is from that single completed run, not a splice of successful attempts.

Some overlapping scenarios changed between the smoke/interrupted runs and the completed run,
despite temperature 0. Their exact cause was not isolated. These observations do not establish
repeatability or a statistical confidence interval; earlier passes must not replace later failures.

## Stage results and denominators

| Check | Result | Interpretation |
| --- | --- | --- |
| Expected routing output | 84/87 | Includes three repeated-clarification errors |
| Request specification | 45/48 | Turns whose expected route requires a complete specification |
| Expected primary tool | 45/48 | Selecting the right tool does not prove valid arguments or execution |
| Clarification missing fields | 24/24 | Checks only turns expected to ask for clarification |
| Continuation protocol | 24/24 | Does not mean the resumed answer was correct |
| Final verification of scored data-answer turns | 17/45 | Scored data-answer turns; excludes routing-only reference scoring |
| First verification | 16/21 | All turns that actually reached verification, including references |
| One-repair recovery | 1/5 | Only turns where a repair was triggered |
| Successful data/retrieval tool execution | 21/87 turns | Recomputed excluding `decline_request` and `state_findings` |
| Router/model protocol errors | 0/87 | Does not exclude execution errors, refusal or budget exhaustion |

The runner's broader `tool_execution=36` counts any successful tool, including terminal tools.
It must not be read as 36 successful data queries. Structural scope/card checks can pass when
there are no executed queries or no cards; their 45/45 values are not answer-quality evidence.

All three reference scenarios passed their routing-only rubric, but returned `tool_error`
responses later in execution. Full RAG answer quality is therefore **not** demonstrated by 3/3.

## Failure partition: 28 scenarios

### 1. Tool argument shape: 24 scenarios

For example, `routed-v1-00000-daily` correctly identifies steps and the requested date, then
attempts `daily_summary` with:

```json
{"metric": {"type": "steps"}, "start_date": "2026-08-09", "end_date": "2026-08-09"}
```

The tool requires `"metric": "steps"`.
[Scope enforcement](../../src/api-service/healthops/assistant/guards.py), `scope_error`, compares
the submitted metric to confirmed metrics and rejects this object. Repeated submissions can
exhaust the budget or lead to an incorrect `invented_value_requested` refusal. The server does
not execute the malformed scope, but the user still does not receive an answer.

The completed run contains 84 metric-scope rejection calls across repeated attempts. The 24
failed scenarios with scope rejections are distinct from the three routing failures and the
one failed availability verification below. The dataset's missing-record scenarios also fail
in this group: they do not demonstrate that missing records became zero; they fail before
producing the required missing-data answer.

[Tool schemas](../../src/api-service/healthops/assistant/toolbox.py), `ToolSpec.schema`, still
use Pydantic schemas with definitions/references. Making the model-facing enum type explicit
and adding complete tool-call examples are reasonable next experiments, not proven fixes.
The current scope error also obscures a type error as an unconfirmed metric; more precise
feedback should preserve validation rather than silently coercing arbitrary objects.

### 2. Confirmed time phrase rejected: three scenarios

All three `lately` cases ask "How are my steps lately?" and then answer "The past 7 days."
The HTTP response asks for a time range again.

A separate post-run reproduction with the same user/assistant dialogue recorded:

```json
{"decision": "ready", "task": "summary", "metrics": ["steps"],
 "time_phrase": "The past 7 days", "baseline_phrase": "", "language": "en", "reason": "none"}
```

[Routing code](../../src/api-service/healthops/assistant/routing.py), `quoted_window`, uses
`re.fullmatch` and only strips surrounding whitespace. `past 7 days` parses, but
`The past 7 days` and `The past 7 days.` return `None`. The downstream `route` function then
marks `time_range` unresolved and returns `needs_clarification` despite the model's `ready`.

This reproduction demonstrates a deterministic parser boundary. The HTTP run does not retain
raw Router proposals, so the reproduction is separate evidence rather than a recovered
original trace. A conservative normalization fix should retain grounding in actual user text
and must not turn ambiguous "recently" into an invented seven-day range.

### 3. Wrong evidence identifier: one scenario

`routed-v1-00000-inventory` successfully retrieves an availability card with ID `e1`. The model
submits `available_series e1.healthkit`, then repeats that nonexistent ID during repair.
[Claim verification](../../src/api-service/healthops/assistant/claims.py), `verify_claim`, rejects
it as `unknown evidence id`. Coverage is not satisfied, and the server falls back to showing
cards without a verified explanation. A response status of `answered` alone is not a pass.

## Successful example and cohort results

`routed-v1-00000-comparison` calls `period_comparison`, receives card `e1.healthkit`, submits
`window_difference e1.healthkit`, and passes the first verification with no missing targets.
The server renders the values from the validated card; the model does not supply the displayed
numbers. All three comparison scenarios passed.

| Cohort | Passed scenarios |
| --- | --- |
| Apple-only, `synthetic-00000` | 11/21 |
| Fitbit-only, `synthetic-00003` | 12/21 |
| Paired, `synthetic-00001` | 12/21 |

Other useful group results: unsupported requests 12/12, cancellation 3/3, changed intent 3/3,
casual questions 3/12, completeness 0/6, and partial clarification 0/3.

## Next work and verification scope

Prioritize the bounded time-phrase parser fix, explicit execution-tool parameter schemas and
examples, and evidence-ID selection/repair guidance. Keep strict identity, scope, missing-data
and claim checks. These changes were **not** implemented during this measurement.

The live diagnostic, direct Router smoke, reference recovery smoke and post-run parser
reproduction were executed. Existing unit/integration suites were not rerun because no
application code changed. Public-content, Markdown-path and whitespace checks accompany this
report. Further training should use failures after avoidable protocol/parser issues are fixed,
with a separate held-out evaluation set.

## Cleanup verified

The temporary L4 and its auto-delete boot disk were deleted. The abandoned snapshot was
also deleted; inventory checks found no temporary VM, disk or snapshot remaining. The
`routed-eval` URL tag was removed. The evaluated revision remains as an untagged zero-traffic
revision; its temporary model endpoint is no longer running. Both original VMs remain stopped.
Normal traffic remains 100% on `healthops-ms3-api-00002-ps7`; the pre-existing candidate tag is
unchanged. No commit or push was performed.
