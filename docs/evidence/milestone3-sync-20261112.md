# Milestone 3 portfolio synchronization

Verified on November 12, 2026 (America/New_York). This record describes a source migration
and local checks, not a new cloud rollout or a model-quality benchmark.

## Source and starting point

- Source: [HuihanCui/ac215_Megatron, milestone3](https://github.com/HuihanCui/ac215_Megatron/tree/65852283a20c5528403b611e4a78ef6135856e6a),
  commit `65852283a20c5528403b611e4a78ef6135856e6a` (527 tracked files).
- Destination baseline: HealthOps `980250f7f364bd3bc6801238c684c419f51253bc`.
- Initial comparison: 287 upstream-only files, 71 shared files with different contents,
  and 7 portfolio-only files. The root README is one of the 71 and was deliberately preserved.
- All upstream tracked paths are represented after synchronization. Private working directories,
  dataset stores, cloud credentials and external model weights were not imported.

## Functional commit groups

| Group | Main paths | Behavior brought into the portfolio |
| --- | --- | --- |
| Data pipeline | `src/synthea-generator`, `src/data-collector`, `src/data-processor`, `src/data-synthesizer`, `configs`, DVC pointers | Patient generation, source collection, canonical records, synthetic device observations and immutable data versions |
| Training workflows | `src/model-training`, `src/ml-workflow` | Reconciliation models, windowed comparison, Vertex pipeline compilation, writer preference training and reviewed checkpoint/registry workflows |
| Assistant and API | `src/api-service` | Reconciliation serving, deterministic evidence facts, writer/editor flow, output protections, upload data-mode checks and replay/evaluation tooling |
| iOS client | `src/frontend-app/HealthOpsUploader` | Upload destination isolation, request data-kind declarations, clarification/history transitions and explicit generated/fallback answer states |
| Deployment and operations | Compose, Makefile, `src/deployment`, repository tests | Reconciliation image builds, personal environment configuration, monitoring dashboards and alerts, data/training launch profiles |
| Documentation and evidence | `docs`, provenance | Upstream historical records, architecture, exact import boundary, local verification and contributor attribution |

These are import commits grouped by functionality, not claims that one person independently
authored the team's work. See [provenance](../provenance.md) and the
[contribution table](../../contributions-table.md).

## Intentional differences from upstream

1. Root `README.md` and all three `docs/demo` files remain byte-identical to the destination
   baseline. The README's architecture/authorship/evaluation narrative awaits the owner's
   separate update; it still describes the earlier application-only scope.
2. `.gitignore` retains protections for legacy Swift build artifacts and the old root `ios/`.
   The blanket `/data/` exclusion is replaced by upstream's dataset-directory rules, so Git
   can track manifests and DVC pointers without tracking dataset contents.
3. Both Compose entry points use `healthops-portfolio` to preserve the portfolio's isolated
   local project/volume name. Service definitions otherwise follow upstream.
4. `Makefile` retains the portfolio's `make test` alias. The local quickstart uses all API
   extras for verification and links to this record.
5. `src/model-training/writer_dpo/presentation.py` receives the seven-line `provider_name`
   helper already present in the upstream serving copy. The existing byte-parity assertion
   in `test_writer_experiment.py` failed before this repair and passes afterwards. No
   measurement, identifier or duration-formatting behavior was changed by the helper.
6. The evidence index links this synchronization record. Portfolio-only provenance,
   quickstart, prior migration record and workflow illustration remain available.

## Local verification

Dependencies were installed from each service's existing lock with `uv sync --frozen`;
API used `--all-extras`. No dependency lock was regenerated. The API test environment also
received NumPy 2.4.6 and pandas 2.3.3 solely to enable training/serving feature parity tests.

| Check | Observed result | What this establishes |
| --- | --- | --- |
| API `python -m pytest -q` | **801 passed**, 3 Transformer warnings | Full suite with a new disposable `healthops_test` PostgreSQL 17/pgvector container, synthetic fixture users, Swift contract output, and model serving parity |
| Root repository/deployment tests | **44 passed** | Content policy, deployment command construction, synthetic seeding and evaluation HTTP contracts |
| Data processor | **2 passed** | Canonical mapping edge cases |
| Data synthesizer | **7 passed**, 6 NumPy warnings | Generated offline fixture pipeline and device-bias/missingness behavior |
| Model training | **134 passed**, 8 warnings | Tiny synthetic model fits, saved output contracts, checkpoint/presentation/preference/registry logic; no production-size or GPU training |
| ML workflow | **8 passed**, 5 KFP deprecation warnings | Pipeline dependency graph, compile-only spec and submission validation; no cloud submission |
| Swift package | **71 passed** | Fake-network upload headers, environment isolation, state transitions, fixtures and sync engine |
| iOS app | **Simulator build succeeded** | SwiftUI app compiles with code signing disabled; not physical-device acceptance |
| Compose | **3 configurations passed** | All root profiles, `src` entry point, and reconciliation overlay parse successfully |
| Ruff | **Lint passed in all 7 Python services**; API format passed (160 files) | Imported source passes its service lint configuration |
| Publication | **Public-content policy passed; source/document whitespace passed** | Working tree and staged text/path checks; sampled credential-pattern scan found no matches |
| Documentation | **Relative file links checked** | Referenced local targets exist; no assertion of external-link availability |
| Preservation | **README and demo byte comparisons passed** | The requested portfolio presentation was not rewritten |

Commands were run from the respective service directories unless noted:

```bash
uv sync --frozen                     # each tested service; API adds --all-extras
.venv/bin/python -m pytest -q        # API receives HEALTHOPS_TEST_DATABASE_URL
PYTHONPATH=src/api-service src/api-service/.venv/bin/python -m pytest -q scripts/tests
swift test                          # src/frontend-app/HealthOpsUploader
python3 scripts/check_public_content.py
git diff --check -- . ":(exclude)docs/evidence/api-compose-up.log"
```

The disposable database used a random loopback port and tmpfs storage. Existing application,
recording and development containers were not changed. The initial sandboxed API run passed
724 tests and skipped 75 but could not compile two Swift contract fixtures because compiler
caches were inaccessible; the complete authorized run above resolved both failures.

On this Apple Silicon Mac, the locked LightGBM and PyTorch wheels initially loaded separate
OpenMP runtimes and aborted. Pointing the loader at PyTorch's library directory resolved the
conflict without suppressing runtime safety checks or changing source/locks:

```bash
# Run from src/model-training after uv sync --frozen.
DYLD_LIBRARY_PATH="$PWD/.venv/lib/python3.12/site-packages/torch/lib" \
OMP_NUM_THREADS=2 .venv/bin/python -m pytest -q
```

The iOS build reported an existing main-actor/deprecation warning at
`App/GoogleHealthModel.swift:157`. Root `scripts` mix 88- and 100-column formatting conventions;
a default formatter check still flags the two unchanged policy files, and forcing the API
configuration flags four upstream deployment tests. This import preserves upstream formatting
instead of applying unrelated reformatting. Service lint and API formatting pass.
The verbatim upstream `api-compose-up.log` contains psql table-padding whitespace and a final
blank line. It is retained unchanged as historical evidence and excluded from the whitespace
check; all other imported paths pass that check.

## Local checkout versus GitHub versus deployment

The old root `ml/`, `ios/`, `services/`, `infra/`, `tests/` and `examples/` paths were not tracked
in the destination baseline. They contained old caches, build products, empty directories and
local Xcode remnants. They were moved to ignored `.local/legacy-layout-20261112/`, not deleted
or merged into current source. Git tracks files, not empty directories, and pulling does not
remove ignored files. Their presence therefore did not establish that GitHub was out of date.

Published implementation belongs under `src/<service>/`; cross-service documents remain in
`docs/`. The local checkout is refreshed from the synchronized Git tree with pre-sync files
preserved under ignored `.local/pre-milestone3-sync-20261112/`.

Cloud Run serves deployed image revisions, not a live GitHub checkout. A source push does not
update those images. Existing cloud evidence retains its original run context; this task did
not inspect live cloud state or deploy anything. No full Docker image rebuild, live Qwen
accuracy evaluation, private DVC pull, real-provider sync, or paid cloud operation was run.
