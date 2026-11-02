HOST_UID := $(shell id -u)
HOST_GID := $(shell id -g)
export HOST_UID HOST_GID

.PHONY: app rag test
app:
	docker compose up -d --build api
	@for i in $$(seq 60); do \
	  docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready')" >/dev/null 2>&1 && exit 0; \
	  sleep 2; \
	done; echo "API not ready; inspect docker compose logs api"; exit 1

rag:
	mkdir -p src/api-service/.local/knowledge/raw
	docker compose --profile rag run --rm --build rag-ingest

test:
	docker compose --profile test build test
	docker compose --profile test run --rm test

.PHONY: synthetic-import
synthetic-import:
	mkdir -p src/api-service/.local/synthetic-import
	docker compose --profile synthetic run --rm --build synthetic-import
