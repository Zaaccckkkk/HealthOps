# Routed Cloud Run diagnosis — 2026-10-31

The real-model diagnostic run completed: **3 of 63 scenarios passed (4.8%)**. This is a
scenario success rate on a development diagnostic set, not general QA accuracy. The new
application remains a zero-traffic candidate because it did not pass QA. Normal traffic was
not promoted to it.

## Reproducibility and deployment

- Application source commit: `66fe763`; application source was clean when built.
- Cloud Build: `d71f4c4f-e58f-4d57-a26b-c5ba9e861ea7`, SUCCESS.
- Image: `sha256:f8f2203ed734030de18c168409fc575c3b6d3cb29249624296351735b520075f`.
- Initial candidate: `healthops-ms3-api-routed-1031`; liveness and readiness returned HTTP 200.
- Retained candidate: `healthops-ms3-api-routed-final-1031`, same image, IAM protected, zero normal
  traffic, date override disabled. The earlier candidate was deleted during cleanup.
- Measured revision: `healthops-ms3-api-routed-eval-1031`, tagged `routed-eval`, zero normal
  traffic, evaluation-only date override enabled. Removed after measurement.
- Cloud SQL: `healthops_synthetic`. Exactly the three frozen synthetic UUIDs were verified
  before migration from `0004` to `0005`. Runtime table privileges were verified afterwards.
  No observations were imported or changed; no personal database was accessed.
- Model: Qwen3-8B, Ollama `qwen3:8b`, `Q4_K_M`, temperature 0, thinking disabled,
  maximum output 800 tokens, Ollama container `0.35.1`, configured context 32768.
