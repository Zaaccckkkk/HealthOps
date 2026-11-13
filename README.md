# HealthOps

**A multi-device health-data assistant that asks for missing context and answers from verified evidence.**

<p align="center">
  <a href="docs/demo/app-demo.mp4">
    <img src="docs/demo/app-demo-preview.gif" width="300" alt="HealthOps app: select sleep duration and active energy, specify the past seven days, and inspect the answer and evidence">
  </a>
</p>
<p align="center">
  <a href="docs/demo/app-demo.mp4"><strong>Watch the full 25-second demo</strong></a><br>
  <sub>November 12, 2026 · iOS app · Clarification, answers and evidence</sub>
</p>

HealthOps combines Apple HealthKit and Google Health / Fitbit records with a Qwen3-8B assistant.
Recorded-history summaries come from deterministic Python tools; a pgvector reference library
supports sourced definitions. A trained Transformer provides cross-device reconciliation estimates
when its pinned weights are configured. Qwen selects tools and can write an explanation from the
resulting evidence, while Python controls identity, request scope, structured claims and display
formatting. Generated prose is distinguished from verified evidence.

Built with **FastAPI, LangGraph, PostgreSQL + pgvector, SwiftUI, PyTorch, Ollama and Google Cloud**.
This is an engineering prototype with fixed synthetic evaluations, not a clinical product or
an always-on public service.

## See the interaction

```text
User:       How have I been doing recently?
Assistant:  Requests the missing time range and metrics.
User:       Past 7 days; steps and sleep duration.
Assistant:  Resumes the scoped conversation, queries both metrics, validates evidence,
            and reports each requested metric, including missing records.
```

This is an illustrative interaction, not a transcript or a claim about a specific model run.

- **Native app:** [SwiftUI client and build instructions](src/frontend-app/HealthOpsUploader/README.md).
- **UI preview:** [interactive HTML prototype](src/frontend-app/prototype/README.md).
  Run `python3 -m http.server 8765 --bind 127.0.0.1 --directory src/frontend-app/prototype`.
  It uses scripted synthetic examples and does not call the API or model.
- **Recorded native demo:** [watch the 25-second app video](docs/demo/app-demo.mp4).
  Select sleep duration and active energy, specify the past seven days, then inspect the answer
  and evidence. [Recording details and export settings](docs/demo/README.md).

## Design that matters

- **Clarify before querying.** A typed Router distinguishes ready, underspecified and unsupported
  requests. PostgreSQL stores account-bound clarification state with expiry and version checks.
- **One model, dynamic tools.** A LangGraph execution loop lets Qwen select history tools,
  reference lookup or `reconcile_devices`, inspect results and submit `state_findings`.
  Reconciliation reports model estimates, intervals and coverage warnings alongside recorded
  values; it does not establish a device's true accuracy.
- **Check evidence before writing.** Python validates structured claims against evidence cards,
  checks the requested scope and permits one bounded findings correction. Verified templates
  provide an answer before the optional Writer runs.
- **Grounded writing, bounded editing.** Python prepares the evidence and readable units; the same
  Qwen model writes a draft. Draft checks can trigger one Editor revision. Formatting and a final
  sensitive-output check follow, with template fallback for blocked or unusable output. Generated
  prose is labeled separately: the final gate does not revalidate every edited factual claim.
- **Defense in layers.** Input screening, server-injected identity, registered tool schemas,
  execution budgets, reference screening and quote limits constrain the workflow. Personal and
  synthetic uploads are checked at both client and API boundaries. These checks have documented
  limits; they do not establish universal prompt-injection resistance.

![HealthOps assistant pipeline: input guardrail, clarification, tool agent, evidence verification, Writer, one Editor revision and final output guardrail](docs/diagrams/healthops-assistant-pipeline-simple.png)

Cloud Run hosts the API and orchestration; Qwen inference runs on a separate GPU VM.
The reconciliation model runs inside the API when configured. See the
[deployment guide](src/deployment/cloud-run/README.md), [reconciliation contract](docs/reconciliation.md)
and [assistant guardrails](docs/assistant-guardrails.md).

## Observed evaluation

Qwen3-8B Q4_K_M, Ollama 0.35.1, temperature 0, thinking disabled; Cloud Run API,
synthetic Cloud SQL data and a temporary V100 model server. Reported run: **2026-11-02 UTC**.

