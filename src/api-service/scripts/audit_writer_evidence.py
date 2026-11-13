"""Audit handcrafted development cards against production evidence construction.

This is a reachability probe, not a cohort replay: invented in-memory observations
exercise the real daily aggregator and card/packet builders. No DB, model, reserved
suite or personal records are accessed. Reconciliation quantiles remain unverified.
Historical prompts/completions must not be paired with reconstructed evidence.
"""

import json
from datetime import date, timedelta
from decimal import Decimal

from healthops.assistant.cards import Card, build_cards
from healthops.assistant.narrative import packet
from healthops.models import Observation
from healthops.schemas import Metric
from healthops.tools.daily_summary import summarize_days
from healthops.tools.local_days import MAX_SUMMARY_DAYS, LocalDayWindow
from scripts.eval_answer_quality import load_cases


def daily_observations(case):
    """Construct one possible record history, not the original history of a cohort."""
    cards = [Card.model_validate(c) for c in case["cards"]]
    if not cards or any(c.kind != "daily_table" for c in cards):
        raise ValueError("Daily cards required")
    first = cards[0]
    if any(
        (c.metric, c.timezone, c.start_date, c.end_date)
        != (first.metric, first.timezone, first.start_date, first.end_date)
        for c in cards
    ):
        raise ValueError("This probe supports one daily tool window only")
    window = LocalDayWindow.from_dates(
        first.timezone,
        date.fromisoformat(first.start_date),
        date.fromisoformat(first.end_date),
        MAX_SUMMARY_DAYS,
    )
    observations = []
    for card in cards:
        for row in card.rows:
            start, end = window.day_bounds(date.fromisoformat(row.date))
            if row.value is not None:
                if row.kind == "daily_total":
                    intervals = [(start, end, row.value, "daily_total")]
                elif row.kind == "interval_total_sum":
                    intervals = [
                        (
                            start + timedelta(hours=12),
                            start + timedelta(hours=13),
                            row.value,
                            "interval_total",
                        )
                    ]
                else:
                    raise ValueError("Unsupported row basis in this audit")
            elif row.quality == "unusable" and "overlapping_intervals" in row.warnings:
                # Deliberately invented overlapping records. Their values must not
                # become a usable total, whatever numbers were used here.
                intervals = [
                    (
                        start + timedelta(hours=12),
                        start + timedelta(hours=14),
                        "100",
                        "interval_total",
                    ),
                    (
                        start + timedelta(hours=13),
                        start + timedelta(hours=15),
                        "200",
                        "interval_total",
                    ),
                ]
            elif row.quality == "none" and row.value is None:
                continue
            else:
                raise ValueError("Unsupported missing/unusable row")
            for begin, finish, value, aggregation in intervals:
                observations.append(
                    Observation(
                        provider=card.provider,
                        source_record_id=f"writer-audit-{len(observations)}",
                        metric=first.metric,
                        unit=first.unit,
                        value=Decimal(value),
                        started_at=begin,
                        ended_at=finish,
                        source_timezone=first.timezone,
                        aggregation=aggregation,
                    )
                )
    return observations, Metric(first.metric), window


def replay_daily(case):
    observations, metric, window = daily_observations(case)
    result = summarize_days(observations, metric, window)
    evidence = [{"tool": "daily_summary", "result": result.model_dump(mode="json")}]
    return build_cards(evidence)


def differing_fields(old, new, path=""):
    """Return exact JSON paths; ordering and value serialization are intentional."""
    if isinstance(old, dict) and isinstance(new, dict):
        return [
            field
            for key in sorted(old.keys() | new.keys())
            for field in (
                [f"{path}/{key}"]
                if key not in old or key not in new
                else differing_fields(old[key], new[key], f"{path}/{key}")
            )
        ]
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [
            field
            for i, (a, b) in enumerate(zip(old, new))
            for field in differing_fields(a, b, f"{path}/{i}")
        ]
    return [] if old == new else [path]


def audit_case(case):
    if case.get("split") != "development" or case.get("data_kind") != "synthetic":
        raise ValueError("Only synthetic development cases may be audited")
    cards = [Card.model_validate(c) for c in case["cards"]]
    report = {
        "id": case["id"],
        "family": case["family"],
        "training_eligible": False,
        "cohort_replayed": False,
        "router_and_database_executed": False,
    }
    if all(c.kind == "daily_table" for c in cards):
        old = packet(cards, case["today"])
        rebuilt = packet(replay_daily(case), case["today"])
        report.update(
            method="invented_observations_through_production_daily_builder",
            changed_fields=differing_fields(old, rebuilt),
            original_packet=old,
            reconstructed_packet=rebuilt,
        )
    elif all(c.kind == "reconciliation" for c in cards):
        # These are unconditional outputs of reconcile_from_summaries and
        # _reconcile_card. Do not manufacture a predictor run to certify quantiles.
        report.update(
            method="mandatory_field_audit_only_no_predictor_run",
            quantiles_provenance="hand_authored_not_observed_model_outputs",
            missing_context_defaulted=[
                c.evidence_id for c in cards if "context_defaulted" not in c.warnings
            ],
            nonproduction_statistic=[
                c.evidence_id for c in cards if c.statistic != "synthetic-trained q50 estimate"
            ],
        )
    else:
        raise ValueError("Unsupported card type")
    return report


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "scope": "development_only_reachability_audit_not_end_to_end_validation",
                "cases": [audit_case(c) for c in load_cases("development")],
            },
            indent=2,
        )
    )
