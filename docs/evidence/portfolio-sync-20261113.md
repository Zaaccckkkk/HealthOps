# November 13 portfolio synchronization

## Source

HealthOps starts at `0aa4b6093833b5630ffa8827296c0b6f13dff6c8`. The team repository's
`milestone3` branch still points to `65852283a20c5528403b611e4a78ef6135856e6a`.
This update imports 25 modified tracked files and one new deployment overlay from the
owner-authorized local team working tree, not a newer upstream commit. All 26 files match
the SHA-256 hashes in the [source snapshot](portfolio-source-snapshot-20261113.json).
The team checkout and team GitHub branch were not modified.

## Commit themes

1. **Null-safe Writer formatting:** preserve missing comparison values without crashing
   or inventing durations; keep serving and offline training formatters byte-identical.
2. **Assistant completion and independent Writer serving:** complete omitted daily-summary
   coverage only from valid, already-fetched, exactly scoped evidence after one model repair;
   allow Writer and Editor to use a separately configured model while routing/tool selection
   stays on the base model. Include response metadata, Swift contract fixtures, deployment
   validation, documentation and an optional inference-host residency overlay.
3. **Portfolio documentation:** retain the README structure, demo and pipeline PNG; update
   behavior, code links, owner-confirmed evaluation aggregates and contributor responsibilities.
   Project direction is distinguished from teammates' implementation; data work appears last.

Earlier intentional portfolio differences, including Compose isolation, `make test`, local
artifact exclusions and the training presentation helper, are preserved. See the
[previous import record](milestone3-sync-20261112.md).

## Verification

| Check | Result | Scope |
| --- | --- | --- |
| API full suite | **815 passed**, 3 existing Transformer warnings | Disposable PostgreSQL 17/pgvector database, migrations, API integration, assistant behavior, serving parity and Swift contract fixtures |
| Repository/deployment tests | **45 passed** | Configuration validation and deployment command generation, policy and evaluation helpers |
| Writer training regression subset | **44 passed** | `test_writer_experiment.py`, `test_writer_posttrain.py`, `test_writer_dpo_v2.py`; formatting/presentation parity and offline training tooling |
| Swift package | **71 passed** | Response decoding, app state and upload/sync contracts |
| Ruff | **Passed** | API, changed Writer formatter, deployment script and its tests; all 160 API files pass format checking |
| Compose | **2 combinations passed** | Model + Writer overlay; model + V100 + Writer overlays, configuration parsing only |
| Publication | **Passed** | Public-content policy, staged whitespace checks, changed Markdown file targets and source snapshot hashes |
| Presentation preservation | **Passed** | Demo assets and pipeline PNG unchanged from HealthOps baseline |

The test database was a new loopback-only, tmpfs-backed container with synthetic fixtures.
An initial test invocation used the wrong SQLAlchemy URL scheme and failed during fixture
setup; changing the test invocation to `postgresql+psycopg://` produced the full passing run
above without source changes. No dependency lock was changed.

The new behavioral tests exercise separate base/Writer requests, optional Editor routing,
model metadata, missing-value formatting, and omission completion. Negative cases ensure
completion cannot substitute for an unqueried metric, cross date/timezone/provider scope,
or conceal invalid claims. Deployment tests keep the base model unchanged and reject unsafe
Writer identifiers.

These local checks do not measure model-quality improvements. The README's latest aggregate
results have their own [source and scope note](portfolio-results-20261113.md). This update did
not rerun live inference, GPU training, data pipelines, full image builds, physical-device
acceptance, or cloud deployment. Source publication does not change a running cloud revision.
