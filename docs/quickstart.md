# Reproduce with public synthetic fixtures

## Prerequisites

Docker Engine/Desktop with Compose, free loopback ports 8000 and 55432, and a shell in the
repository root. Stop a conflicting development stack first; do not point these commands at
an existing personal-health database. This Compose project is named `healthops-portfolio` and
uses its own named volume. No cloud account or private dataset is required.

For live assistant answers, install and run Ollama on the host, then pull `qwen3:8b`.
The recorded cloud run used Ollama 0.35.1 and the model digest in the evaluation report;
a newly pulled mutable tag does not establish an exact reproduction of that run.
The API can start without Ollama, but questions then report a model-service error.

```bash
ollama pull qwen3:8b
make app
```

`make app` builds the API, starts PostgreSQL, applies migrations and waits for readiness.
Readiness covers the database, not model availability. Compose reaches host Ollama through
`host.docker.internal:11434` (with the host-gateway mapping on Linux).

## Create one synthetic demo account

From the repository root, after `make app` succeeds:

```bash
mkdir -p src/api-service/.local
chmod 700 src/api-service/.local
docker compose run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/src/api-service/.local:/bootstrap" \
  api python -m healthops.cli create-user \
  --name synthetic-portfolio-demo --output /bootstrap/portfolio-demo.json

docker compose run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/src/api-service/.local:/bootstrap:ro" \
  api python scripts/seed_synthetic_user.py \
  http://api:8000 /bootstrap/portfolio-demo.json
```

The first command creates a fresh account and a seven-day bearer token, written only to the
ignored local file (mode 0600). It refuses to overwrite an existing credential file. The second
uploads deterministic synthetic observations through the API. Repeat only the second command
to verify replay-safe deduplication. The demo uses the Compose-internal API hostname and database;
it does not read a personal database or contact Google Health.

The generator is `src/api-service/eval/synthetic_history.py`. Its dates are fixed through
2026-10-02, with deliberate gaps and a genuine zero-step day. Ask explicit dated questions:

- `Show my recorded steps on 2026-10-01.`
- `Show sleep duration and resting heart rate on 2026-10-01. Report missing metrics too.`
- `How have I been doing recently?` followed by an explicit supported date range and metric.

Relative questions use the current date, so they may correctly report no recent records.
The app does not secretly shift the fixture dates. Do not enable a date override on a public
service; frozen-date overrides belong only on isolated evaluation revisions.

Open `http://127.0.0.1:8000/debug/assistant` for the local operator debug page and use your local
credential file's token. Do not publish the token or record it in a demo video. For the actual
phone client, follow the [native app guide](../src/frontend-app/HealthOpsUploader/README.md);
phone networking and Apple signing require local setup. This guide does not prove physical-device
acceptance or an always-on cloud demo.

## Optional reference library

```bash
ollama pull nomic-embed-text
make rag
```

This downloads checksum-verified sources listed in `knowledge/sources.yaml`, extracts chunks,
embeds them and writes pgvector reference rows. Downloaded source texts stay ignored. Query
routing and retrieval recall are separate: a correctly selected reference tool can return no
matching passage. Source quotation limits are enforced by the application.

## Verify without live model inference

```bash
make test
uv sync --frozen --extra synthetic --directory src/api-service
uv run --frozen --extra synthetic --directory src/api-service python -m pytest -q
uv run --frozen --extra synthetic --directory src/api-service python -m pytest -q ../../scripts/tests
swift test --package-path src/frontend-app/HealthOpsUploader --scratch-path /tmp/healthops-swift
python3 scripts/check_public_content.py
```

`make test` uses an ephemeral `healthops_test` database, isolated from the application DB.
Host-only tests skip database-dependent cases when no dedicated test URL is configured;
Swift requires the Apple toolchain. Scripted model responses test contracts, not Qwen accuracy.
See [verification](evidence/portfolio-migration.md) for what was actually run.

The exact three-account model evaluation is an advanced path requiring the upstream versioned
synthetic cohort. See [synthetic import](synthetic-import.md) and [evaluation](assistant-routed-evaluation.md).
It is not interchangeable with the one-account public fixture above.

To stop local services while keeping demo data: `docker compose --profile test down`.
Removing the project's named volume would erase its demo data; it is not needed for normal shutdown.
