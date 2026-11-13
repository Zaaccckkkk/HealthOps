"""New-account writer test questions. No authored cards or numerical answers.

Freeze before training. Capture evidence through the normal API and SQL tools.
Account windows were inspected from synthetic-v0.2 metadata, not model results.
"""

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from eval.writer_chain_cases import ZONE

# Separate from the three previously reviewed cohorts. One scenario per account.
ACCOUNTS = {
    "synthetic-00002": ("2026-06-17", "2026-08-27"),
    "synthetic-00004": ("2026-06-10", "2026-09-08"),
    "synthetic-00005": ("2026-08-18", "2026-09-25"),
    "synthetic-00008": ("2026-05-20", "2026-08-12"),
    "synthetic-00011": ("2026-08-06", "2026-09-18"),
    "synthetic-00013": ("2026-07-12", "2026-09-26"),
    "synthetic-00014": ("2026-06-22", "2026-09-13"),
    "synthetic-00006": ("2026-06-16", "2026-08-03"),
    "synthetic-00007": ("2026-07-30", "2026-08-29"),
    "synthetic-00009": ("2026-07-17", "2026-09-30"),
    "synthetic-00010": ("2026-06-20", "2026-09-08"),
    "synthetic-00016": ("2026-05-23", "2026-08-11"),
    "synthetic-00019": ("2026-08-11", "2026-09-29"),
    "synthetic-00021": ("2026-06-21", "2026-08-06"),
    "synthetic-00012": ("2026-07-05", "2026-08-04"),
    "synthetic-00028": ("2026-06-27", "2026-09-07"),
    "synthetic-00030": ("2026-07-16", "2026-08-22"),
    "synthetic-00038": ("2026-06-22", "2026-08-08"),
    "synthetic-00040": ("2026-07-16", "2026-09-05"),
    "synthetic-00045": ("2026-07-11", "2026-08-26"),
}


