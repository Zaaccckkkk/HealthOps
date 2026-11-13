## Contributions


| Area | Work item | Where | Owner(s) |
|---|---|---|---|
| Project | Course template migration (`src/<service>/` layout, Makefile, root Compose) | `docker-compose.yml`, `Makefile`, `src/` | Helena Li |
| Project | One-command setup: `make app` (application) and `make all` (first-time setup: DVC pull → data → RAG → app); README quick start for a new machine and "Running the pipelines" | `Makefile`, `README.md` | Huihan Cui, Helena Li |
| Data | Dataset selection and licence review (NetHealth Fitbit, BIDSleep Apple Watch, Synthea) | `data/README.md` | Huihan Cui, Helena Li, Zhentian Wu |
| Data | Data pipeline orchestration: Compose `data` profile, `make run`, data versions in `.env`, every container logging `INPUT_DATA_VERSION` / `OUTPUT_DATA_VERSION` and the Git commit | `docker-compose.yml` (profile `data`), `Makefile`, `.env.example` | Helena Li |
| Data | `synthea-generator` container (pinned Synthea run, patient tables) | `src/synthea-generator/` | Huihan Cui |
| Data | `data-collector` container (checksum-verified downloads, raw manifest) | `src/data-collector/` | Huihan Cui |
| Data | `data-processor` container (raw → canonical schema, time zones, sleep dating, validation) | `src/data-processor/` | Huihan Cui|
| Data | Canonical schema and shared versioning helpers, kept identical across the data services | `src/*/…/schema.py`, `src/*/…/versioning.py` | Helena Li |
| Data | `data-synthesizer`: calibration on real data, baselines, day-to-day trajectory | `src/data-synthesizer/synthesizer/{calibrate,baseline,trajectory}.py` | Huihan Cui |
| Data | `data-synthesizer`: LLM life-event context with `qwen3:4b`, JSON-schema constraints, caching | `src/data-synthesizer/synthesizer/context.py`, `configs/context_generation.yaml` | Helena Li |
| Data | `data-synthesizer`: Synthea clinical profiles, Apple Watch and Fitbit observation models, missingness | `src/data-synthesizer/synthesizer/{clinical,observe,missingness}.py` | Huihan Cui|
| Data | Generator configuration: cohort and baselines, trajectory effects, device models, missingness, calibration settings | `configs/*.yaml` | Huihan Cui|
| Data | Data pipeline tests (NetHealth mapping edge cases, offline end-to-end synthesizer run) | `src/data-processor/tests/`, `src/data-synthesizer/tests/` | Helena Li |
| Data | Versioning: manifests, immutable versions, DVC pointers and local remote, `make dvc-track` / `dvc-pull`; built and committed `raw-v1`, `canonical-v0.1`, `synthetic-v0.1` (5000 users) | `data/manifests/`, `data/**/*.dvc`, `.dvc/config`, `.gitignore`, `scripts/dvc_track.sh` | Helena Li |
| Data | Data pipeline documentation and design-choice write-up | `data/README.md`, service READMEs, `README.md` ("Data pipeline and versioning"), `docs/architecture.md` (data rows) | Helena Li |
| Data | Pipeline run logs, rerun log, screenshot and sample input → output artifacts | `docs/evidence/pipeline-run-*.log`, `docs/evidence/pipeline-rerun-*.log`, `docs/evidence/data-sample/`, `docs/screenshots/data-pipeline-run.png` | Huihan Cui |
| Application | Ingestion API: canonical observation contract, token auth, replay-safe deduplication, conflict reporting, Alembic migrations | `src/api-service/healthops/{main,schemas,repository,auth,models}.py`, `migrations/` | Zhentian Wu |
| Application | Deterministic history tools (availability, daily summary, period comparison) with explicit limits | `src/api-service/healthops/tools/` | Zhentian Wu |
| Application | Assistant: tool-using orchestration, cards and claims verification, fixed refusals, budgets and rate limits | `src/api-service/healthops/assistant/` | Zhentian Wu |
| Application | Google Health (Fitbit) connector: server-side OAuth, encrypted refresh tokens, windowed sync | `src/api-service/healthops/connections.py`, `connectors/` | Zhentian Wu |
| LLM / RAG | Reference library: licence-checked source manifest, fetch and hash verification, chunking, `nomic-embed-text` embeddings, pgvector store, `lookup_reference` tool, licence-aware verbatim quotation | `knowledge/`, `src/api-service/healthops/knowledge/`, `scripts/knowledge/`, migration `0004` | Zhentian Wu |
| LLM / RAG | `rag-ingest` container and `make rag`; retrieval example and ingest logs | `docker-compose.yml`, `docs/rag-retrieval-example.md`, `src/api-service/examples/` | Zhentian Wu |
| LLM / RAG | Assistant evaluation: synthetic question sets, evaluation script, observed results on the Mac and the L4 VM | `src/api-service/eval/`, `scripts/eval_assistant.py` | Zhentian Wu |
| Frontend | iOS app: HealthKit read access, consent, replay-safe sync engine, Overview charts, Ask tab, Setup tab, Google Health connection, Swift tests | `src/frontend-app/HealthOpsUploader/` | Zhentian Wu |
| Frontend | Application mock-up: wireframe, Simulator screenshots, changes since Milestone 1 | `docs/app-mockup/` | Zhentian Wu |
| Infrastructure | Local Compose stack (API, pgvector database, isolated test services) and the container test suite | `docker-compose.yml`, `src/api-service/tests/`, `Dockerfile` | Zhentian Wu |
| Infrastructure | Single-GPU cloud VM: Compose with Caddy and Ollama, `vm.sh` / `remote.sh`, auto-stop, IAP-only SSH | `src/deployment/`, `docs/cloud-dev-deployment.md` | Zhentian Wu |
| Infrastructure | Running-instance screenshots and end-to-end evidence for the application containers | `docs/screenshots/`, `docs/evidence/` (api, rag, test rows) | Zhentian Wu |
| Quality | Repository policy: public-content check, CI workflow, synthetic-data-only rule, English-only public text | `scripts/check_public_content.py`, `.github/workflows/`, `AGENTS.md` | Zhentian Wu |
| Documentation | API contract, architecture, deployment notes, README deliverables index | `docs/api-contract.md`, `docs/architecture.md`, `README.md` | Zhentian Wu |
