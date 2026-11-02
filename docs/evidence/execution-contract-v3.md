# Time parsing and execution-tool contract: November 1, 2026

## Implemented changes

This change targets the malformed execution arguments and time-phrase rejection observed in
[the Router v2 retest](router-contract-v2-retest.md). It does not relax identity, scope,
coverage, missing-data or claim-verification checks.

- [Routing](../../src/api-service/healthops/assistant/routing.py):
  `normalized_time_phrase` removes a leading English article and terminal sentence punctuation
  for parsing only. `WindowProposal.phrase` retains the original wording, and `resolve_window`
  still requires that wording to occur in user messages. Ambiguous phrases such as "recently",
  negated whole phrases, alternatives and invalid calendar dates remain unresolved.
- [Tool schemas](../../src/api-service/healthops/assistant/toolbox.py):
  `ToolSpec.schema` inlines local definitions, retaining constraints and reference-site metadata,
  and removes unused definitions/titles. Metric fields explicitly expose a string enum. Hidden
  user-context fields remain absent. Complete daily-summary and comparison argument examples
  illustrate shape; the prompt directs the model to use confirmed dates rather than example dates.
  Model-facing schemas prohibit additional properties; existing server parameter validation is
  retained. Nonlocal or recursive schema references fail explicitly.
- [Scope feedback](../../src/api-service/healthops/assistant/guards.py):
  malformed metric objects/lists receive a type-specific rejection before membership checking.
  No object-to-string coercion is performed. Correcting the type does not permit different
  metrics, providers or date ranges.
- [Execution prompt](../../src/api-service/healthops/assistant/prompts.py):
  explains string metrics, type correction and the distinction between malformed arguments
  and a user asking for fabricated values. Router output version and execution prompt version
  are `2026-11-01.1`; the Router prompt text itself is unchanged.

For example, `The past 7 days.` now resolves to the same inclusive range as `past 7 days`
when the original phrase is present in the user's message. A model-proposed range not present
in user text is still rejected. `{"metric":{"type":"steps"}}` remains invalid; the model
must submit `{"metric":"steps"}` before a data query can execute.

## Verification

- Targeted routing, toolbox, scope and evaluator suite: **75 passed, 2 skipped** without a DB.
- Full isolated Compose integration suite: **339 passed, 5 skipped** for host-only tools.
- Repository-policy, iOS-contract, evaluation-set and DOM host checks: **45 passed** overall.
  Two Swift checks initially failed because the sandbox could not access compiler caches;
  both passed when rerun with the required cache access. Counts overlap with earlier suites;
  they must not be summed as unique coverage.
- Ruff lint/format, public-content policy, whitespace and Markdown path checks accompany the change.

The new regressions verify real failure boundaries:

| Setup/action | Assertions |
| --- | --- |
| Parse article/punctuation variants | Same dates, original quote retained, user grounding still required |
| Ambiguous/negated/alternative/invalid phrases | No inferred date window |
| Scripted pending conversation plus confirmed seven-day reply | Ready scope, consumed conversation version, no repeated clarification |
| Inspect every model-facing tool schema | No unresolved references or hidden identity fields; metric is a string enum |
| Validate complete schema examples | Examples satisfy the real server parameter models after timezone injection |
| Submit nested metric through the real toolbox | Invalid arguments; no silent coercion |
| Scripted invalid metric followed by correction | First call rejected, exactly one valid data call, verified complete answer |

Scripted tests demonstrate protocol behavior, not Qwen accuracy. The full integration suite
uses only `healthops_test`; live evaluation uses only the three frozen synthetic accounts.

## Cloud build and model preflight

Cloud Build `509daae2-eeb8-4b04-aaaa-d7bb149a57b0` succeeded. The source upload contained only
tracked API-service files copied from the working tree, including the tested changes, and
excluded local environments, credentials, datasets and evaluation outputs.

Image digest: `sha256:1402d9438c235264899acdfcf56e7609944d6e0ba897f5e5699921a7136c30c1`.
The four changed implementation files were compared byte-for-byte with the build source.
The runner checkout is `475e6de` plus the uncommitted changes; the image digest and recorded
source hashes, rather than HEAD alone, identify the tested implementation.

