"""data-processor: map a raw version onto the HealthOps canonical schema.

Input  : DATA_ROOT/raw/<INPUT_DATA_VERSION>/{nethealth,bidsleep}/ (manifest must exist)
Output : DATA_ROOT/processed/canonical/<OUTPUT_DATA_VERSION>/
           nethealth.parquet   Fitbit rows, provider google_health
           bidsleep.parquet    Apple Watch rows, provider healthkit
           users.parquet       one row per real participant
         DATA_ROOT/manifests/<OUTPUT_DATA_VERSION>.yaml
"""

import logging

import pandas as pd

from processor import bidsleep, nethealth
from processor.schema import validate, write_parquet
from processor.versioning import (
    already_built,
    env_versions,
    input_reference,
    read_manifest,
    setup_logging,
    version_dir,
    write_manifest,
)

log = logging.getLogger("data-processor")

PARAMETERS = {
    "timezones": {"nethealth": nethealth.TIMEZONE, "bidsleep": bidsleep.TIMEZONE},
    "providers": {"nethealth": nethealth.PROVIDER, "bidsleep": bidsleep.PROVIDER},
    "bidsleep_sleep_label": "expert_label",
}


def users_table(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.groupby(["user_id", "provider", "provenance_dataset"])
    users = grouped.agg(
        first_observation=("started_at", "min"),
        last_observation=("ended_at", "max"),
        records=("metric", "size"),
        metrics=("metric", lambda m: ",".join(sorted(set(m)))),
    )
    return users.reset_index().rename(columns={"provenance_dataset": "dataset"})


def summary(frame: pd.DataFrame) -> dict:
    counts = frame.groupby(["provider", "metric", "aggregation"]).size()
    return {
        "rows": len(frame),
        "users": int(frame["user_id"].nunique()),
        "first_observation": frame["started_at"].min().isoformat(),
        "last_observation": frame["ended_at"].max().isoformat(),
        "rows_by_metric": {"/".join(key): int(n) for key, n in counts.items()},
    }


def main() -> int:
    setup_logging()
    input_version, output_version = env_versions("data-processor", "raw", "canonical")
    read_manifest(input_version)  # refuses to read an unfinished raw version
    if already_built(output_version, {}, PARAMETERS):
        return 0
    raw, out = version_dir(input_version), version_dir(output_version)

    stats, frames = {}, []
    for name, module in (("nethealth", nethealth), ("bidsleep", bidsleep)):
        log.info("mapping %s", name)
        rows, load_stats = module.load(raw / name, input_version)
        rows, dropped = validate(rows)
        write_parquet(rows, out / f"{name}.parquet")
        stats[name] = {**load_stats, "dropped": dropped, **summary(rows)}
        frames.append(rows)
        log.info("%s: %d rows, %d users", name, len(rows), stats[name]["users"])
    users = users_table(pd.concat(frames, ignore_index=True))
    users.to_parquet(out / "users.parquet", index=False)

    write_manifest(
        output_version,
        "data-processor",
        "Real Apple Watch (healthkit) and Fitbit (google_health) data in the canonical schema.",
        input=input_reference(input_version),
        configs={},
        parameters=PARAMETERS,
        stats=stats,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
