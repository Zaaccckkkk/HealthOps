"""Dataset versions, paths, hashing and manifests.

Each data container keeps an identical copy of this module so it stays self-contained.
A version name decides where its files live under DATA_ROOT:

    raw-v1          -> raw/raw-v1/
    canonical-v0.1  -> processed/canonical/canonical-v0.1/
    synthetic-v0.1  -> synthetic/synthetic-v0.1/

and its manifest is manifests/<version>.yaml. A version is immutable once its manifest
exists: a rerun with the same configuration skips the build, while a rerun with different
configuration or changed files stops and asks for a new OUTPUT_DATA_VERSION.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

STAGE_DIRS = {"raw": "raw", "canonical": "processed/canonical", "synthetic": "synthetic"}
VERSION_PATTERN = re.compile(r"^(raw|canonical|synthetic)-v\d+(\.\d+)*$")

log = logging.getLogger("versioning")


class VersionError(RuntimeError):
    """The requested version is invalid, missing, or already built differently."""


def data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT", "/data"))


def config_dir() -> Path:
    return Path(os.environ.get("CONFIG_DIR", "/configs"))


def check_version(version: str | None, stage: str | None = None) -> str:
    if not version or not VERSION_PATTERN.match(version):
        raise VersionError(f"invalid data version {version!r}; expected e.g. canonical-v0.1")
    if stage and not version.startswith(f"{stage}-"):
        raise VersionError(f"{version} is not a {stage} version")
    return version


def version_dir(version: str) -> Path:
    return data_root() / STAGE_DIRS[check_version(version).split("-")[0]] / version


def manifest_path(version: str) -> Path:
    return data_root() / "manifests" / f"{check_version(version)}.yaml"


def env_versions(stage: str, input_stage: str | None, output_stage: str) -> tuple[str | None, str]:
    """Read INPUT_DATA_VERSION / OUTPUT_DATA_VERSION and log them with the Git commit."""
    input_version = os.environ.get("INPUT_DATA_VERSION") or None
    output_version = os.environ.get("OUTPUT_DATA_VERSION")
    if input_stage:
        check_version(input_version, input_stage)
    check_version(output_version, output_stage)
    log.info(
        "[%s] INPUT_DATA_VERSION=%s OUTPUT_DATA_VERSION=%s GIT_COMMIT=%s",
        stage,
        input_version or "-",
        output_version,
        git_commit(),
    )
    return input_version, output_version


def git_commit() -> str:
    return os.environ.get("GIT_COMMIT") or "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def describe_files(root: Path) -> list[dict]:
    """Every file under root with size and SHA-256, sorted by relative path."""
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return files


def config_hashes(names: list[str]) -> dict[str, str]:
    return {name: sha256_file(config_dir() / name) for name in names}


def load_config(name: str) -> dict:
    return yaml.safe_load((config_dir() / name).read_text())


def read_manifest(version: str) -> dict:
    path = manifest_path(version)
    if not path.exists():
        raise VersionError(f"{version} has no manifest at {path}; build it first")
    return yaml.safe_load(path.read_text())


def already_built(version: str, configs: dict[str, str], parameters: dict | None) -> bool:
    """True when the version exists with identical configuration and untouched files.

    parameters=None skips the parameter comparison, for run conditions that are only known
    after start-up (e.g. whether an optional LLM is reachable); they are still recorded.
    """
    path = manifest_path(version)
    if not path.exists():
        return False
    manifest = yaml.safe_load(path.read_text())
    changed_parameters = parameters is not None and manifest.get("parameters", {}) != parameters
    if manifest.get("configs", {}) != configs or changed_parameters:
        raise VersionError(
            f"{version} was built with different configuration; set a new OUTPUT_DATA_VERSION"
        )
    if describe_files(version_dir(version)) != manifest.get("files", []):
        raise VersionError(f"files of {version} differ from its manifest; set a new version")
    log.info("%s already built with this configuration; nothing to do", version)
    return True


def write_manifest(version: str, stage: str, description: str, **fields) -> Path:
    manifest = {
        "version": version,
        "stage": stage,
        "description": description,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        **fields,
        "files": describe_files(version_dir(version)),
    }
    path = manifest_path(version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(manifest, sort_keys=False, width=10_000))
    log.info("wrote %s (%d files)", path, len(manifest["files"]))
    return path


def input_reference(version: str) -> dict:
    """How an output refers to its input: name plus the hash of the input manifest."""
    return {"version": version, "manifest_sha256": sha256_file(manifest_path(version))}


def setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