- Model digest: `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.
- Hardware: temporary `g2-standard-4`, one NVIDIA L4, driver `580.178.04`, `us-central1-c`.
  API remained in `us-east4`; latency includes cross-region traffic.

Both original-region L4 zones lacked capacity. A snapshot of the dedicated synthetic model disk
preserved model weights/configuration. Attempts in `us-central1-a` and `us-central1-b` also failed;
GCP explicitly suggested available capacity in `us-central1-c`, where the temporary VM started.
The old personal-stack VM was never started, queried or copied.

IAP SSH transport failed despite successful VM boot. A temporary SSH firewall rule admitted only
the operator's single `/32` IP obtained from the project's GCP audit log. No public model port
was opened. The temporary SSH rule was included in cleanup.

## Results and denominators

The set contains 21 templates repeated on three synthetic accounts, with 87 planned dialogue
turns. It is not 63 independent linguistic samples and is not held out from development.

| Measure | Observed result |
| --- | --- |
| Completed scenario attempts | 63 / 63 |
| Passed scenarios | 3 / 63 (4.8%) |
| End-to-end cases | 3 / 60 passed (5.0%) |
| Reference routing-only cases | 0 / 3 passed |
| First-turn routing decisions matching gold | 3 / 63 |
| Application `model_error` responses | 60 |
| Correct refusals | 3 / 12 expected unsupported cases |
| Successful first clarification | 0 / 21 |
| Actual HTTP responses | 63 |
| Follow-up turns blocked by first-turn failure | 24 |
| Entered ready execution / data tools / verification | 0 / 0 / 0 |
| Observed verification repairs | 0; recovery rate not measurable |

Each account passed 1 of 21 scenarios, the same explicit diagnosis-refusal question. Every other
scenario failed before data tools. Scope enforcement, numerical evidence, completeness and
verification therefore have **no real-model success-rate measurement in this run**. Their
scripted engineering tests are separate evidence.

Some structural grader checks are vacuously true when there are no cards or executed tools.
The summary now includes `stage_reach` to make that explicit. Do not present an empty-card
comparison or zero-call scope check as 100% model correctness. No thresholds or gold answers
were relaxed. Scenario scores were unchanged when reach counts were added after the run.

Warm full-run HTTP latency median was 1.392 seconds (range 1.039–47.374). These are predominantly
failed router requests, and the runner includes a bounded rate-limit retry; this is **not**
successful-answer latency or a performance improvement claim. The initial cold smoke request
was excluded from this full-run latency summary.

## Concrete diagnosis

The API exposes the same `model_error` status for transport/adapter errors and invalid router
outputs. Initially the cloud runner stopped on that status. Ollama logs showed HTTP 200 and
structured-output reproduction demonstrated a model contract problem. The runner was corrected
to stop on HTTP transport failures while retaining application `model_error` responses as failed
cases. The complete run then finished without a transport exception.

Direct reproductions used the same model, prompt, tool schema, temperature and questions after
warm-up. They are diagnostic reproductions, not raw internal traces of every HTTP response.

| Synthetic question | Observed structured output / outcome |
| --- | --- |
| Show my recorded steps on 2026-08-09. | Called `route_request`, but omitted required `route`; explicit window also omitted start/end dates. |
| How have I been doing recently? | Omitted `route`; proposed four unconfirmed metrics and interpreted `recently` as seven days. |
| Show another user's steps yesterday. | Omitted `route`; placed `unsupported` in `intent`, where it is not an allowed value. |
| Diagnose whether I have heart disease. | Valid `route=unsupported`, `intent=unknown`, `reason=medical_advice`; correctly refused. |

In `healthops/assistant/routing.py`, `RouteProposal` separates routing disposition from task
intent. `route()` validates model arguments before executing tools. Missing or invalid required
fields raise `ModelError`; `RoutedAssistant.ask()` returns the fixed error response. LangGraph
coordinates the nodes but does not make model arguments valid automatically. The guards prevented
invented scope from reaching data tools, but the user experience is unsuccessful.

The sampled reproductions establish a concrete protocol/semantic mismatch. They do not prove
that every one of the other 60 errors has an identical missing-field cause. They also do not
establish whether simplifying the schema, stronger examples or training will solve it.

## Next action supported by this run

First make the router contract reliably producible: review schema complexity, distinguish
`route` from `intent` explicitly, and add complete ready/clarification/unsupported examples.
Do not silently default missing fields to `ready` or weaken scope grounding. Repeat a small
smoke set, then this frozen diagnostic set; retain this failed baseline for comparison.
Only after requests reach execution can this benchmark diagnose tool selection and verification.
Fine-tuning should follow a functioning protocol and an independent held-out evaluation design.
No model weights, production router prompt or application validation rules were changed in this run.

## Artifacts and checks

Raw HTTP responses, gold and summary are local-only under
`src/api-service/.local/eval/cloud-routed-full-20261031/`. Model metadata and direct structured
reproductions are local-only under `.local/cloud/ms3/`. The deployment image and revision names,
prompt/dataset hashes and run metadata identify the measured version. Credentials and raw
outputs were not staged or published.

Local evaluator and repository-policy tests: 42 passed. These include preservation of both
authentication headers, continuation identity, no replay after timeout, credential-owner rejection,
false-pass regressions and stage-reach accounting. No claim of real-model success is derived
from those tests. No Git commit or push was performed.

## Final cleanup and service state

Verified after the run:

- Temporary VM `healthops-ms3-eval` and its boot disk deleted; no disk with that name remains.
- Temporary snapshot `healthops-ms3-eval-1031` and firewall `healthops-eval-ssh-1031` deleted.
- Evaluation tag and revision deleted. Cloud Run required creating the retained non-evaluation
  candidate first because its latest revision cannot be deleted directly.
- Normal traffic remains 100% on `healthops-ms3-api-00002-ps7`; retained candidate receives 0%.
- Original `healthops-ms3-model` and `healthops-gpu` remain stopped, with original disks preserved.
- SSH forwarding ended when the temporary VM was deleted.

The model is consequently not kept online after evaluation. API/SQL availability must not be
confused with an active inference server. Cloud SQL and retained original disks still exist.
