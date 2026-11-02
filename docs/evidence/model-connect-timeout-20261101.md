# Fast model-connection failure and live retest attempt — 2026-11-01

## Change and verification

Implementation commit: `89c2d36`.
`model.py::model_timeout` separates the default five-second connection/pool limit from the
existing 120-second read/write limit. The overall request deadline remains unchanged. Both
Ollama and OpenAI-compatible adapters classify connection refusal/timeout as
`ModelConnectionError`; routing and execution show the same fixed service-unavailable message
with the existing `model_error` application status. Invalid outputs and generation read timeouts
are not classified as connection failures. No automatic retry was added.

Tests: 378 passed / 5 skipped in the isolated Compose API suite; 25 repository/deployment tests
passed. Eight new parameterized cases check timeout extensions, connection-vs-read failures,
no request replay, validated settings, and routing/execution messages. Mock transports prove
configuration and classification, not elapsed network time. Ruff, whitespace and public-content
checks passed. No frontend contract or frozen evaluation questions changed.

## Real cloud timing

Cloud Build: `934f63a1-b58e-419d-b284-41e96cd438cc`.
Image: `us-east4-docker.pkg.dev/example-healthops-project/healthops/api@sha256:a0270541d0f8bc4dd74a9ac033a3cb7ebe1e254089f2ea798bf94814dcd36c54`.
Build sources were compared byte-for-byte with tracked committed API files. The deployed
revision's resolved image digest matched this artifact.

Evaluation revision: `healthops-ms3-api-connect-eval-1101`, IAM protected, zero default traffic.
The dedicated model VM remained stopped during measurement, using the same private endpoint
as the [previous 120.14-second observation](reference-safety-cloud-20261101.md).

Each account was first verified through `/v1/me`; the timed request then asked for recorded
steps on 2026-08-09 with America/New_York timezone and a frozen evaluation date. Identity
preflight warmed the API, so these timings do not represent cold-start latency.

| Synthetic account | Elapsed seconds | HTTP | Application result |
| --- | --- | --- | --- |
| synthetic-00000 | 5.093 | 200 | model_error |
| synthetic-00003 | 5.138 | 200 | model_error |
| synthetic-00001 | 5.168 | 200 | model_error |

All three answers were exactly: "The model service is temporarily unavailable. Please try
again shortly." No values or citations were invented. These three failure-path measurements
are not a latency distribution, production SLA, successful-generation benchmark, or accuracy
score. A server that accepts connections and then stalls may still reach the longer read or
whole-request timeout.

## Real-model evaluation blocker

The dedicated L4 in us-east4-c could not start. Temporary attempts also failed for capacity:
L4 g2-standard-4 in us-west4-a and us-central1-a; L4 g2-standard-8 in us-east4-c; and T4
n1-standard-4 in us-east1-c, us-central1-f and us-west1-b. Each allocation was constrained to
one GPU with a two-hour automatic STOP. Existing model weights were not changed or re-pulled.

The unchanged 63-scenario / 87-turn diagnostic and 36-scenario / 72-turn interaction set remain
pending. They were not run against an unavailable model or scored as model-answer failures.
No new live Qwen accuracy, RAG relevance, native cloud UI acceptance or fine-tuning result is
claimed. Private timing/freeze artifacts remain under `.local/cloud/ms3/connect-freeze/`.

## Deployment and cleanup

`healthops-ms3-api-connect-candidate-1101` retains the new image under `safety-candidate`, with
frozen-date overrides disabled and zero normal traffic. Normal traffic remains 100% on
`healthops-ms3-api-00002-ps7`; the pre-existing routed candidate remains unchanged. The temporary
connect-eval tag and date-override evaluation revision were removed after measurement.

Both existing GPU VMs were verified TERMINATED. No temporary VM or disk remains. The isolated
Compose test DB was stopped. Cloud SQL continues independently. No real health records, corpus
contents, account grants or database migrations were modified.
