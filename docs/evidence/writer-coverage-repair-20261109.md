# Writer citation repair and truthful phone fallback status

## Observed failure

The phone displayed a deterministic data summary despite the final writer being enabled.
A status-only replay on the active personal service returned `narrative_status=fallback`,
`narrative_reason=evidence_coverage_mismatch` and `explanation_status=verified`.
The structured claims were verified, but the writer's citation set did not match the cards.
The iOS client previously decoded only explanation status and hid the writer fallback status.
No personal measurements or answer text were copied into fixtures, training data or this report.

## Change

- [Narrative repair](../../src/api-service/healthops/assistant/narrative.py) preserves the exact
  original evidence and requests one complete rewrite with the required evidence IDs and
  source-specific findings or limitations.
- [Graph execution](../../src/api-service/healthops/assistant/orchestrator.py) permits at most
  one repair for citation coverage. Both attempts share the original writer timeout, total
  request deadline, turn limit and concurrency gate. Numeric/date/format checks remain active;
  repeated failure keeps the checked template. No extra tool or database query is added.
- [Phone contracts](../../src/frontend-app/HealthOpsUploader/Sources/HealthOpsCore/AssistantContracts.swift)
  decode writer status and reason. The screen now distinguishes AI prose from a checked-template
  fallback; an unverified explanation never gets the checked-summary label.
- The capture audit verifies the bounded repair transcript and unchanged evidence. Preference
  export rejects retry traces as first-draft pairs until separately reviewed. Existing training
  data and the first-draft writer system prompt were not rewritten.

## Verification

- 343 selected API/database/contract/evaluation/policy tests passed. One additional invented-value
  repair test was then added and passed in the 38-test writer suite.
- Scripted tests cover successful missing-source repair, repeated failure without looping,
  shared timeout, model-turn budget, unchanged evidence, no extra SQL tool call and continued
  rejection of an invented value after citation repair.
- 71 Swift tests passed; the current device build compiled and installed on the confirmed phone.
  Tests include fallback visibility, older responses without writer fields and unverified evidence.
- The reported question was also run through the real model and isolated synthetic database for
  three cohort-relative windows. All three returned generated prose and passed evidence-provenance
  audits. Each succeeded on its first writer attempt, so this is not a measurement of repair efficacy.
- The cloud candidate replay of the reported request returned `answered`, `complete`, `generated`,
  `model_generated` and Chinese prose in 7.7 seconds. Only execution metadata was saved.
  Readiness/account/Google-connection checks passed; personal mode and the existing connection remained.

Cloud Build `34e4a688-0f63-497f-9bf8-b641cb425fb0` succeeded. Image SHA-256:
`dfa99e89310fecb1ef8dabe64522bf4052e9312c2659395b9a780e31c55104ff`.
Revision `healthops-personal-api-writer-fix-1109` receives 100% of phone-service traffic.
The private deploy configuration pins this image and keeps the writer enabled.

Ruff, changed-file Markdown links and public-content checks passed. Full local Compose,
data-pipeline and model-training suites were not rerun; those implementations did not change.
The existing inference VM keeps its original two-hour stop policy.

## Limits

A citation-set match does not prove that prose accurately attributes every number or describes
statistics correctly. Natural-language generation can still fail and use a clearly labeled
fallback. The new guard does not relax refusals or add support for previously unsupported card
kinds. A single successful cloud replay does not establish a general answer-quality improvement;
visual acceptance and further synthetic semantic evaluation remain separate checks.
