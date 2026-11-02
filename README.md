# HealthOps

A multi-device health-data assistant. This repository is being refreshed in explicit functional
migration batches from the AC215 team project; commits record the integration date, not the
original implementation date. See [provenance](docs/provenance.md).

Application: `src/api-service/`; native client: `src/frontend-app/HealthOpsUploader/`;
deployment: `src/deployment/`; reference corpus manifest: `knowledge/`.
Run `make app` from the root; run Python commands with `uv` inside `src/api-service/`.
`make test` uses a separate ephemeral PostgreSQL database.

Private datasets, credentials, model files and local learning notes are not included.
The final portfolio guide follows after the application, client and deployment imports.
