# Synthea Generator

Runs [Synthea](https://github.com/synthetichealth/synthea) to create synthetic patients that
give each synthetic wearable user a clinical background (age, sex, active conditions and
medications). First step of the data pipeline.

| | |
|---|---|
| Input | `configs/baseline.yaml`, section `synthea` (population, seed, age range, reference date, state) |
| Output | `data/raw/<OUTPUT_DATA_VERSION>/synthea/csv/{patients,conditions,medications}.csv` and `run_info.json` |
| Environment | `OUTPUT_DATA_VERSION` (a `raw-*` version), `DATA_ROOT`, `CONFIG_DIR`, `GIT_COMMIT` |
| Image | `python:3.12-slim` + default headless JRE + Synthea v3.3.0 JAR, verified by SHA-256 at build time |

The data-collector hashes this folder into the raw manifest; this container writes no manifest
itself. Reruns with the same settings do nothing; different settings for a finished raw version
stop with an error. Only the three tables the synthesizer reads are kept.

```bash
docker compose --profile data run --rm synthea-generator     # from the repository root
```

5000 patients take several minutes (one JVM, Massachusetts demographics). Synthea output is
synthetic; its patient names and identifiers are fictional.
