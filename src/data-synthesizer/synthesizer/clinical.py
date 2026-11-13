"""Synthea patients -> clinical profiles (age, sex, active conditions and medications)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def _active(frame: pd.DataFrame, reference: pd.Timestamp) -> pd.DataFrame:
    start = pd.to_datetime(frame["START"].str[:10], errors="coerce")
    stop = pd.to_datetime(frame["STOP"].str[:10], errors="coerce")
    return frame[(start <= reference) & (stop.isna() | (stop > reference))]


def load_profiles(csv_dir: Path, settings: dict, effects: dict) -> pd.DataFrame:
    """One row per living patient within the age range, with clinical flags from config."""
    reference = pd.Timestamp(settings["reference_date"])
    patients = pd.read_csv(csv_dir / "patients.csv", dtype=str)
    birth = pd.to_datetime(patients["BIRTHDATE"], errors="coerce")
    death = pd.to_datetime(patients["DEATHDATE"], errors="coerce")
    age = (reference - birth).dt.days // 365.25
    keep = (death.isna() | (death > reference)) & age.between(
        settings["min_age"], settings["max_age"]
    )
    profiles = pd.DataFrame(
        {"synthea_patient_id": patients["Id"], "age": age, "sex": patients["GENDER"]}
    )[keep]

    conditions = _active(pd.read_csv(csv_dir / "conditions.csv", dtype=str), reference)
    medications = _active(pd.read_csv(csv_dir / "medications.csv", dtype=str), reference)
    by_patient = {
        "conditions": conditions.groupby("PATIENT")["DESCRIPTION"].agg(lambda d: sorted(set(d))),
        "medications": medications.groupby("PATIENT")["DESCRIPTION"].agg(lambda d: sorted(set(d))),
    }
    for column, lists in by_patient.items():
        profiles[column] = profiles["synthea_patient_id"].map(lists)
        profiles[column] = profiles[column].apply(lambda v: v if isinstance(v, list) else [])

    for flag, rule in effects.items():
        if not isinstance(rule, dict):
            continue
        column, terms = (
            ("medications", rule["medication_match"])
            if "medication_match" in rule
            else ("conditions", rule["condition_match"])
        )
        terms = [t.lower() for t in terms]
        profiles[flag] = profiles[column].apply(
            lambda items, terms=terms: any(t in item.lower() for item in items for t in terms)
        )
    return profiles.reset_index(drop=True)


def prompt_conditions(conditions: list[str], limit: int = 8) -> str:
    """Disorders only: Synthea also records social findings such as employment status."""
    disorders = [c for c in conditions if "(disorder)" in c or "obesity" in c.lower()]
    return ", ".join(disorders[:limit]) or "none recorded"
