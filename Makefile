# HealthOps shortcuts. Run from the repository root. One command per pipeline:
#   make run        data pipeline in containers (raw -> canonical -> synthetic)
#   make rag        RAG corpus: fetch sources, chunk, embed (host Ollama) and store in pgvector
#   make app        application: PostgreSQL, migrations and the API on 127.0.0.1:8000
#   make train-smoke  reconciliation models on 20 synthetic users (local check before Vertex AI)
#   make train      the same on the whole synthetic version
#   make train-vertex  the same as a Vertex AI custom job (image via Cloud Build; see src/model-training/README.md)
#   make pipeline      Vertex AI Pipeline: prepare -> transformer per window on GPU -> compare (src/ml-workflow)
# First-time setup on a new machine, all three in order:
#   make all        dvc-pull -> run -> rag -> app
# Data versioning:
#   make dvc-track  version every built dataset with DVC and push it to the DVC remote
#   make dvc-pull   fetch the datasets that the committed .dvc pointers describe

# The commit (with -dirty when there are uncommitted changes) is written into every manifest.
GIT_COMMIT := $(shell git describe --always --dirty 2>/dev/null || echo unknown)
HOST_UID := $(shell id -u)
HOST_GID := $(shell id -g)
export GIT_COMMIT HOST_UID HOST_GID
DVC ?= uvx --from "dvc[gs]==3.67.1" dvc

.PHONY: run data rag app all dvc-track dvc-pull synthetic-import train-smoke train train-vertex pipeline

run: data

data:
	docker compose --profile data up --build --attach-dependencies data-synthesizer

# Requires the versioned synthetic parquet files (see docs/synthetic-import.md).
synthetic-import:
	mkdir -p src/api-service/.local/synthetic-import
	docker compose --profile synthetic run --rm --build synthetic-import

train-smoke:
	mkdir -p src/model-training/.local
	TRAIN_USERS=20 TRAIN_SMOKE=true docker compose --profile train run --rm --build model-training

train:
	mkdir -p src/model-training/.local
	docker compose --profile train run --rm --build model-training

train-vertex:
	src/model-training/vertex/submit.sh

# IMAGE is the CUDA build of the training image (see src/ml-workflow/README.md); PIPELINE_ARGS
# e.g. "--smoke --users 200" for the cheap first run, "--windows 14,21,30" for more windows.
pipeline:
	@test -n "$(IMAGE)" || { echo "set IMAGE=<training image with TORCH_VARIANT=cu132>"; exit 1; }
	uv run --directory src/ml-workflow healthops-pipeline --image "$(IMAGE)" --out .local/pipeline.json $(PIPELINE_ARGS)

rag:
	mkdir -p src/api-service/.local/knowledge/raw
	docker compose --profile rag run --rm --build rag-ingest

app:
	docker compose up -d --build api
	@for i in $$(seq 60); do \
	  curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1 && \
	    { echo "API ready: http://127.0.0.1:8000/docs"; exit 0; }; \
	  sleep 2; \
	done; echo "API not ready after 120 s; see: docker compose logs api"; exit 1

# Committed data versions are pulled, never rebuilt under the same names (the run refuses it).
all:
	@$(MAKE) --no-print-directory dvc-pull || { \
	  echo "make all needs the committed data versions: put a copy of healthops-dvc-store next to"; \
	  echo "the repository (see data/README.md), or build new versions with new names in .env"; \
	  echo "and make run, then run make rag and make app."; exit 1; }
	$(MAKE) --no-print-directory run
	$(MAKE) --no-print-directory rag
	$(MAKE) --no-print-directory app

dvc-track:
	DVC="$(DVC)" ./scripts/dvc_track.sh

dvc-pull:
	$(DVC) pull

# Portfolio quickstart compatibility: run only the isolated synthetic test stack.
.PHONY: test
test:
	docker compose --profile test build test
	docker compose --profile test run --rm test
