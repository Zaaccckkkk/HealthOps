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

- The repository follows the course template (dlops-io/project-setup-template, milestone2):
  every container is `src/<service>/` with its own `Dockerfile`, `pyproject.toml` + `uv.lock`,
  `README.md`, source and `tests/`. Run Compose and `make` from the repo root; run `uv` inside
  the service folder.
- The application is in `src/api-service/healthops/` (API, tools, assistant); the iOS adapter is
  in `src/frontend-app/HealthOpsUploader/` (Swift sources English, no tokens, Team IDs or health
  exports). Keep ingestion database migrations in `src/api-service/migrations/`.
- Team-owned data generation and discrepancy training remain in the upstream repository.
  Keep their attribution in `docs/provenance.md`; do not imply they are implemented here.
- Do not create empty runnable services or claim planned work exists.
- Keep service-owned schemas beside their consumers until a shared versioned contract is needed.
- Do not change API behavior or migration revisions solely to reorganize files.

## Verification

- Unit tests (in `src/api-service/`): `uv run pytest -q tests/test_schema.py tests/test_tools.py tests/test_period_comparison.py tests/test_assistant.py tests/test_assistant_model.py tests/test_assistant_toolbox.py tests/test_assistant_cards.py tests/test_assistant_limits.py tests/test_assistant_fixtures.py tests/test_knowledge.py tests/test_knowledge_manifest.py tests/test_eval_sets.py tests/test_ios_contract.py`;
  repository policy: `uv run --directory src/api-service pytest -q ../../scripts/tests`.
- Knowledge base (RAG): third-party texts stay in `src/api-service/.local/knowledge/raw/` (fetched by manifest);
  only `knowledge/sources.yaml` and `knowledge/own/` are committed. Quotation limits per source are enforced in
  code, never relaxed for a demo. `make rag` fetches and ingests the corpus into pgvector in Compose.
- iOS adapter: `cd src/frontend-app/HealthOpsUploader && swift test`; build with `xcodebuild` using a DerivedData path outside the synced Desktop folder (see its README). Tests and fixtures are Simulator-only with synthetic data; device builds are
  signed with a locally supplied team and never add real health data or Team IDs to the repository.
- Full integration suite: `docker compose --profile test build test`, then
  `docker compose --profile test run --rm test` using only the isolated synthetic test DB.
- Check lint/format, Markdown links and public-content policy. Report checks not run.
- No cloud provisioning, real health-data uploads, git commits or pushes without user authority.
  `src/deployment/scripts/vm.sh` refuses billable actions without `--yes`; never pass it on the user's behalf.
- Cloud files: `src/deployment/`; the filled `.env.cloud` and `.local/cloud/` never enter Git.
- Assistant evaluation (`src/api-service/scripts/eval_assistant.py`) runs only against synthetic users; raw outputs stay in `src/api-service/.local/eval/`. Report model results as observed; never weaken tests to pass.

## Teaching and implementation handoff

- For every implementation change and its verification, explain the important concrete code
  so the user can present the project and code to instructors. Explain the problem, real
  file/function, key mechanism, input/output, and why the change is needed.
- Include concrete synthetic examples when useful, especially account identity, record flow,
  failure/retry behavior, and test isolation. Distinguish illustrative fixtures from observed runs.
- Explain each meaningful test's setup, action, assertions, result, and limits rather than only
  reporting a pass count. Maintain the detailed Chinese lesson in ignored `docs/learning/`
  and give a focused code-grounded walkthrough in the handoff.