| Fixed evaluation suite | Scenarios | User turns | Scenario pass rate |
| --- | ---: | ---: | ---: |
| End-to-end diagnostic | 60 | 84 | **59/60 (98.3%)** |
| Interaction suite | 36 | 72 | **32/36 (88.9%)** |
| RAG routing only, excluded from end-to-end scores | 3 | 3 | 3/3 tool selection |

The first two rows total **96 scenarios / 156 turns**. All three total **99 / 159**.
Templates repeat across three synthetic accounts; follow-up replies are authored fixtures.
These are fixed development diagnostics, not held-out production accuracy or clinical validation.
Both suites include multi-turn behavior. The RAG routing checks did not establish successful retrieval.

Known failures: two reproducible empty model answers after correct tool execution; three
availability-versus-summary contract mismatches; SDNN retrieval below the similarity threshold.
See the [full report](docs/evidence/v100-live-evaluation-20261102.md),
[question-set guide](docs/assistant-routed-evaluation.md) and [evaluation index](docs/evidence/README.md).

## Reproduce locally

Start with the [synthetic quickstart](docs/quickstart.md). It needs Docker Compose; live answers
also need host Ollama with `qwen3:8b`. No Google account, private dataset, OAuth credentials or
service-account JSON is needed for the public fixture demo. Python development uses 3.12 + uv.

```bash
make app       # local PostgreSQL, migrations and API
make test      # isolated ephemeral PostgreSQL integration suite
```

The public fixture generator reproduces ingestion, tools and the assistant interface. Replaying
the exact reported three-account scores additionally requires the team's versioned synthetic
cohort and frozen run inputs; those datasets and private artifacts are not published here.
Optional reference ingestion: `make rag` after pulling `nomic-embed-text`; this downloads
manifest-listed third-party sources under their recorded usage terms.

Device reconciliation additionally needs the pinned model artifact and optional inference
extra; follow the [reconciliation setup](docs/reconciliation.md). The versioned data pipeline,
reconciliation training and Writer DPO workflows have separate service guides below.
They are not prerequisites for the public fixture demo.

## Code map

| Component | Start here |
| --- | --- |
| API, identity and observation ingestion | [API service](src/api-service/README.md) |
| Router and persistent clarification | [clarification contract](docs/assistant-clarification.md) |
| Scope, evidence and completeness | [execution guards](docs/assistant-execution-guards.md) |
| Writer, Editor and output checks | [writer implementation](src/api-service/healthops/assistant/narrative.py), [guardrails](docs/assistant-guardrails.md) |
| Device reconciliation and model serving | [reconciliation guide](docs/reconciliation.md) |
| Reference retrieval and content screening | [knowledge library](knowledge/README.md), [safety design](docs/design/reference-safety.md) |
| Native app and interaction design | [frontend](src/frontend-app/README.md), [UX specification](docs/design/assistant-experience.md) |
| Versioned data and synthetic cohort | [data guide](data/README.md), [synthesizer](src/data-synthesizer/README.md) |
| Reconciliation training and ML workflow | [model training](src/model-training/README.md), [Vertex pipeline](src/ml-workflow/README.md) |
| Writer preference training and checkpoints | [Writer DPO guide](src/model-training/writer_dpo/README.md) |
| Cloud Run / Cloud SQL / model VM | [deployment guide](src/deployment/cloud-run/README.md) |
| Monitoring | [dashboards and alerts](src/deployment/monitoring/README.md) |
| Local verification of the synchronized code | [verification record](docs/evidence/milestone3-sync-20261112.md) |

## Authorship and scope

**Application and assistant developed by Zhentian Wu.** My work covers ingestion and
authentication, deterministic analytics, LangGraph orchestration, reference retrieval,
the iOS client, application deployment and evaluation.

HealthOps was developed within the [AC215 team project](https://github.com/HuihanCui/ac215_Megatron).
This repository also includes the team's data generation, processing, versioning and
cross-device model-training work, with contributions from Huihan Cui and Helena Li.
The reconciliation adapter connects trained predictions to the assistant's evidence cards.
See the [contribution table](contributions-table.md) and
[provenance and acknowledgments](docs/provenance.md) for ownership and source history.

Writer DPO training, checkpoint evaluation and registry workflows are included as a separate
experiment. The reported assistant scores above use the base Qwen3-8B model, not a fine-tuned
Writer. Credentials, personal health records, downloaded corpora and private run artifacts
are excluded from Git.
