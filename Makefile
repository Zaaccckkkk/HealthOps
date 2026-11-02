HOST_UID := $(shell id -u)
HOST_GID := $(shell id -g)
export HOST_UID HOST_GID

.PHONY: app rag test
app:
	docker compose up -d --build api

rag:
	mkdir -p src/api-service/.local/knowledge/raw
	docker compose --profile rag run --rm --build rag-ingest

test:
	docker compose --profile test build test
	docker compose --profile test run --rm test
