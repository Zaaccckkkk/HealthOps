"""Behavioral boundaries for deterministic writer facts across arbitrary windows."""

import copy
from datetime import date, timedelta
from pathlib import Path

import pytest

from healthops.assistant import narrative
from healthops.assistant.evidence_facts import date_text, enriched_packet


def packet(values, *, start="2024-02-25", basis="sample_mean", unit="bpm"):
    first = date.fromisoformat(start)
    days = [
        {
            "date": str(first + timedelta(days=i)),
            "value": str(v) if v is not None else None,
            "quality": "value" if v is not None else "none",
            "basis": basis,
        }
        for i, v in enumerate(values)
    ]
    return {
        "today": "2025-01-01",
        "sources": [
            {
                "evidence_id": "e1.healthkit",
                "provider": "healthkit",
                "kind": "daily_table",
                "start_date": days[0]["date"],
                "end_date": days[-1]["date"],
                "unit": unit,
                "metric": "heart_rate",
                "warnings": [],
                "days": days,
            }
        ],
    }


def analyze(raw, language="en"):
    return enriched_packet(raw, language)["sources"][0]["computed_facts"]


def test_peak_end_date_binding_and_nonmonotonic_net_increase():
    raw = packet([60, 65, 63, 68, 66, 64, 62])
    before = copy.deepcopy(raw)
    facts = analyze(raw)
    assert raw == before
    assert facts["first"]["value"] == "60" and facts["last"]["value"] == "62"
    assert facts["last"]["date_display"] == "March 2, 2024"
    assert facts["maximum"] == {
        "value": "68",
        "value_display": "68",
        "dates_display": ["February 28, 2024"],
    }
    assert facts["first_to_last"]["absolute_change"] == "2"
    assert facts["between_available_readings"]["pattern"] == "fluctuating"
    fall = facts["continuous_segments"][-1]
    assert fall["start"]["value"] == "68" and fall["end"]["value"] == "62"
    assert fall["daily_transitions"] == 3


def test_thirty_days_with_flat_and_reverse_runs_use_same_rules():
    values = list(range(60, 70)) + [69] * 10 + list(range(68, 58, -1))
    facts = analyze(packet(values))
    assert facts["coverage"]["calendar_days"] == 30
    assert facts["coverage"]["all_calendar_days_have_values"]
    assert not facts["coverage"]["full_day_measurement_coverage_established"]
    assert facts["consecutive_calendar_day_transitions"] == {
        "increase": 9,
        "decrease": 10,
        "unchanged": 10,
    }
    assert [s["direction"] for s in facts["continuous_segments"]] == [
        "increase",
        "unchanged",
        "decrease",
    ]
    assert len(facts["maximum"]["dates_display"]) == 11


def test_gaps_unusable_zero_and_unreported_days_are_distinct():
    raw = packet([0, None, 4, None, 2, 1])
    source = raw["sources"][0]
    source["days"][3]["quality"] = "unusable"
    source["warnings"] = ["overlapping_intervals"]
    del source["days"][1]
    facts = analyze(raw)
    assert facts["first"]["value"] == "0"
    assert facts["coverage"]["days_not_supplied"] == 1
    assert facts["coverage"]["days_with_unusable_records"] == 1
    assert facts["coverage"]["days_without_records"] == 0
    assert facts["consecutive_calendar_day_transitions"] == {
        "increase": 0,
        "decrease": 1,
        "unchanged": 0,
    }
    assert len(facts["continuous_segments"]) == 1
    assert enriched_packet(raw, "en")["sources"][0]["warning_reasons_display"] == [
        "overlapping records prevent a reliable total"
    ]


def test_order_independence_and_no_source_mutation():
    raw = packet([2, 1, 3])
    expected = analyze(raw)
    raw["sources"][0]["days"].reverse()
    assert analyze(raw) == expected
    rendered = enriched_packet(raw, "zh")
    assert enriched_packet(rendered, "zh") == rendered
    assert raw["sources"][0]["days"][0]["date"] == "2024-02-27"


