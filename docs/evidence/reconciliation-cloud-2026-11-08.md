# Reconciliation Cloud Run rollout — November 8, 2026 UTC

This rollout follows the local integration at API source commit `d003647`. The owner authorized
cloud deployment and retention of one model VM with a two-hour automatic STOP. It uses only the
separate synthetic Cloud SQL database; the personal-history VM and its disk were not queried.

## Deployment and reproducibility

- L4 allocation failed in `us-east4-c` and `us-east4-a`. One `n1-standard-4` / V100 VM allocated
  in `us-central1-a`: `healthops-reconcile-v100-1107`, private model endpoint `10.128.0.16:11434`.
- The clean DLVM image was `common-cu129-ubuntu-2204-nvidia-580-v20261029`. Its open kernel
  module was replaced with the matching proprietary 580 module for kernel `6.8.0-1070-gcp`.
  Observed GPU: Tesla V100-SXM2-16GB, driver 580.178.04, 16,384 MiB.
- Ollama 0.35.1 uses the checked-in CUDA 12 override. Initial startup nevertheless discovered
  CPU only; container recreation restored CUDA discovery. Debug logging was then removed and
  GPU offload verified again before grading. The interrupted CPU attempt is not a scored run.
- Qwen3-8B Q4_K_M digest:
  `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
  `/api/ps` confirmed `size_vram == size == 9969650892` and context length 32,768.
  Temperature is zero and thinking is disabled. This differs from the local Metal / Ollama 0.35.0 run.
- Reconciliation uses CPU inside Cloud Run with the exact pinned w14 artifact from
  [reconcile-model.json](../../src/api-service/reconcile-model.json); it is not executed by Qwen.
- Build `2b8dea02-5e0c-4d80-899d-e09e0fa84ede` succeeded. Immutable API image:
  `us-east4-docker.pkg.dev/ac215-project-509705/healthops/api@sha256:f08203b67ba704a22e5c0a6b1535d25e6397dcf07c61aeaabc3c0b625e5012c7`.
  Source was exported from committed API code, excluding unrelated working-tree edits and local files.
- The build config now uses explicit Python steps because the slim Cloud SDK image lacks standalone
  `python3`. A conditional bucket grant allows the build identity to read only this model artifact
  prefix. The runtime uses bundled weights and requires no GCS key.

## Synthetic data and evaluation boundary

The checksum-validated v0.2 import inserted **16,276 observations, zero duplicates**, using the
existing authenticated batch ingestion API. Three v0.1 accounts were explicitly retained after
validating their completed import report. The isolated database now has **six synthetic accounts**;
this evaluation selects only the three version-derived v0.2 identities. No data was overwritten.

Evaluation ran on the tagged revision `healthops-ms3-api-reconcile-eval-1107`, with zero default
traffic and date overrides enabled only for frozen questions. Gold uses the pinned CPU model
and the same selected synthetic accounts through a private IAP SQL tunnel. The existing rubric
is unchanged: scope, evidence, cards, verified claims, completeness, insufficiency warnings,
no invented readings, stated intervals and matching attribution all remain required.

The set has 42 scenarios / 45 turns: 14 families across Apple-only, paired and Fitbit-only users.
It is a development diagnostic of orchestration and tool grounding, not a measurement of real
physiological accuracy or independent model generalization.

## Observed diagnostic results

| Check | Result |
| --- | --- |
| End-to-end scenarios | **41/42 (97.6%)** |
| Conversation turns | **44/45 (97.8%)** |
| Expected route | 45/45 |
| Required tool, scope and gold evidence | 30/30 execution turns |
| Clarification continuation and required missing fields | 6/6 clarification turns |
| Required insufficiency red flag | 22/22 applicable turns |
| No invented number when no reading | 8/8 applicable turns |
| Interval in final answer | 21/22 applicable turns |
| Attribution matches card in final answer | 29/30 applicable turns |

The single failure is `synthetic-00001 / three_day_history`, also seen locally. For May 16,
the model queried the right metric/date and obtained matching evidence. Its first findings
included invalid claims; one correction removed the false missing-value claim but retained
`reference e1`, which is not valid for a reconciliation card. The server returned an **unverified**
fallback with insufficiency warnings and retained evidence cards. The fallback does not render
the estimated interval or attribution, so those rubric columns correctly fail. No invalid claim
was promoted to a verified answer. Deployment does not resolve this known finalization failure.

The raw GPU run is ignored under `src/api-service/.local/eval/reconcile-v02/cloud-v100-gpu-1107/`.
An earlier interrupted CPU startup attempt remains separate and is not counted. No prompts,
questions or acceptance criteria were changed between the final local run and this cloud run.
The previous 99-scenario general suite was not rerun in this rollout; these numbers describe the
new reconciliation suite only.

## Additional live-revision probes

These probes are separate from the frozen 42-scenario suite; failures were retained, not replaced
by alternate wording:

| Probe | Observed result |
| --- | --- |
| Identity, reconciliation reading, no-reading null estimate and disabled date override | Passed for all three v0.2 accounts |
| Direct daily-summary endpoint vs selected-account database gold | 3/3 exact matches |
| "Show my recorded steps on DATE, separately for every channel with records." | 0/3 selected the expected daily-summary tool; all routed to availability |
| "How many steps did I take on DATE? Give the step count separately for each channel." | 1/3 selected daily_summary and completed; two routed to availability, one also ended unverified |
| Resting-heart-rate definition using the reference library | lookup_reference succeeded; verified and complete answer |
| Request without Cloud Run IAM identity | HTTP 403; no public IAM binding |

The numeric-query probes expose an intent-routing weakness: evidence validity and coverage relative
to the routed request do not prove that the router preserved the original question. These are model
behavior failures, not missing SQL records. They remain follow-up work; no prompt or acceptance
criterion was changed to hide them. This is a synthetic development rollout, not production
readiness or a claim of near-perfect general question answering.

## Build and local checks

All 28 repository-script tests passed. The new build regression executes the YAML's actual Python
steps against temporary fixture weights, verifies the manifest copy, then tampers with the bytes
and asserts checksum rejection. This validates the build guard without accessing GCS or real data.
Cloud Build then succeeded against the real pinned artifacts. Changed Python lint/format, public
content policy, whitespace and changed Markdown relative-link targets passed. Full API, Docker
and Swift suites were run in the preceding local integration; they were not repeated for this
build-config/documentation-only increment.

## Promoted service

The canonical service now routes **100%** of default traffic to
`healthops-ms3-api-reconcile-live-1107`, using the immutable image above. The normal revision
has `HEALTHOPS_ASSISTANT_ALLOW_TODAY_OVERRIDE=false`. The temporary evaluation tag and revision were removed
after testing. Previous non-evaluation rollback revisions were retained.

Post-promotion acceptance through the canonical service URL confirmed readiness, HTTP 422 for
a frozen-date override, and a verified, complete reconciliation answer for the paired account's
explicit August 7 steps request. The private deployment config now records the current image
and private V100 endpoint. Existing IAM protection and account-token authentication remain.

## Lifecycle and limits

The retained VM has `maxRunDuration=7200s`, action STOP, and automatic restart disabled. Its model
files persist on disk. After automatic stop, Cloud Run and SQL can still serve deterministic data
endpoints, but Qwen-dependent answers need the VM restarted. Restart capacity is not guaranteed.
The V100 is in `us-central1` while API/SQL remain in `us-east4`; latency and inter-region networking
costs differ from a colocated deployment. Stopped disks and running Cloud SQL remain billable.

Cloud Run remains IAM protected and also requires an application account token. The iOS client
still needs a separate IAM access solution; changing its base URL alone does not implement cloud
login. Synthetic-trained attribution, defaulted context, sleep alignment and interval calibration
limitations from [the tool documentation](../reconciliation.md) remain unchanged.
