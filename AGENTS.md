# Repository instructions

## Publication and local learning notes

- Keep all publishable code, comments, documentation, configuration and filenames in English.
- Preserve Chinese teaching notes locally under `docs/learning/`. That directory is ignored
  by Git and excluded from Docker; never stage, force-add, commit or publish it.
- Do not link public documentation to local-only notes. Update local learning notes when
  implementation changes, including real file paths, tests and known limitations.
- Never commit credentials, real health records or local artifacts from `.local/` or `.env`.
- Before handoff, run `python3 scripts/check_public_content.py` and inspect the staged diff.
  Ignore rules alone do not protect files that were already tracked.

## Layout and scope

- The implemented application is in `services/ingestion-api/src/healthops/`.
- Keep ingestion database migrations in `services/ingestion-api/migrations/`.
- Container definitions belong in `infra/docker/`; run Compose and uv from the repo root.
- Root `pyproject.toml` and `uv.lock` currently describe one Python package, not a completed
  multi-service workspace. Do not create empty runnable services or claim planned work exists.
- Keep service-owned schemas beside their consumers until a shared versioned contract is needed.
- Do not change API behavior or migration revisions solely to reorganize files.

## Verification

- Unit tests: `uv run pytest -q tests/test_schema.py tests/test_tools.py tests/test_public_content.py`.
- Full integration suite: `docker compose --profile test build test`, then
  `docker compose --profile test run --rm test` using only the isolated synthetic test DB.
- Check lint/format, Markdown links and public-content policy. Report checks not run.
- No cloud provisioning, real health-data uploads, git commits or pushes without user authority.
