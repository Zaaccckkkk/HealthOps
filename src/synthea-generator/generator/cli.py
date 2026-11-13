"""synthea-generator: run Synthea to create synthetic patients for a raw data version.

Inputs : configs/baseline.yaml (section `synthea`), the Synthea JAR baked into the image.
Output : DATA_ROOT/raw/<OUTPUT_DATA_VERSION>/synthea/{csv/*.csv, run_info.json}
The data-collector container later hashes this folder into the raw manifest.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from generator.versioning import (
    VersionError,
    config_dir,
    env_versions,
    manifest_path,
    setup_logging,
    version_dir,
)

log = logging.getLogger("synthea-generator")

# Only the tables the synthesizer reads are kept; Synthea writes many more.
KEEP = ("patients.csv", "conditions.csv", "medications.csv")


def main() -> int:
    setup_logging()
    _, output_version = env_versions("synthea-generator", None, "raw")
    settings = yaml.safe_load((config_dir() / "baseline.yaml").read_text())["synthea"]
    jar = Path(os.environ.get("SYNTHEA_JAR", "/opt/synthea/synthea-with-dependencies.jar"))
    parameters = {**settings, "synthea_version": os.environ.get("SYNTHEA_VERSION", "unknown")}
    out = version_dir(output_version) / "synthea"
    info_path = out / "run_info.json"

    if manifest_path(output_version).exists():
        built = yaml.safe_load(manifest_path(output_version).read_text())
        if built.get("parameters", {}).get("synthea") != parameters:
            raise VersionError(
                f"{output_version} was built with other Synthea settings; set a new version"
            )
        log.info("%s already contains this Synthea run; nothing to do", output_version)
        return 0
    if info_path.exists() and json.loads(info_path.read_text())["parameters"] == parameters:
        log.info("Synthea output for these settings already present; nothing to do")
        return 0

    command = [
        "java", "-jar", str(jar),
        "-p", str(settings["population"]),
        "-s", str(settings["seed"]),
        "-cs", str(settings["seed"]),
        "-r", settings["reference_date"].replace("-", ""),
        "-a", f"{settings['min_age']}-{settings['max_age']}",
        "--exporter.csv.export", "true",
        "--exporter.fhir.export", "false",
        "--exporter.hospital.fhir.export", "false",
        "--exporter.practitioner.fhir.export", "false",
    ]  # fmt: skip
    with tempfile.TemporaryDirectory() as scratch:
        command += ["--exporter.baseDirectory", scratch, settings["state"]]
        log.info("running: %s", " ".join(command))
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
        if out.exists():
            shutil.rmtree(out)
        (out / "csv").mkdir(parents=True)
        for name in KEEP:
            shutil.move(Path(scratch) / "csv" / name, out / "csv" / name)

    info = {
        "parameters": parameters,
        "source": {
            "name": "synthea",
            "title": "Synthea synthetic patient generator",
            "url": "https://github.com/synthetichealth/synthea",
            "license": "Apache-2.0",
            "role": "Clinical context for synthetic participants",
            "tables": list(KEEP),
            "parameters": parameters,
        },
    }
    info_path.write_text(json.dumps(info, indent=2))
    log.info("Synthea wrote %s", ", ".join(KEEP))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
