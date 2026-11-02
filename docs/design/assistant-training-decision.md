# Assistant error triage and post-training decision

Date: 2026-11-01. Decision: **do not start paid post-training yet**.

## Evidence and immediate action

The previous live v2 and v3 runs used the same frozen 63 scenarios. Their recorded rubric passes
were 35/63 and 63/63, respectively, after parser/schema/prompt changes without fine-tuning.
The v3 run includes 60/60 end-to-end scenarios and three reference-routing-only cases whose
actual answers failed. It is a reused diagnostic, not held-out evidence.

`src/api-service/scripts/triage_assistant_eval.py` was run on both existing local reports. On v3
it found 60 scenarios without flagged error signals and three with both empty retrieval and
invalid execution-contract attempts. All 63 still passed the old rubric. These signal counts
overlap; they are review hints, not causal attribution or an alternative accuracy score.
It created **zero training examples**. The new implementation handles empty retrieval explicitly;
that fix passed scripted tests, but has not been remeasured on the cloud model.

The new 36-scenario / 72-turn interaction candidate suite has valid expected scopes and has not
been run on Qwen. Its eventual results, plus observed user difficulty with the native interface,
should drive the next model decision. Keep it out of training. If reused for prompt tuning,
mark it diagnostic and author a separate evaluation split.

| Observed issue | First action | Training eligibility |
| --- | --- | --- |
| Ambiguous UI or hidden selection | Change choices, wording or confirmation summary | No |
| Expired/stale version, lost response, account mismatch | Fix lifecycle or explain restart | No |
| Empty/unavailable retrieval | Inspect corpus/query/service and handle absence | No automatic eligibility |
| Invalid metric object or wrong tool | Check schema, examples and protocol first | Review if repeated after fixes |
| Wrong routing, scope or unsupported decision | Check intent contract and ambiguity | Review against manually authored target |
| Invented evidence ID | Confirm evidence existed and was visible in context | Review after upstream fixes |

## What a training example would contain

The target is the next structured model action, not a fabricated final health sentence. Keep the
exact tool schemas, system prompt version, user dialogue, confirmed scope and any tool evidence
visible at that step. A router target is `route_request`; an execution target may be
`daily_summary`; a terminal target is `state_findings` with valid evidence references.
Python still renders verified facts. An empty retrieval should not be labelled as a successful
reference answer.

Illustrative router example, not a training record or measured model output:

- Context: user says "My steps recently?"; no date has been confirmed.
- Reviewed target: `decision=needs_clarification`, `task=summary`, `metrics=[steps]`,
  `time_phrase=""`, `baseline_phrase=""`, `language=en`, `reason=none`.
- A candidate rejected action would invent a seven-day window. It can become a preference pair
  only after the exact same input/context and the model-produced rejected action are recorded
  and reviewed. Do not generate preference labels from pass/fail alone.

## Gate before any training job

1. Freeze model digest, prompt/schema/code and dataset versions; evaluate the new candidate set.
2. Reproduce residual failures and rule out UI, transport, missing data and retrieval defects.
3. Author correct action sequences and validate arguments, ownership, scope and evidence.
4. Separate train/development/evaluation by scenario template and paraphrase family, not only by
   synthetic account. Repeating one template for three accounts is not three independent intents.
5. Start with a bounded supervised adapter experiment if enough reviewed action traces exist;
   consider preference optimization only with comparable reviewed pairs. RL requires a validated
   reward and action trajectory setup; preference pairs alone do not establish one.
6. Compare baseline and candidate on the same untouched evaluation set. Require no regression in
   unsupported handling, account isolation, scope, missing-data statements or factual verification.
   Also record clarification burden, latency and cost. Do not replace verification with a trained model.

## Reproduce triage

Run from `src/api-service` with an existing synthetic evaluator output directory:

```bash
uv run python scripts/triage_assistant_eval.py \
  --run .local/eval/cloud-router-v3-full-20261101 \
  --output .local/eval/review-new-name.json
```

The script accepts only reports for the frozen three synthetic identities, rejects duplicate IDs,
keeps input/output under `.local/eval`, refuses overwrites and exports only IDs and error signals,
not questions, health evidence or credentials. `eligible_for_training` stays false pending review.
This decision does not launch Vertex, Brev, GPU instances, W&B or a training job.
