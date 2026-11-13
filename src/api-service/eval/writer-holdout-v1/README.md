# Writer holdout v1

Twenty question scenarios (22 conversation turns) are frozen for 20 synthetic-v0.2 accounts
outside the three accounts used by the current writer preference pool. Seven accounts are
Apple-only, seven Fitbit-only and six paired. Each account appears in one scenario. This is
a new-account synthetic test, not a real-world or independently authored benchmark.

**Status, 2026-11-08:** the first complete baseline ran on the frozen questions and 20 imported
accounts. See the [baseline report](../../../../docs/evidence/writer-holdout-baseline-20261108.md):
5/20 strict full-chain passes and 6/11 raw writer semantic passes on valid captured inputs.
These are assistant-reviewed synthetic results, not independently reviewed gold. The other
nine scenarios did not reach the writer. Exact prompts and outputs remain local for paired
post-training comparison; no training has run. No observation values, ideal prose or evidence
cards are authored in these scenario definitions.

[Scenario definitions](../writer_holdout_cases.py) contain the questions and conditional review
criteria. [manifest.json](manifest.json) pins their canonical JSON hash and the dataset manifest.
Account availability dates were read from local parquet metadata after source checksum
verification. We did not select these accounts by model performance or desired outcomes.

## Coverage

| Scope | Scenarios |
| --- | ---: |
| Steps: unfinished day, absent records, period comparison, plain summary | 4 |
| Sleep summary | 1 |
| Ordinary versus resting heart rate | 1 |
| Resting heart rate: single and paired sources | 2 |
| HRV definitions, selected RMSSD and missing metrics | 4 |
| Sleep with resting heart rate or steps | 2 |
| Selected Google source with two metrics | 1 |
| Clarification followed by a multi-metric summary | 1 |
| Reconciliation: single source, short history, paired, clarification | 4 |
| **Total** | **20** |

The rubric requires supported patterns, correct dates/units/source attribution, coverage of
requested metrics, understandable qualifications and no unsupported health or device-accuracy
claims. Missingness and estimate winners must come from captured evidence. A three-day-history
case should retain the insufficient-history warning. A no-reading result must not become an
invented observed value. These criteria are not prewritten numerical answers.

## Capture and compare

Use a fresh, migrated loopback PostgreSQL database named `healthops_test`, containing only the
20 imported accounts. Never point this runner at the personal or cloud development database.
The existing importer supports repeated `--user-id`; use the IDs in `ACCOUNTS` from the scenario
module. Run its dry-run first with the manifest and dataset below, then use `--apply` only after
setting `HEALTHOPS_SYNTHETIC_DATABASE_URL` to that isolated database. A `--max-records 150000`
bound accommodates the inspected cohort. Keep import reports and credentials local.

Paths from the repository root:

- Dataset: `data/synthetic/synthetic-v0.2`
- Manifest: `data/manifests/synthetic-v0.2.yaml`
- Import report directory: `src/api-service/.local/eval/writer-holdout-v1/import`

After preparing that isolated import and an authorized model endpoint, run from `src/api-service`:

```sh
uv run python -m scripts.eval_writer_chain \
  --database-url "$HEALTHOPS_TEST_DATABASE_URL" \
  --import-dir .local/eval/writer-holdout-v1/import \
  --model-url http://127.0.0.1:<model-port> \
  --model-dir .local/reconcile/w14 \
  --holdout --output .local/eval/writer-holdout-v1/base
```

The runner verifies the exact account set, manifest hash, stored identities and record counts.
It refuses extra accounts, including the development edge account. This suite does not upload
development edge fixtures and cannot be shortened with `--limit`. Use development for smoke
tests. Changing questions or the dataset manifest requires an explicitly versioned freeze.

Record upstream success and writer quality separately. A scenario blocked by routing or context
limits is an end-to-end failure, not a fabricated writer negative. Expected-tool checks use the
ordinary SQL tool as a scope reference; they are not an independent numerical oracle. Capture
the real writer input before judging prose. For writer-only comparison, freeze those exact
prompts and compare the base and trained writers on identical inputs; repeat the full chain
separately to check regressions. Review labels against evidence without selecting the model
using test errors. Do not mine this holdout into preference pairs: the preference builder rejects
`heldout_test` traces.

## Development split alongside this test

The current 36 reviewed candidates include five unresolved arithmetic-contract cases. A local
proposal assigns the remaining 31 as **24 training candidates and 7 development-validation
candidates**. All examples from a cohort account stay together, including reconstruction cases
whose reconciliation evidence uses that account. Handcrafted fixture families, paraphrases and
numeric/date variants also stay together. No identical evidence packet crosses the proposed split.

The seven validation candidates comprise the two `synthetic-00000` cases, the one
`synthetic-00003` case and four sparse-constant fixture cases. They form only three conservative
groups. The 24 training candidates form six groups. Validation is previously reviewed
development material, useful for a pilot's model selection but not an independent performance
estimate. It does not cover every training capability, notably paired-device reconciliation.
More independent validation groups are preferable as the candidate pool grows.

Candidate assignment is not approval of a training export. Preserve saved review decisions,
resolve outstanding replacements, check that the preference is meaningful for the same prompt,
and keep blocked pairs out. No fixed train/validation/test size ratio makes a small dataset
reliable; additional near-duplicates do not create independent evidence.

## Verification

At preparation, thirty-five focused tests passed for chain capture, preference guards, replay
compatibility and the new holdout. They cover tool schemas, frozen-question tampering, missing/duplicate/old import
accounts, exclusion of the development edge account, and rejection of holdout preference mining.
Ruff and public-content checks passed. Dataset checksums and the 20 availability windows were
verified locally. No PostgreSQL integration, cloud inference, baseline scoring or training was
performed for that preparation step. The linked baseline report documents the subsequent
real-model run, isolated import and verification separately. The freeze manifest's original
`questions_frozen_not_run` status is retained as a historical preparation artifact.
