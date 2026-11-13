"""BIDSleep Apple Watch nights -> canonical rows with provider ``healthkit``.

Facts relied on (dataset README and files checked on 2026-10-18):
- One folder per subject (BidslabXX) and night (1..7) with hr.csv, labels.mat, motion.csv.
- hr.csv has no header: Unix time in seconds, heart rate in bpm (Apple Watch PPG via
  HealthKit, about one value every 5 s).
- labels.mat: ``recStart`` as text in US Eastern local time, ``expert_label`` and
  ``dreem_label`` with one stage per 30 s epoch from recStart (0 wake, 1 N1, 2 N2, 3 N3,
  4 REM, 5 unknown).

Mapping:
  each hr.csv row                  -> heart_rate, sample
  expert_label epochs in {1,2,3,4} -> sleep_duration (epochs * 30 s), interval_total over the
                                      labelled recording [recStart, recStart + epochs * 30 s)
Motion stays in the raw data only: HealthOps has no canonical accelerometry metric.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import loadmat

TIMEZONE = "America/New_York"
PROVIDER = "healthkit"
DEVICE = "Apple Watch (BIDSleep)"
SLEEP_STAGES = (1, 2, 3, 4)
EPOCH_SECONDS = 30


def nights(raw_dir: Path) -> list[tuple[str, str]]:
    """(subject, night) pairs listed in SHA256SUMS.txt whose files were downloaded."""
    found = set()
    for line in (raw_dir / "SHA256SUMS.txt").read_text().splitlines():
        name = line.split()[-1] if line.strip() else ""
        parts = name.split("/")
        if len(parts) == 3 and (raw_dir / parts[0] / parts[1] / "hr.csv").exists():
            found.add((parts[0], parts[1]))
    return sorted(found, key=lambda pair: (pair[0], int(pair[1])))


def _common(subject: str, raw_version: str, source: str) -> dict:
    return {
        "user_id": f"bidsleep-{subject}",
        "provider": PROVIDER,
        "source_device": DEVICE,
        "source_timezone": TIMEZONE,
        "provenance_dataset": "bidsleep",
        "provenance_version": raw_version,
        "provenance_source": source,
    }


def heart_rate_rows(raw_dir: Path, subject: str, night: str, raw_version: str) -> pd.DataFrame:
    source = f"{subject}/{night}/hr.csv"
    hr = pd.read_csv(raw_dir / source, header=None, names=["ts", "bpm"], dtype={"ts": str})
    seconds = pd.to_numeric(hr["ts"], errors="coerce")
    keep = seconds.notna() & np.isfinite(hr["bpm"]) & (hr["bpm"] > 0)
    hr, seconds = hr[keep], seconds[keep]
    time = pd.to_datetime(seconds, unit="s", utc=True)
    return pd.DataFrame(
        {
            **_common(subject, raw_version, source),
            "source_record_id": f"bidsleep:hr:{subject}/{night}:" + hr["ts"].str.strip(),
            "metric": "heart_rate",
            "value": hr["bpm"].astype(float),
            "unit": "bpm",
            "started_at": time,
            "ended_at": time,
            "aggregation": "sample",
        }
    )


def sleep_row(raw_dir: Path, subject: str, night: str, raw_version: str) -> dict | None:
    source = f"{subject}/{night}/labels.mat"
    path = raw_dir / source
    if not path.exists():
        return None
    mat = loadmat(path)
    if "expert_label" not in mat or "recStart" not in mat:
        return None
    labels = np.asarray(mat["expert_label"]).ravel()
    if labels.size == 0 or not np.isin(labels, (0, *SLEEP_STAGES)).any():
        return None
    local = pd.Timestamp(str(np.asarray(mat["recStart"]).ravel()[0]).strip())
    start = local.tz_localize(TIMEZONE, ambiguous=False, nonexistent="shift_forward").tz_convert(
        "UTC"
    )
    end = start + pd.Timedelta(seconds=EPOCH_SECONDS * labels.size)
    return {
        **_common(subject, raw_version, source),
        "source_record_id": f"bidsleep:sleep:{subject}/{night}",
        "metric": "sleep_duration",
        "value": float(np.isin(labels, SLEEP_STAGES).sum() * EPOCH_SECONDS),
        "unit": "s",
        "started_at": start,
        "ended_at": end,
        "aggregation": "interval_total",
    }


def load(raw_dir: Path, raw_version: str) -> tuple[pd.DataFrame, dict]:
    frames, sleeps, missing_labels = [], [], 0
    pairs = nights(raw_dir)
    for subject, night in pairs:
        frames.append(heart_rate_rows(raw_dir, subject, night, raw_version))
        row = sleep_row(raw_dir, subject, night, raw_version)
        if row is None:
            missing_labels += 1
        else:
            sleeps.append(row)
    frames.append(pd.DataFrame(sleeps))
    stats = {
        "nights": len(pairs),
        "subjects": len({subject for subject, _ in pairs}),
        "nights_without_usable_labels": missing_labels,
    }
    return pd.concat(frames, ignore_index=True), stats
