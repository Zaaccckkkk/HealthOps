# Repository instructions

## Publication and local learning notes

- Keep all publishable code, comments, documentation, configuration and filenames in English.
- Preserve Chinese teaching notes locally under `docs/learning/`. That directory is ignored
  by Git and excluded from Docker; never stage, force-add, commit or publish it.
- Do not link public documentation to local-only notes. Update local learning notes when
  implementation changes, including real file paths, tests and known limitations.
- Never commit credentials, real health records or local artifacts from `.local/` or `.env`.
  The cloud development database holds one account with the owner's real HealthKit history;
  never copy its values into fixtures, evaluation sets, examples or documentation.
- Before handoff, run `python3 scripts/check_public_content.py` and inspect the staged diff.
  Ignore rules alone do not protect files that were already tracked.

## Layout and scope

- The implemented application is in `services/ingestion-api/src/healthops/` (API, tools, assistant); the iOS adapter is in `ios/HealthOpsUploader/` (Swift sources English, no tokens, Team IDs or health exports).
- Keep ingestion database migrations in `services/ingestion-api/migrations/`.
- Container definitions belong in `infra/docker/`; run Compose and uv from the repo root.
- Root `pyproject.toml` and `uv.lock` currently describe one Python package, not a completed
  multi-service workspace. Do not create empty runnable services or claim planned work exists.
- Keep service-owned schemas beside their consumers until a shared versioned contract is needed.
- Do not change API behavior or migration revisions solely to reorganize files.

## Verification

- Unit tests: `uv run pytest -q tests/test_schema.py tests/test_tools.py tests/test_period_comparison.py tests/test_assistant.py tests/test_assistant_model.py tests/test_assistant_toolbox.py tests/test_assistant_cards.py tests/test_assistant_limits.py tests/test_assistant_fixtures.py tests/test_eval_sets.py tests/test_ios_contract.py tests/test_public_content.py`.
- iOS adapter: `cd ios/HealthOpsUploader && swift test`; build with `xcodebuild` using a DerivedData path outside the synced Desktop folder (see its README). Tests and fixtures are Simulator-only with synthetic data; device builds are
  signed with a locally supplied team and never add real health data or Team IDs to the repository.
- Full integration suite: `docker compose --profile test build test`, then
  `docker compose --profile test run --rm test` using only the isolated synthetic test DB.
- Check lint/format, Markdown links and public-content policy. Report checks not run.
- No cloud provisioning, real health-data uploads, git commits or pushes without user authority.
  `scripts/cloud/vm.sh` refuses billable actions without `--yes`; never pass it on the user's behalf.
- Cloud files: `infra/cloud/` and `scripts/cloud/`; the filled `.env.cloud` and `.local/cloud/` never enter Git.
- Assistant evaluation (`scripts/eval_assistant.py`) runs only against synthetic users; raw outputs stay in `.local/eval/`. Report model results as observed; never weaken tests to pass.