def cases():
    rows = []

    def add(user, family, metrics, questions, rubric, provider=None, mode="daily"):
        first, end = (date.fromisoformat(d) for d in ACCOUNTS[user])
        start = end - timedelta(days=6)
        today = end if mode == "today" else end + timedelta(days=1)
        target = first + timedelta(days=2) if mode == "short" else end
        if mode == "empty":
            start, end = first - timedelta(days=3), first - timedelta(days=1)
        values = {
            "start": start,
            "end": end,
            "target": target,
            "baseline_start": start - timedelta(days=7),
            "baseline_end": start - timedelta(days=1),
        }
        queries = []
        for metric in metrics:
            if mode in {"short", "reconcile"}:
                tool, arguments = "reconcile_devices", {"metric": metric, "date": str(target)}
            elif mode == "period":
                tool, arguments = (
                    "period_comparison",
                    {
                        "metric": metric,
                        "provider": provider,
                        "baseline_start": str(values["baseline_start"]),
                        "baseline_end": str(values["baseline_end"]),
                        "current_start": str(start),
                        "current_end": str(end),
                    },
                )
            else:
                tool, arguments = (
                    "daily_summary",
                    {
                        "metric": metric,
                        "provider": provider,
                        "start_date": str(start),
                        "end_date": str(end),
                    },
                )
            queries.append({"tool": tool, "arguments": arguments | {"timezone": ZONE}})
        rows.append(
            {
                "id": f"writer-holdout-v1-{family}",
                "source_user_id": user,
                "scenario_group": user,
                "family": family,
                "split": "heldout_test",
                "data_kind": "synthetic",
                "timezone": ZONE,
                "today": str(today),
                "provider": provider,
                "questions": [q.format(**values) for q in questions],
                "expected_queries": queries,
                "manual_rubric": rubric,
                "training_eligible": False,
            }
        )

    add(
        "synthetic-00002",
        "unfinished_steps",
        ["steps"],
        [
            "My count looks low today. Look at my step recordings from {start} to {end}: "
            "can I really say I was less active?"
        ],
        ["Explain unfinished-day and partial-coverage limits; do not infer an activity drop."],
        mode="today",
    )
    add(
        "synthetic-00004",
        "sleep_pattern",
        ["sleep_duration"],
        ["What stands out in my sleep recordings between {start} and {end}?"],
        ["Describe recorded patterns; explain relevant gaps and midnight boundaries plainly."],
    )
    add(
        "synthetic-00005",
        "apple_hrv_missing_definition",
        ["resting_heart_rate", "hrv_sdnn", "hrv_rmssd"],
        [
            "Give me the main takeaways for resting heart rate, HRV SDNN and HRV RMSSD "
            "from {start} to {end}."
        ],
        ["Cover all three metrics; distinguish unavailable RMSSD from SDNN and zero."],
    )
    add(
        "synthetic-00008",
        "heart_definitions",
        ["heart_rate", "resting_heart_rate"],
        [
            "Walk me through heart rate and resting heart rate from {start} to {end}. "
            "What does each set of recordings tell us?"
        ],
        ["Keep heart-rate definitions separate; sample means are not continuous all-day means."],
    )
    add(
        "synthetic-00011",
        "empty_window",
        ["steps"],
        ["I see nothing for steps between {start} and {end}. Does that mean I stayed still?"],
        ["Missing records do not establish zero activity; invent no values."],
        mode="empty",
    )
    add(
        "synthetic-00013",
        "period_basis",
        ["steps"],
        [
            "Did my recorded steps change between {baseline_start} to {baseline_end} and "
            "{start} to {end}? Please explain the comparison in everyday language."
        ],
        ["Distinguish mean daily differences from period totals; preserve coverage limits."],
        mode="period",
    )
    add(
        "synthetic-00014",
        "single_source_reconcile",
        ["steps"],
        ["For {target}, reconcile my steps and tell me which source I can compare."],
        [
            "Only one source is available; no invented rival or device winner.",
            "Explain insufficient three-day history if an estimate is returned.",
        ],
        mode="short",
    )
    add(
        "synthetic-00006",
        "resting_pattern",
        ["resting_heart_rate"],
        ["Is there a consistent pattern in my resting heart rate from {start} through {end}?"],
        ["Check the sequence; do not substitute extrema for endpoints or claim health change."],
    )
    add(
        "synthetic-00007",
        "sleep_resting",
        ["sleep_duration", "resting_heart_rate"],
        [
            "Put my sleep and resting heart rate recordings for {start} to {end} into words. "
            "I want the main points, not a list of every day."
        ],
        ["Cover both metrics with correct direction and units; do not infer causation."],
    )
    add(
        "synthetic-00009",
        "selected_rmssd",
        ["hrv_rmssd"],
        ["Using my selected source, summarize HRV RMSSD between {start} and {end}."],
        ["Honor Google selection; identify actual first/last readings and any gaps."],
        provider="google_health",
    )
    add(
        "synthetic-00010",
        "hrv_definitions",
        ["hrv_sdnn", "hrv_rmssd"],
        ["Describe both HRV SDNN and HRV RMSSD for {start} to {end}, including anything missing."],
        ["SDNN and RMSSD are distinct; missing SDNN is not zero or a substituted RMSSD."],
    )
    add(
        "synthetic-00016",
        "steps_plain_summary",
        ["steps"],
        ["How would you describe my recorded steps from {start} to {end} in a few sentences?"],
        ["Answer with a supported pattern and material limitation, not an availability inventory."],
    )
    add(
        "synthetic-00019",
        "clarify_wellbeing",
        ["resting_heart_rate", "hrv_rmssd"],
        [
            "How have I been doing lately?",
            "For {start} to {end}, describe resting heart rate and HRV RMSSD. "
            "I mean the recordings.",
        ],
        ["Persist clarification; cover both metrics after resume without a health verdict."],
    )
    add(
        "synthetic-00021",
        "short_sleep_estimate",
        ["sleep_duration"],
        [
            "Reconcile my sleep duration on {target}. "
            "How much confidence should I put in that result?"
        ],
        [
            "Flag insufficient history and single-source limits; "
            "do not invent calibrated confidence.",
            "If no usable reading exists, do not present a numerical estimate as observed sleep.",
        ],
        mode="short",
    )
    add(
        "synthetic-00012",
        "paired_resting",
        ["resting_heart_rate"],
        ["What do Apple and Google each show for resting heart rate from {start} to {end}?"],
        ["Cover both sources; do not treat their means as a time trend or silently pool them."],
    )
    add(
        "synthetic-00028",
        "paired_hrv",
        ["hrv_sdnn", "hrv_rmssd"],
        [
            "Explain my HRV recordings from {start} to {end} for SDNN and RMSSD. "
            "Would a bigger number in one mean that source is better?"
        ],
        ["Explain distinct HRV definitions; magnitude alone does not establish device quality."],
    )
    add(
        "synthetic-00030",
        "paired_sleep_steps",
        ["sleep_duration", "steps"],
        [
            "Summarize sleep and steps from {start} to {end} across my sources. "
            "Keep it practical and mention what the records cannot establish."
        ],
        ["Cover both metrics and sources; do not infer sleep caused an activity change."],
    )
    add(
        "synthetic-00038",
        "paired_selected_google",
        ["resting_heart_rate", "hrv_rmssd"],
        [
            "I only want the selected Google source for {start} to {end}: "
            "what happened in resting heart rate and HRV RMSSD?"
        ],
        ["Do not include Apple results; preserve gaps and avoid claiming higher HRV is healthier."],
        provider="google_health",
    )
    add(
        "synthetic-00040",
        "paired_reconcile",
        ["steps"],
        [
            "Reconcile the steps on {target}. Which reading is nearer the estimate, and what "
            "can that comparison actually establish?"
        ],
        [
            "Derive attribution from the actual card; do not predetermine a winning source.",
            "Model agreement is not accuracy; preserve interval and relevant reliability limits.",
        ],
        mode="reconcile",
    )
    add(
        "synthetic-00045",
        "clarify_reconcile",
        ["resting_heart_rate"],
        [
            "Which of my device readings should I trust?",
            "Reconcile resting heart rate on {target}; explain agreement with the estimate.",
        ],
        [
            "Request metric/date clarification then resume; no device-accuracy claim.",
            "Use actual card attribution, values and uncertainty rather than a prewritten winner.",
        ],
        mode="reconcile",
    )
    return rows


def frozen_cases():
    """Refuse silent question or data-manifest changes before a held-out run."""
    root = Path(__file__).resolve().parents[3]
    manifest = json.loads((Path(__file__).parent / "writer-holdout-v1/manifest.json").read_text())
    rows = cases()
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    data_manifest = root / "data/manifests/synthetic-v0.2.yaml"
    if (
        hashlib.sha256(encoded).hexdigest() != manifest["cases_sha256"]
        or hashlib.sha256(data_manifest.read_bytes()).hexdigest()
        != manifest["data_manifest_sha256"]
        or len(rows) != manifest["scenarios"]
    ):
        raise ValueError("Frozen holdout changed; explicitly version the suite before running")
    return rows
