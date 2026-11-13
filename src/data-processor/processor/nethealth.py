"""NetHealth Fitbit export -> canonical rows with provider ``google_health``.

Facts relied on (checked against the published files on 2026-10-18):
- Activity: one row per ``egoid`` and ``datadate``; about 2,000 user-days appear twice and the
  copies agree except for blanks (one daily mean heart rate conflicts).
- ``meanrate`` is the mean heart rate while worn, not resting heart rate.
- Sleep: one row per episode. ``dataDate`` is the local date the episode ENDED;
  ``timetobed``/``timeoutofbed`` are clock times without a date. ``bedtimedur`` equals the
  clock span; ``Efficiency`` = minsasleep / (minsasleep + minsawake).
- No time zone is recorded; the cohort lived in South Bend, Indiana.

Mapping:
  steps                                   -> steps, daily_total over the local day
  meanrate                                -> heart_rate, daily_summary (daily mean, not RHR)
  fairlyactiveminutes + veryactiveminutes -> exercise_duration (s), daily_total
  each sleep episode minsasleep * 60      -> sleep_duration, interval_total over time in bed
Resting heart rate, HRV and active energy are not in the export and are not derived.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from processor.schema import iso_utc

TIMEZONE = "America/Indiana/Indianapolis"
PROVIDER = "google_health"
DEVICE = "Fitbit (NetHealth)"
ACTIVITY_FILE = "FitbitActivity(1-30-20).csv"
SLEEP_FILE = "FitbitSleep(1-30-20).csv"
ACTIVITY_FIELDS = ["steps", "meanrate", "fairlyactiveminutes", "veryactiveminutes"]


def _common(egoid: pd.Series, raw_version: str, source: str) -> dict:
    return {
        "user_id": "nethealth-" + egoid,
        "provider": PROVIDER,
        "source_device": DEVICE,
        "source_timezone": TIMEZONE,
        "provenance_dataset": "nethealth",
        "provenance_version": raw_version,
        "provenance_source": source,
    }


def _localize(naive: pd.Series) -> pd.Series:
    # Repeated fall-back hours resolve to standard time; skipped spring-forward times move on.
    ambiguous = np.zeros(len(naive), dtype=bool)
    return naive.dt.tz_localize(
        TIMEZONE, ambiguous=ambiguous, nonexistent="shift_forward"
    ).dt.tz_convert("UTC")


def activity_rows(path: Path, raw_version: str) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path, dtype={"egoid": str, "datadate": str})
    raw["egoid"] = raw["egoid"].str.strip()
    grouped = raw.groupby(["egoid", "datadate"], sort=False)[ACTIVITY_FIELDS]
    conflicts = grouped.nunique() > 1
    days = grouped.first().mask(conflicts).reset_index()  # first non-blank copy per field
    stats = {
        "activity_rows": len(raw),
        "activity_duplicate_days": int(len(raw) - len(days)),
        "activity_conflicting_values": int(conflicts.to_numpy().sum()),
    }
    day = pd.to_datetime(days["datadate"], format="%Y-%m-%d", errors="coerce")
    days, day = days[day.notna()], day[day.notna()]
    start, end = _localize(day), _localize(day + pd.Timedelta(days=1))
    common = _common(days["egoid"], raw_version, ACTIVITY_FILE)
    date = days["datadate"]

    frames = []
    for metric, value, key, unit in (
        ("steps", days["steps"], "daily-steps", "count"),
        ("heart_rate", days["meanrate"], "daily-mean-heart-rate", "bpm"),
        (
            "exercise_duration",
            (days["fairlyactiveminutes"] + days["veryactiveminutes"]) * 60,
            "daily-active-minutes",
            "s",
        ),
    ):
        keep = value.notna()
        frames.append(
            pd.DataFrame(
                {
                    **{k: (v[keep] if isinstance(v, pd.Series) else v) for k, v in common.items()},
                    "source_record_id": f"nethealth:{key}:" + date[keep],
                    "metric": metric,
                    "value": value[keep],
                    "unit": unit,
                    "started_at": start[keep],
                    "ended_at": end[keep],
                    "aggregation": "daily_summary" if metric == "heart_rate" else "daily_total",
                }
            )
        )
    return pd.concat(frames, ignore_index=True), stats


def sleep_rows(path: Path, raw_version: str) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(
        path, dtype={"egoid": str, "dataDate": str, "timetobed": str, "timeoutofbed": str}
    )
    raw["egoid"] = raw["egoid"].str.strip()
    total = len(raw)
    end_day = pd.to_datetime(raw["dataDate"], format="%Y-%m-%d", errors="coerce")
    to_bed = pd.to_timedelta(raw["timetobed"], errors="coerce")
    out_of_bed = pd.to_timedelta(raw["timeoutofbed"], errors="coerce")
    start = end_day + to_bed
    end = end_day + out_of_bed
    # The episode ended on dataDate: a bedtime later than the wake time was the evening before.
    crossed = out_of_bed.notna() & (end <= start)
    start = start.where(~crossed, start - pd.Timedelta(days=1))
    # Missing wake time: rebuild it from time in bed and keep the end on dataDate.
    no_wake = out_of_bed.isna()
    end = end.where(~no_wake, start + pd.to_timedelta(raw["bedtimedur"], unit="m"))
    late = no_wake & (end.dt.normalize() > end_day)
    start = start.where(~late, start - pd.Timedelta(days=1))
    end = end.where(~late, end - pd.Timedelta(days=1))

    asleep = raw["minsasleep"]
    keep = start.notna() & end.notna() & asleep.notna() & (asleep >= 0)
    raw, start, end = raw[keep], _localize(start[keep]), _localize(end[keep])
    keep = end > start
    raw, start, end = raw[keep], start[keep], end[keep]
    rows = pd.DataFrame(
        {
            **_common(raw["egoid"], raw_version, SLEEP_FILE),
            "source_record_id": "nethealth:sleep:" + iso_utc(start) + ":" + iso_utc(end),
            "metric": "sleep_duration",
            "value": raw["minsasleep"] * 60,
            "unit": "s",
            "started_at": start,
            "ended_at": end,
            "aggregation": "interval_total",
        }
    )
    stats = {"sleep_rows": total, "sleep_unparseable": total - len(rows)}
    return rows, stats


def load(raw_dir: Path, raw_version: str) -> tuple[pd.DataFrame, dict]:
    activity, activity_stats = activity_rows(raw_dir / ACTIVITY_FILE, raw_version)
    sleep, sleep_stats = sleep_rows(raw_dir / SLEEP_FILE, raw_version)
    return pd.concat([activity, sleep], ignore_index=True), {**activity_stats, **sleep_stats}
