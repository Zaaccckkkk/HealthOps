# Writer DPO preparation, 2026-11-08

Prepared a final-writer-only preference experiment using Qwen3-8B, NeMo RL's Megatron
backend and BF16 LoRA. The selected topology is one four-GPU node with TP=2, DP=2, PP=1
and CP=1. No topology sweep, cloud provisioning, paid inference or model training ran.
This is not a QLoRA implementation or evidence of A100 runtime compatibility.

Implementation and runnable instructions:
[writer training preparation](../../src/model-training/writer_dpo/README.md).

## Implemented

- Immutable HF model revision and NeMo source/interface pins; the upstream lock and submodules
  own GPU dependencies separately from the reconciliation service's CPU environment.
- Exact captured prompt/rejected-output audits, grouped train/validation leakage checks,
  blocked/holdout exclusion, and approval binding to the specific preference pair and split.
- A NeMo configuration generator, full-snapshot verification, runtime processor preflight,
  three-step smoke profile, ten-epoch/60-step training limits, validation checkpoints and a
  two-hour subprocess timeout. The timeout is not VM cleanup or a billing limit.
- A separate container recipe requiring an immutable upstream NeMo image digest.
- Frozen writer replay, explicit semantic-review comparison, and evaluation-only dispatch
  that changes the final writer while retaining the base router/tool model.

## Local evidence

Audited all **31 development candidates**: 24 proposed training and 7 validation. Exact prompts
and observed rejected completions matched original synthetic traces. All 31 chosen outputs
passed the existing application's basic writer checker. This does not prove semantic correctness.
Five contract-blocked candidates remain excluded. Existing metadata has 24 saved wording
approvals and seven replacement confirmations still pending. A local draft was written; no
candidate flags, spreadsheets or training eligibility were silently changed.

For seven multimetric candidates whose merged metadata lacked a source path, the auditor used
an explicit original trace directory and verified the previously stored trace hashes. No
evidence or rejected completion was reconstructed to fill that metadata gap.

Used the pinned upstream configuration loader to resolve the generated smoke overlay, checking
the model precision, three-step bound, checkpoint metric and replacement of the example
HelpSteer3 data configuration. Executed the pinned upstream
`BinaryPreferenceDataset.format_data` method on all 31 actual candidates: it preserved the
conversation list and both completion strings exactly. These are CPU interface checks, not
execution of the full NeMo processor, distributed trainer or GPU kernels.

## Tests

| Group | Result | What it establishes |
| --- | --- | --- |
| Writer preparation contracts | 25 passed | Stale approvals, leakage, trace/negative edits, bundle tampering, unsupported precision/topology, partial-batch loss and timeout behavior |
| API required unit/contract list plus writer chain, holdout, preference and checkpoint suites | 234 passed | Existing API contracts, synthetic test isolation/freeze, writer-only model dispatch and semantic-review boundaries |
| Repository policy suite | 43 passed | Existing deployment/publication policy contracts |
| Ruff lint/format and public-content check | Passed | Formatting, English public text and exclusion of local artifacts |

The existing API virtual environment stalled reading a LangSmith dependency during test
collection. Recreated its exact frozen `uv.lock` in `/tmp/healthops-api-dpo-tests` without
altering the original environment. The broad run initially passed 232 checks; two Swift fixture
checks failed because the sandbox denied compiler-cache access. Those two passed when rerun
with the required local cache access. No test expectations were weakened.

Representative commands (from the repository root unless noted):

```sh
PYTHONPATH=src/model-training /tmp/healthops-api-dpo-tests/bin/python -m pytest -q \
  src/model-training/tests/test_writer_dpo.py
PYTHONPATH=src/api-service /tmp/healthops-api-dpo-tests/bin/python -m pytest -q scripts/tests
python3 scripts/check_public_content.py
```

The API run used the test list in `AGENTS.md`, plus `test_writer_checkpoint.py`,
`test_writer_chain.py`, `test_writer_holdout.py` and `test_chain_preferences.py`, from the API
service directory. The training tests use temporary synthetic fixtures and scripted subprocesses.
The API writer dispatch tests use two fake models, ensuring router/tool calls reach the base
and only the exact tool-free writer request reaches the candidate writer.

## Evaluation boundary and remaining work

The unchanged [holdout](../../src/api-service/eval/writer-holdout-v1/README.md) contains
20 scenarios / 22 turns. Replay is limited to the **11 valid writer inputs actually captured**
by the historical baseline; the nine upstream failures remain in the full-chain denominator.
No frozen question was run again during this preparation or mined into training examples.

For a controlled training comparison, serve base and adapted writers using the same runtime,
precision and decoding settings. Historical Ollama Q4 output is not a matched BF16 control.
Mechanical checks remain separate from reviewed semantic pass/partial/fail labels. Test labels
must not select the checkpoint or provide new training negatives.

Still required: resolve exact-pair approvals, privately version/export the bundle, build and
record the GPU image digest, fetch full HF weights, execute actual tokenization and GPU smoke
checks, verify checkpoint restore/export/loading, and run the matched before/after evaluations.
The GPU image, full tokenizer processor, distributed backward pass and adapter export were not
run. PostgreSQL integration, other services' full test suites and cloud/phone deployment were
not rerun because application behavior and deployment were not changed by this increment.
