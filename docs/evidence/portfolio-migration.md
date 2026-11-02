# Portfolio migration verification

Verification date: 2026-11-02 UTC. Source snapshot: `ac215_Megatron` commit
`c23565d63b0236f969ea4672893a6b0784226974`. This report concerns local verification of the
curated HealthOps checkout, not a new cloud-model evaluation.

## Functional batches

1. `2ee6991`: preserve standalone history; move application, client and deployment into service directories; record provenance.
2. `ae7c1e0`: import LangGraph routing/tool execution, clarification persistence, guardrails, synthetic import and evaluation harness.
3. `7303517`: import native clarification UI, continuation contracts and the labeled static prototype.
4. `05e3b84`: import deployment templates; parameterize the cloud evaluator's project/region/service; add a no-network command test.
5. Portfolio guide, reproducible synthetic quickstart, historical evaluation evidence and integration CI.

Commits are integration batches, not original implementation dates. See [provenance](../provenance.md).

## Executed checks

| Check | Result | What it establishes |
| --- | --- | --- |
| Initial layout regression selection | 127 passed | Core schemas/tools/assistant/corpus fixtures survive directory migration |
| Backend host tests before updated client import | 307 passed, 60 skipped | Local scripted contracts; database-dependent checks skipped |
| Swift package tests | 60 passed | Native data/continuation/clarification logic, not physical-device acceptance |
| Python/Swift fixture and clarification checks | 19 passed | Shared encoding and structured clarification contract |
| Full Docker API suite | 378 passed, 5 skipped | PostgreSQL migrations, account-scoped requests and scripted-model workflow in isolated test DB |
| Repository/deployment tests | 26 passed | Publication policy, deployment validation, HTTP evaluation and operator-selected cloud project |
| Synthetic HTTP bootstrap | Passed | Two observations inserted; replay counted two duplicates; tools and missing baseline behaved as expected |
| Public fixture upload and replay | 259 inserted; second upload 259 duplicates | Deterministic generated history can be loaded without private cohort data |
| Ruff lint/format and Compose validation | Passed | Imported code and application/test/synthetic/RAG configuration checks |
| Public-content and relative Markdown link checks | Passed | No forbidden local paths/language links or missing local Markdown targets |

The full integration command used a fresh Compose project:

```bash
docker compose -p healthops-portfolio-verify --profile test build test
docker compose -p healthops-portfolio-verify --profile test run --rm test
```

The test database was ephemeral and named `healthops_test`. The bootstrap used a disposable
API container on that same isolated network; no personal-health database or cloud service
was involved. Credential material remained local and was removed after cleanup. The native
Swift build used a scratch directory outside Desktop.

For root-level repository tests, use `uv run --directory src/api-service python -m pytest -q
../../scripts/tests`; invoking the pytest entry point from the root did not resolve the package
in this checkout. Lint/format outside the API directory explicitly uses
`--config src/api-service/pyproject.toml` to retain the same rules.

## Limits

No GPU or paid cloud resource was started. No live Qwen inference, new RAG download/embedding,
physical-iPhone acceptance, Apple signing or new accuracy measurement was performed for this
migration. Scripted-model test success is not real-model answer accuracy. Hosted GitHub Actions
execution is separate from the local checks recorded above. The exact three-account cohort and
private freeze artifacts are not bundled; the small public fixture is a different dataset.
The historical V100 report preserves both measured failures and routing-only RAG limitations.
