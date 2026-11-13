"""data-collector: download the real datasets into a raw version and write its manifest.

Inputs : NetHealth (Zenodo) and BIDSleep (PhysioNet) over HTTPS; Synthea output already
         written into the same raw version by the synthea-generator container.
Output : DATA_ROOT/raw/<OUTPUT_DATA_VERSION>/{nethealth,bidsleep,synthea}/ and
         DATA_ROOT/manifests/<OUTPUT_DATA_VERSION>.yaml
"""

import json
import logging
import os

import httpx

from collector import sources
from collector.versioning import (
    already_built,
    env_versions,
    setup_logging,
    version_dir,
    write_manifest,
)

log = logging.getLogger("data-collector")


def main() -> int:
    setup_logging()
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per file is too noisy
    _, output_version = env_versions("data-collector", None, "raw")
    include_motion = os.environ.get("BIDSLEEP_INCLUDE_MOTION", "false").lower() == "true"
    out = version_dir(output_version)
    synthea_info = out / "synthea" / "run_info.json"
    if not synthea_info.exists():
        log.error("missing %s: run synthea-generator for %s first", synthea_info, output_version)
        return 1
    synthea = json.loads(synthea_info.read_text())
    parameters = {"bidsleep_include_motion": include_motion, "synthea": synthea["parameters"]}
    if already_built(output_version, {}, parameters):
        return 0

    with httpx.Client(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True) as client:
        nethealth = sources.collect_nethealth(client, out / "nethealth")
        bidsleep = sources.collect_bidsleep(
            client, out / "bidsleep", include_motion, int(os.environ.get("DOWNLOAD_WORKERS", "8"))
        )
    write_manifest(
        output_version,
        "data-collector",
        "Real wearable datasets (NetHealth Fitbit, BIDSleep Apple Watch) and Synthea patients.",
        configs={},
        parameters=parameters,
        sources=[nethealth, bidsleep, synthea["source"]],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
