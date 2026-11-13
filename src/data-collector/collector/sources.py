"""Download the real wearable datasets and verify every file against the publisher's checksum."""

from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

log = logging.getLogger("sources")

NETHEALTH = {
    "name": "nethealth",
    "title": "NetHealth Project Data (University of Notre Dame)",
    "record_api": "https://zenodo.org/api/records/21904040",
    "url": "https://zenodo.org/records/21904040",
    "doi": "10.5281/zenodo.21904040",
    "license": "CC-BY-4.0",
    "files": ["FitbitActivity(1-30-20).csv", "FitbitSleep(1-30-20).csv"],
    "role": "Fitbit (provider google_health)",
}
BIDSLEEP = {
    "name": "bidsleep",
    "title": "BIDSleep Apple Watch Dataset: multi-night IHR and accelerometry with EEG labels",
    "base_url": "https://physionet.org/files/bidsleep-dataset/1.0.1/",
    "url": "https://physionet.org/content/bidsleep-dataset/1.0.1/",
    "doi": "10.13026/rees-1092",
    "license": "ODC-By-1.0",
    "night_files": ["hr.csv", "labels.mat"],
    "motion_file": "motion.csv",
    "extra_files": ["README.md", "LICENSE.txt"],
    "role": "Apple Watch (provider healthkit)",
}


class ChecksumError(RuntimeError):
    pass


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(client: httpx.Client, url: str, target: Path, algorithm: str, expected: str) -> bool:
    """Fetch url to target unless a verified copy exists. Returns True when downloaded."""
    if target.exists() and _digest(target, algorithm) == expected:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    for attempt in range(1, 6):
        try:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with partial.open("wb") as stream:
                    for chunk in response.iter_bytes(1 << 20):
                        stream.write(chunk)
            break
        except httpx.HTTPError as exc:
            if attempt == 5:
                raise
            log.warning("retry %d for %s (%s)", attempt, url, type(exc).__name__)
            time.sleep(2**attempt)
    actual = _digest(partial, algorithm)
    if actual != expected:
        partial.unlink()
        raise ChecksumError(f"{target.name}: {algorithm} {actual} != published {expected}")
    partial.replace(target)
    return True


def collect_nethealth(client: httpx.Client, out: Path) -> dict:
    record = client.get(NETHEALTH["record_api"]).raise_for_status().json()
    published = {f["key"]: f for f in record["files"]}
    downloaded = 0
    for name in NETHEALTH["files"]:
        entry = published[name]
        algorithm, expected = entry["checksum"].split(":", 1)
        downloaded += download(client, entry["links"]["self"], out / name, algorithm, expected)
    log.info("nethealth: %d files verified (%d downloaded)", len(NETHEALTH["files"]), downloaded)
    return {
        **{k: v for k, v in NETHEALTH.items() if k != "record_api"},
        "verification": "md5 published by Zenodo",
    }


def collect_bidsleep(client: httpx.Client, out: Path, include_motion: bool, workers: int) -> dict:
    base = BIDSLEEP["base_url"]
    sums_text = client.get(base + "SHA256SUMS.txt").raise_for_status().text
    out.mkdir(parents=True, exist_ok=True)
    (out / "SHA256SUMS.txt").write_text(sums_text)
    wanted = set(BIDSLEEP["night_files"]) | ({BIDSLEEP["motion_file"]} if include_motion else set())
    jobs = []
    for line in sums_text.splitlines():
        expected, _, name = line.strip().partition(" ")
        name = name.strip().lstrip("*")
        if name in BIDSLEEP["extra_files"] or name.rsplit("/", 1)[-1] in wanted:
            jobs.append((base + name, out / name, expected))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(lambda job: download(client, job[0], job[1], "sha256", job[2]), jobs)
        )
    nights = {target.relative_to(out).parent.as_posix() for _, target, _ in jobs} - {"."}
    log.info("bidsleep: %d files verified (%d downloaded)", len(jobs), sum(results))
    return {
        **{k: v for k, v in BIDSLEEP.items() if k not in ("night_files", "motion_file")},
        "verification": "sha256 from SHA256SUMS.txt published by PhysioNet",
        "files_selected": sorted(wanted),
        "include_motion": include_motion,
        "nights": len(nights),
        "subjects": len({n.split("/")[0] for n in nights}),
    }
