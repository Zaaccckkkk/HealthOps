# Evidence that the containerized pipeline works end to end

For the latest portfolio import and its local checks, see the
[Milestone 3 synchronization record](milestone3-sync-20261112.md).
The records below retain their original run dates and environments.

Milestone 2 deliverable 2: "Evidence it works end-to-end (logs + a small sample input →
output artifact)". One row per container. Every log here is the unedited output of the command
shown at the top of the file (only blank lines and Docker build progress lines are removed);
every sample is synthetic or public data.

| Container (`src/<service>/`) | Role in the pipeline | Run log | Sample input → output |
|---|---|---|---|
| `api-service` + `db` (`pgvector/pgvector:pg17`) | Ingestion API, deterministic tools, assistant, migrations | [api-compose-up.log](api-compose-up.log): `docker compose up -d --build api` from an empty volume, the four Alembic revisions running, the API becoming healthy, and what the run left in the database | [api-sample/](api-sample/): `0-upload-request.json` (the committed synthetic batch) → `1-upload-response.json` (2 inserted) → `2-replay-response.json` (same batch again: 2 duplicates, 0 inserted) → `3-history-response.json` → `4-availability-response.json`, `5-daily-summary-response.json`, `6-period-comparison-response.json` (the three tools over that data). [api-smoke-run.log](api-smoke-run.log): `scripts/smoke.py` doing the same through the client script. |
| `rag-ingest` (same image as the API, Compose profile `rag`) | RAG pipeline: fetch and verify sources, chunk, embed, store in pgvector | [rag-ingest-run.log](rag-ingest-run.log): `make rag` into the empty database; one line per source with its licence and quotation limit, totals at the end | Input: [`knowledge/sources.yaml`](../../knowledge/sources.yaml) and [`knowledge/own/`](../../knowledge/own/). Output: the `reference_chunks` count at the end of `api-compose-up.log`, and one complete retrieval (question → query → passages → verified answer) in [../rag-retrieval-example.md](../rag-retrieval-example.md) with the raw response in [`src/api-service/examples/reference-lookup.synthetic.json`](../../src/api-service/examples/reference-lookup.synthetic.json) |
| `test` (same image, Compose profile `test`, ephemeral `test-db`) | Application test suite inside the container against a real PostgreSQL | [docker-test-suite.log](docker-test-suite.log): `docker compose --profile test run --rm test` | The suite's own fixtures (synthetic users and records created and checked in the test database) |
| `synthea-generator` | Synthetic patients (clinical background), first step of the data pipeline | [pipeline-run-20261019.log](pipeline-run-20261019.log): the `make run` that built `raw-v1`, `canonical-v0.1` and `synthetic-v0.1` (each container logs its `INPUT_DATA_VERSION`, `OUTPUT_DATA_VERSION` and Git commit, then exits with code 0); [pipeline-rerun-20261021.log](pipeline-rerun-20261021.log): `make run` again on the committed versions; every container logs its `INPUT_DATA_VERSION`, `OUTPUT_DATA_VERSION` and Git commit, finds its version already built, changes nothing and exits with code 0 | `data/manifests/raw-v1.yaml` (Synthea parameters and file hashes); the sample user's Synthea profile in [data-sample/user.csv](data-sample/user.csv) |
| `data-collector` | Downloads NetHealth and BIDSleep, verifies every file against the publisher's checksum | same pipeline logs ([pipeline-run-20261019.log](pipeline-run-20261019.log)) | Input: the public datasets listed in [`data/README.md`](../../data/README.md). Output: `data/manifests/raw-v1.yaml` (every file with its publisher checksum and SHA-256) |
| `data-processor` | Raw → canonical parquet | same pipeline logs ([pipeline-run-20261019.log](pipeline-run-20261019.log)) | Output: `data/manifests/canonical-v0.1.yaml` (row counts per source and metric, file hashes). Canonical rows are real participants' records and stay in DVC, never in Git; the synthetic sample below uses the same canonical schema |
| `data-synthesizer` | Canonical → hybrid synthetic cohort (statistical generator, `qwen3:4b` for event labels only) | same pipeline logs ([pipeline-run-20261019.log](pipeline-run-20261019.log)) | [data-sample/](data-sample/README.md): one paired synthetic user over 7 days, profile and true daily state (input) next to the Apple Watch rows, Fitbit rows and missing-data events generated from them (output); `data/manifests/synthetic-v0.1.yaml` |

How the application rows were produced (repository root, Docker Compose v2, an Ollama on the
host with `nomic-embed-text`): a separate Compose project name was used so the run starts from an
empty PostgreSQL volume and the local demo database is untouched; the project and its volume are
removed afterwards. The commands are the ones printed in each log.