Temporary L4 allocations in us-central1-c, us-central1-a and us-west1-a, and T4 allocations in
us-east1-c, us-central1-f and us-west1-b, failed for capacity. The existing dedicated model L4
in us-east4-c subsequently started successfully, with its two-hour automatic-stop limit.
The personal-data VM was not started or accessed.

The existing Ollama 0.35.1 server, Qwen3-8B digest and nomic-embed-text digest matched the
previous run. Qwen was observed loaded on an NVIDIA L4 with context 32768 and driver
580.178.04. Evaluation uses temperature 0 and thinking disabled.

Revision `healthops-ms3-api-router-v3-eval-1101` was deployed with zero normal traffic.
Four real HTTP smoke scenarios passed: daily steps, multiple metrics, seven-day clarification
continuation, and reference routing. A separate simplified direct probe containing consecutive
user messages without the assistant's clarification still selected the stale word "lately";
that probe is not equivalent to the actual persisted dialogue and is not counted as a pass.
The unchanged 63-scenario diagnostic is recorded separately from these smoke checks.

## Completed frozen-set evaluation

The unchanged HTTP diagnostic completed all **63 scenarios / 87 turns** against the three
frozen synthetic identities. No rubric, question, simulated clarification reply or gold answer
was changed to obtain these results.

| Measure | Previous v2 run | This run |
| --- | --- | --- |
| Scenario rubric passes | 35/63 | **63/63** |
| End-to-end scenario passes | 32/60 | **60/60** |
| Reference routing-only passes | 3/3 | **3/3** |
| Routing-output checks | 84/87 | **87/87** |
| Graded final-verification checks | 17/45 | **45/45** |
| Malformed metric scope rejections | 84 calls | **0 calls** |

Request-spec and primary-tool checks each passed 48/48. Coverage, gold evidence and
claim rechecks each passed 45/45. All 24 clarification-continuation checks passed. Each
synthetic account passed its 21 scenarios. Routing-output checks include three cancellations,
so 87/87 is not a count of independent model-routing decisions.

The case file SHA-256 is
`1c9a41cfbf3775d0cfc4e4e7f30ad857aabe3c296336792117da0b56480d7110`.
The cohort-lock SHA-256 is
`a1249f63e2c5d6ff7c61287214ebab624e1340f42d5e8f3d963c9174b09e56b4`.
The generated gold is byte-identical to the previous completed run, with SHA-256
`e5109fb995af03e6a6f3b2d45a52c3a954d2307174e76c26241a714e2a913275`.
Raw reports remain local under `src/api-service/.local/eval/cloud-router-v3-full-20261101/`.

### Remaining reference-answer failure

The three reference scenarios grade routing only. All three still returned downstream
`tool_error`; they are **not successful end-to-end RAG answers**. Retrieval returned no
reference cards with `no_reference_found`. The model then proposed an unavailable evidence ID
such as `e1.r1`; verification rejected it. Empty `state_findings` retries were also invalid.
First verification succeeded on 45/48 execution turns, and reference repair recovered 0/3.
Unlike the previous missing-model incident, the embedding model was available in this run.

A separate next change should handle empty retrieval explicitly and add end-to-end reference
rubrics. This diagnostic set has been reused for debugging: 100% on its current rubric does
not establish held-out accuracy. The parser, schema, feedback and execution prompt changed
together, so these results do not isolate each change's causal contribution. No fine-tuning
was performed.

## Cleanup and deployment scope

The evaluation tag was removed. Normal traffic remains 100% on
`healthops-ms3-api-00002-ps7`; the pre-existing candidate tag is unchanged. The new evaluation
revision was not promoted. The dedicated model VM was stopped and verified `TERMINATED`,
with its disk preserved. No temporary VM or disk from the failed recovery allocations remains.
The SSH tunnel closed when the VM stopped. The isolated Compose test database was stopped.
There was no health-data import or schema migration; synthetic conversation state was written
by the evaluation. No git commit or push was performed.
