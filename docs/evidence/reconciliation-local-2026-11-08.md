# Local reconciliation tool integration and diagnostic

Observed November 7, 2026 (US Eastern), November 8 UTC. This increment integrates the team's
trained reconciliation Transformer into the existing authenticated API and LangGraph assistant.
It does not retrain Qwen or the Transformer. **Cloud import and deployment were deferred by the
project owner**: no new VM, Cloud SQL write, or Cloud Run deployment was performed for this handoff.

## Artifacts and input data

- Checkpoint: `20261107T231608Z/w14/transformer`, trained on `synthetic-v0.2`; the immutable file
  hashes are in [reconcile-model.json](../../src/api-service/reconcile-model.json).
- Downloaded model and dataset files were checked against their SHA-256 manifests. Artifacts,
  imported data, credentials and raw evaluation responses stay outside Git.
- Imported 16,276 synthetic observations into isolated local `healthops_test` PostgreSQL for
  three version-scoped users: `synthetic-00000` (Apple-only), `synthetic-00001` (paired), and
  `synthetic-00003` (Fitbit-only). No personal database or real health records were used.
- Qwen3-8B Q4_K_M via local Ollama 0.35.0, model digest
  `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
  Apple M4 Pro / Metal, 36 GiB unified memory; Python 3.12.6. Transformer inference uses CPU.
  These observations are not a Cloud Run or NVIDIA GPU evaluation.

## Diagnostic protocol

[The committed set](../../src/api-service/eval/assistant_reconcile.synthetic.jsonl) contains
42 scenarios and 45 turns: 14 families per account, including one clarification/resume sequence
per account. Questions with quoted wearable values must still use stored observations. No-reading
questions, limited history, sparse windows, single-channel data, month requests, HRV and medical
judgments have explicit expectations.

Gold is frozen from the authenticated user's actual v0.2 tool output, pinned checkpoint and
server aggregation rules. This checks correct use of the tool, **not whether the estimate equals
physiological truth**. The three users are development diagnostics, not a held-out accuracy study.

A pass requires the expected route, allowed tool/scope, matching evidence and warnings, regenerated
cards, valid claims, full answer coverage and verified completion. Additional rubric columns are:
`red_flag_when_insufficient`, `no_number_when_no_reading`, `interval_stated`,
`attribution_matches_card`. All four insufficiency codes require the exact red-flag sentence.
Warnings are preserved even if claim verification fails; an unverified fallback still fails the
end-to-end diagnostic. We do not accept card presence alone as a successful answer.

## Iterations and remaining failure

| Run | Scenarios | Purpose / result |
| --- | --- | --- |
| Exploratory | 39/42 | Found invalid provider-suffixed evidence handles. Its early rubric checked only short/sparse history, so it is not the final four-warning compliance result |
| Explicit evidence examples | 41/42 | Added exact shared-card `state_findings` examples; checked all four red flags |
| Final local code | **41/42 (97.6%), 44/45 turns** | Includes narrow Chinese sleep grounding, explicit single-day refusal guidance and warning preservation on verification failure |

Both the second and final runs had the same remaining failure: `synthetic-00001 / three_day_history`: Qwen queried the
correct tool/date and obtained the correct evidence, but added `reference e1` to reconciliation
findings. That claim type is invalid for this card; one repair did not remove it. The server
returned an unverified fallback and retained the card. In the final run, all mandatory
insufficiency warnings also survived in that fallback; interval and valid finalization checks
still failed, so the scenario remained a failure. No unsupported health claim was rendered
as verified. This is a finalization-protocol failure, not a database or Transformer inference failure.

Three supplementary real-Qwen routing-only probes also passed: the Chinese last-night sleep
device-comparison question resolves to sleep duration and yesterday; generic sleep wellbeing
asks for a metric; explicit resting-heart-rate reconciliation is ready. These probes are separate
from the 42 scenarios and do not establish full Chinese answer quality.

Raw runs are local only under `src/api-service/.local/eval/reconcile-v02/` (`local-qwen`,
`local-qwen-v2`, `local-qwen-final`). The final harness writes source-file hashes and environment
metadata alongside gold and per-turn responses. Intermediate rates are not substituted for the
final outcome.

## Engineering verification

| Check | Observed result | What it establishes |
| --- | --- | --- |
| Full local API and repository-script tests | 452 passed | Auth, isolation, scope, clarification, warnings, rendering, rubric mutation tests and existing regressions |
| Docker integration suite | 418 passed, 7 skipped | API integration with isolated PostgreSQL in Linux; optional torch/Swift checks run on host separately |
| Swift package tests | 60 passed | Client contracts and existing interaction/state behavior |
| iOS Simulator app build | Passed, unsigned Debug | New reconciliation card case compiles in the actual app |
| Optional CPU inference image | Built and real checkpoint inference passed | CPU torch installs and produces ordered quantiles from an invented fixture inside Linux |
| Training/serving parity | Passed locally with torch/pandas/numpy | Identical network output for identical tensors; matching feature order, masks, defaults and unit transforms |

The counts above include a pre-existing uncommitted API regression test in the working tree;
that test and the unrelated training-data edits are preserved and excluded from this increment's
commits. The training source is used for network/feature parity, not imported by serving.

Meaningful safety tests include: user B cannot read user A's samples; absent target-day readings
never call the predictor; a single channel cannot support attribution; every accepted finding
includes interval and warnings; a failed repair stays unverified with warnings; stripping a caveat,
changing attribution or inventing a reading fails the evaluator. Model loading rejects tampered
artifacts. Calendar validation and schema reject invalid inputs rather than silently coercing them.

Commands executed (repository root unless noted):

```bash
# Inside src/api-service, with the temporary test venv and isolated PostgreSQL URL configured:
PYTHONPYCACHEPREFIX=/tmp/healthops-reconcile-pycache PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /tmp/healthops-reconcile-venv/bin/python -m pytest -q tests ../../scripts/tests
# The local override only publishes test-db on loopback for the host-side test runner.
docker compose -f docker-compose.yml -f /tmp/healthops-reconcile-test-port.yml --profile test build test
docker compose -f docker-compose.yml -f /tmp/healthops-reconcile-test-port.yml --profile test run --rm test
docker build --build-arg INSTALL_RECONCILE=true -t healthops-reconcile-local-check src/api-service
swift test --package-path src/frontend-app/HealthOpsUploader --scratch-path /tmp/healthops-reconcile-swift
# Inside src/frontend-app/HealthOpsUploader:
xcodebuild -project HealthOpsUploader.xcodeproj -scheme HealthOpsUploaderApp \
  -destination 'generic/platform=iOS Simulator' -configuration Debug \
  -derivedDataPath /tmp/HealthOpsReconcileDerivedData CODE_SIGNING_ALLOWED=NO build
