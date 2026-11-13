# Single-check writer and final editor comparison

## Change

The first writer draft receives one Python content-check pass. A passing draft is returned.
Any finding can request one final edit with the original question, evidence, draft and
structured checker feedback. The model may accept or disagree with the feedback.
The final edit is parsed for the JSON contract and absence of tool calls only; its content
is not checked again. This is an explicit experimental tradeoff, not a correctness guarantee.

The policy is `grounded-answer-v3-single-check`. The initial writer system prompt is unchanged
from the baseline; the editing policy and feedback changed. Claims/scope verification before
writing, authenticated user scoping and request budgets remain in force.

`narrative_check_reason` records the initial finding and `narrative_edit_attempted` records an
editor call. Edited prose is `model_generated`, not `verified`. No post-edit numeric scan runs;
`numeric_consistency` reports `not_applicable` with a descriptive note.

## Matched synthetic comparison

Baseline: runtime from commit `2d7c81d`, with citation-only repair and revalidation.
Candidate: the single-check implementation in this change.
Both runs use the existing 30-scenario pretraining suite (36 user turns), the same three
manifest-verified synthetic-v0.2 accounts, isolated PostgreSQL, and the existing Qwen3:8b
Ollama service. Temperature is 0, thinking is disabled, and output limit is 800 tokens.
Model digest: `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
The GPU is an existing V100. HTTP uses local authenticated ASGI, not the Cloud Run endpoint.
No resources or training jobs were created.

| Observation | Baseline | Candidate |
|---|---:|---:|
| Scenarios completed | 30 | 30 |
| User turns | 36 | 36 |
| Ready / clarification / unsupported turns | 15 / 15 / 6 | 15 / 15 / 6 |
| Requests reaching the writer | 14 | 14 |
| Writer-generated responses | 13 | 14 |
| Writer fallbacks | 1 | 0 |

All 36 routing decisions/request specifications and all 36 evidence arrays match across runs.
All 14 first-writer inputs and raw first drafts match exactly. Final responses match for
35 of 36 turns. These checks make the single changed response attributable to the editing policy.

**The changed response is a quality regression.** A synthetic sleep answer contained an
incorrect seconds-to-hours conversion and an unsupported claim about within-day consistency.
The baseline withheld that draft for an unmatched number. The candidate editor received the
feedback but repeated the draft, which was returned under the requested no-second-check policy.
Therefore 14/14 is a generation-completion measure, not accuracy. No semantic accuracy gain
is established; removing a fallback can expose an existing model error.

Raw synthetic traces remain ignored under `src/api-service/.local/eval/`. Candidate directory:
`writer-single-check-final-1109`; isolated baseline snapshot directory:
`writer-policy-baseline-source/.local/eval/baseline`. No preference pairs were approved or
training/validation/held-out data changed. This is a development regression comparison.

## Verification and limits

- API/unit/contract/repository regression selection: 299 passed after updating synthetic
  response fixtures for two additive diagnostic fields.
- Writer and preference-audit tests cover all finding types, unchanged evidence, a maximum
  of one edit, timeout/turn limits, invalid final JSON, prohibited editor tool execution,
  and the deliberate acceptance of unsupported editor prose without another content gate.
- Fixtures are synthetic; personal diagnostic traces never enter fixtures, docs or Git.
- The API contract and Swift-compatible JSON fixtures include the additive fields.
- Real personal replay details are kept separately in the owner's private local directory.
- The comparison above used local orchestration with remote inference. The subsequent
  personal Cloud Run rollout is recorded below.
- Full Compose integration and unrelated data/training-service suites were not run for this
  change. Existing end-to-end synthetic replay used actual database tools and model inference.

To repeat the synthetic suite, run `scripts/eval_writer_chain.py --pretraining` from
`src/api-service`, providing an isolated `healthops_test` database URL, a manifest-verified
import directory, the model URL, the pinned reconcile artifact directory and a fresh output
path under `.local/eval/`. The runner rejects personal databases. Run the same frozen cases
under the baseline and candidate source, and compare evidence and first drafts before
attributing any change to the editing policy. Do not compare generation rate as accuracy.

## Subsequent personal-service rollout

On November 9, 2026 (America/New_York), source commit `6807350` was built from a
tracked-only source snapshot and deployed to the existing personal Cloud Run service.

- Build: `6a01f15c-4261-4226-8a9a-3726c266cbfd`, successful.
- Revision: `healthops-personal-api-single-check-1109`, receiving 100% default traffic.
- Image: `us-east4-docker.pkg.dev/ac215-project-509705/healthops/api@sha256:1abd5ef045d3a7de6d6afc50db095d92ec50ba9609bd6c10eddbbd7c31a5200f`.
- The prior revision is retained for rollback; the existing phone URL and token are unchanged.
- Runtime identity, environment/secret references, resource limits and VPC configuration
  were compared against the prior revision and preserved. No database migration was needed.
- Tagged-revision checks returned HTTP 200 for readiness, personal identity and the existing
  Google Health connection. An authenticated writer request returned `model_generated`,
  `grounded-answer-v3-single-check`, `narrative_edit_attempted=true`, and
  `numeric_consistency.status=not_applicable`; the initial finding was citation coverage.
  The same checks and writer-policy fields also passed on the exact URL stored on the
  phone after promotion (7.9 seconds for the diagnostic question). This verifies routing
  through the new policy, not semantic correctness of the answer.
- The existing V100 VM was restarted with its unchanged 7,200-second automatic STOP setting.
  CUDA offload was confirmed with approximately 9.97 GB model VRAM and a 32,768-token context.
  The model digest is unchanged. The first warm-up request timed out during cold loading;
  a subsequent warm-up completed before traffic promotion.
- The current testing window ends around 00:36 on November 10, 2026 (America/New_York).
  This is a scheduled development window, not continuous availability.

Only deployment metadata is recorded here. Personal answer contents and health values are
not included in this report, the repository, or training data. No iOS reinstall is required;
new questions use the new backend, while existing chat messages retain their original text.
