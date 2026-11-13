# Data Processor

Maps a raw data version onto the HealthOps canonical observation schema (parquet). Real data
from different devices enters one pipeline without losing device or source information.

| | |
|---|---|
| Input | `data/raw/<INPUT_DATA_VERSION>/` (its manifest must exist) |
| Output | `data/processed/canonical/<OUTPUT_DATA_VERSION>/{nethealth,bidsleep,users}.parquet` and the manifest |
| Environment | `INPUT_DATA_VERSION` (`raw-*`), `OUTPUT_DATA_VERSION` (`canonical-*`), `DATA_ROOT`, `GIT_COMMIT` |

| Source | Provider | Canonical rows |
|---|---|---|
| NetHealth activity | `google_health` | `steps` (daily_total), `heart_rate` = daily mean (daily_summary), `exercise_duration` = fairly + very active minutes (daily_total) |
| NetHealth sleep | `google_health` | `sleep_duration` per episode (interval_total over time in bed; `dataDate` is the wake-up date) |
| BIDSleep `hr.csv` | `healthkit` | `heart_rate` samples (about every 5 s overnight) |
| BIDSleep `labels.mat` | `healthkit` | `sleep_duration` per night from expert-scored N1/N2/N3/REM epochs |

Not mapped: BIDSleep motion (no canonical accelerometry metric), NetHealth heart-rate zones,
calories and wear percentage. NetHealth records no resting heart rate or HRV; none is derived.
Rows the ingestion API would reject are dropped and counted in the manifest (`dropped`).

Observed on the full real data (2026-10-18): NetHealth 1,399,623 rows / 623 users;
BIDSleep 1,603,981 rows / 47 users / 253 nights; about 35 s.

```bash
docker compose --profile data run --rm data-processor        # from the repository root
uv run pytest -q                                              # in this folder
```

`processor/schema.py` defines the canonical columns (kept identical in data-synthesizer).