```

Reproduction setup and evaluation command are in [reconciliation.md](../reconciliation.md).
The temporary venv and loopback override are local test infrastructure, not repository dependencies.
A first Docker bind mount from the synced Desktop failed with a file-read deadlock; the same
checksum-pinned artifacts under `/tmp` loaded and inferred successfully. PyTorch emitted optional
NumPy/nested-tensor warnings, which did not prevent tensor-only inference.
No physical-device acceptance, cloud validation or clinical validation is claimed. Unchanged data
processor/synthesizer suites and full model retraining were not rerun for this serving increment.

## Deviations from the teammate brief

- Kept all reconciliation-specific tests in dedicated `test_reconcile*.py` files instead of mixing
  them into existing test modules; they run with the same suite and isolated-database fixtures.
- Added a dedicated `eval_reconcile.py` and versioned scenario file rather than changing gold for
  the existing v0.1 routed benchmark. It reuses the application guards/claims and existing HTTP client.
- Sparse coverage scales with the metadata window (less than half), rather than keeping a literal
  seven-day threshold if a future checkpoint uses w30.
- Single-channel questions with explicit date/metric produce a qualified estimate with null
  attribution; questions without scope clarify. A question cannot manufacture a second device.
- API contract uses existing conventions: `window_days_with_any`, `window_days_with_both`, and
  `definitions` for explanatory notes. Model provenance identifies training data, not a user's data.
- Aggregation reuse revealed a training/serving difference: training's latent bedtime grouping is
  unavailable to serving. It is disclosed via `sleep_alignment_differs`, not silently claimed to
  match. Uncollected context uses defaults and is disclosed via `context_defaulted`.

Before a cloud rollout, align sleep grouping with the training owners, review default context
behavior, import v0.2 only into the dedicated synthetic environment, build the pinned CPU image,
and validate a tagged Cloud Run revision. These remain future actions requiring the appropriate
execution scope; configuration files alone are not deployment evidence.