@pytest.mark.parametrize("problem", ["duplicate", "outside", "nan", "quality", "reversed"])
def test_invalid_evidence_cannot_yield_a_trend(problem):
    raw = packet([1, 2])
    source = raw["sources"][0]
    if problem == "duplicate":
        source["days"][1]["date"] = source["days"][0]["date"]
    elif problem == "outside":
        source["days"][1]["date"] = "2024-03-02"
    elif problem == "nan":
        source["days"][1]["value"] = "NaN"
    elif problem == "quality":
        source["days"][1]["quality"] = "none"
    else:
        source["start_date"], source["end_date"] = source["end_date"], source["start_date"]
    facts = analyze(raw)
    assert facts["status"] == "unavailable_invalid_evidence"
    assert "first_to_last" not in facts


def test_mixed_basis_and_single_value_do_not_create_continuous_trends():
    raw = packet([1, 2])
    raw["sources"][0]["days"][1]["basis"] = "daily_summary"
    assert analyze(raw)["status"] == "mixed_or_unknown_recording_basis"
    assert (
        analyze(packet([None, 2, None]))["between_available_readings"]["pattern"]
        == "insufficient_values"
    )
    assert analyze(packet([None, None]))["status"] == "no_usable_values"


def test_all_ties_and_segment_omissions_are_explicit():
    tied = analyze(packet([3] * 30))
    assert len(tied["minimum"]["dates_display"]) == 30
    assert tied["between_available_readings"]["pattern"] == "all_available_values_equal"
    alternating = analyze(packet([1, 2] * 15))
    assert alternating["omitted_segment_count"] == 24
    assert len(alternating["continuous_segments"]) == 5
    assert sum(alternating["consecutive_calendar_day_transitions"].values()) == 29


def test_duration_facts_reuse_exact_rounding_and_raw_seconds():
    facts = analyze(packet([23370, 25770, 23940], unit="s"), "zh")
    assert facts["maximum"]["value_display"] == "\u7ea67\u5c0f\u65f610\u5206\u949f"
    assert facts["maximum"]["value"] == "25770"
    assert facts["last"]["value_display"] == "6\u5c0f\u65f639\u5206\u949f"


def test_reconciliation_recomputes_tie_and_distinguishes_missing_estimate():
    source = {
        "kind": "reconciliation",
        "unit": "count",
        "readings": [
            {"provider": "healthkit", "value": "5900"},
            {"provider": "google_health", "value": "6300"},
        ],
        "model_estimate": {"q50": "6100"},
    }
    result = analyze({"sources": [source]})
    assert result["tie"] and result["closest_sources"] == ["Apple Health", "Google Health"]
    assert [r["value"] for r in result["distances"]] == ["200", "200"]
    source.pop("model_estimate")
    result = analyze({"sources": [source]})
    assert result["status"] == "no_numerical_estimate_supplied" and "tie" not in result
    source["readings"][0]["value"] = None
    assert analyze({"sources": [source]})["sources_without_values"] == ["Apple Health"]


def test_dates_cross_year_and_period_difference_scope():
    assert date_text("2024-02-29", "en") == "February 29, 2024"
    assert date_text("2025-01-01", "zh") == "2025\u5e741\u67081\u65e5"
    source = {"kind": "comparison", "statistic": "mean of daily totals", "unit": "count"}
    assert analyze({"sources": [source]})["difference_scope"] == "per day"
    assert narrative.PROMPT_VERSION == "grounded-answer-v6-computed-evidence"


def test_rounded_duration_displays_do_not_turn_unequal_distances_into_a_tie():
    source = {
        "kind": "reconciliation",
        "unit": "s",
        "readings": [
            {"provider": "healthkit", "value": "3590"},
            {"provider": "google_health", "value": "3595"},
        ],
        "model_estimate": {"q50": "3600"},
    }
    result = analyze({"sources": [source]})
    assert [r["value_display"] for r in result["distances"]] == [
        "about 0 minutes",
        "about 0 minutes",
    ]
    assert result["closest_sources"] == ["Google Health"]
    assert result["tie"] is False


def test_cloud_and_service_use_identical_implementation():
    root = Path(__file__).resolve().parents[2]
    assert (root / "api-service/healthops/assistant/evidence_facts.py").read_bytes() == (
        root / "model-training/writer_dpo/evidence_facts.py"
    ).read_bytes()
