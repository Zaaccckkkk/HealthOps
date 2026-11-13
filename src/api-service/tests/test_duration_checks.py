"""Dimensional checks: coincident digits cannot rescue incorrect conversions."""

import json

import pytest

from healthops.assistant import narrative
from healthops.assistant.duration_checks import checked_duration_text
from healthops.assistant.model import ModelTurn


def facts(value="25680", unit="s", quality="value"):
    return {
        "sources": [
            {
                "evidence_id": "e1.healthkit",
                "unit": unit,
                "days": [{"date": "2026-08-07", "quality": quality, "value": value}],
            }
        ]
    }


def validate(answer, evidence=None):
    return narrative.validate(
        ModelTurn(text=json.dumps({"answer": answer, "evidence_ids": ["e1.healthkit"]})),
        evidence or facts(),
    )[1]


@pytest.mark.parametrize(
    "answer",
    [
        "Recorded 7 hours 8 minutes.",
        "Recorded 7 h 8 min.",
        "Recorded 428 minutes.",
        "Recorded 25,680 seconds.",
        "Recorded approximately 7.13 hours.",
        "On 2026-08-07, recorded 7 hours and 8 minutes.",
        "\u8bb0\u5f55\u4e3a7\u5c0f\u65f68\u5206\u949f\u3002",
    ],
)
def test_equivalent_seconds_durations(answer):
    assert validate(answer) is None


@pytest.mark.parametrize(
    "answer,value",
    [
        ("Approximately 7 hours 9 minutes.", "25733.276148"),
        ("About 10 hours 0 minutes.", "35999"),
        ("About 0 hours 1 minute.", "59"),
        ("0 hours 0 minutes.", "0"),
        ("7 hours 8 minutes 53.28 seconds.", "25733.276148"),
        (
            "\u7ea6\u4e3a7\u5c0f\u65f69\u5206\u949f\uff08\u56db\u820d\u4e94\u5165\u5230\u5206\u949f\uff09\u3002",
            "25733.276148",
        ),
    ],
)
def test_explicit_rounding_and_carry(answer, value):
    assert validate(answer, facts(value)) is None


@pytest.mark.parametrize(
    "answer",
    [
        "Recorded 7 hours 11 minutes.",
        "Recorded about 7 hours 99 minutes.",
        "Recorded about 8 hours 8 minutes.",
        "Recorded -7 hours 8 minutes.",
        "Recorded 7.5 hours 8 minutes.",
        "Recorded 25680 hours.",
        "Recorded about 9 hours 28 minutes.",
        "Recorded 7 hours 8 minutes and 999999 steps.",
    ],
)
def test_wrong_or_unqualified_conversion_rejected(answer):
    assert validate(answer) == "unsupported_number"


def test_known_bad_sleep_conversion_is_not_accepted_as_rounding():
    assert validate("About 9 hours 28 minutes.", facts("34900.204502")) == "unsupported_number"
    assert validate("About 9 hours 42 minutes.", facts("34900.204502")) is None


@pytest.mark.parametrize(
    "evidence",
    [
        facts(unit="count"),
        facts(unit="bpm"),
        facts(value=None, quality="none"),
        facts(quality="unusable"),
    ],
)
def test_only_usable_seconds_evidence_can_support_duration(evidence):
    assert validate("7 hours 8 minutes.", evidence) == "unsupported_number"


def test_component_digits_already_in_metadata_do_not_bypass_quantity_check():
    evidence = facts()
    evidence["metadata"] = [7, 9, 8, 99]
    assert validate("7 hours 11 minutes.", evidence) == "unsupported_number"


@pytest.mark.parametrize("seconds", [25560, 25680, 25800])
def test_conversion_tolerance_is_inclusive(seconds):
    assert validate("7 hours 8 minutes.", facts(str(seconds))) is None


@pytest.mark.parametrize("seconds", [25559, 25801, 0, -25680])
def test_tolerance_does_not_accept_larger_errors_zero_or_sign_changes(seconds):
    assert validate("7 hours 8 minutes.", facts(str(seconds))) == "unsupported_number"


def test_tolerance_does_not_change_seconds_only_measurements_or_missingness():
    assert validate("25780 seconds.") == "unsupported_number"
    assert validate("0 hours.", facts("60")) == "unsupported_number"
    assert validate("1 minute.", facts("0")) == "unsupported_number"


def test_dates_and_citations_still_checked_independently():
    assert validate("On 2030-08-07: 7 hours 8 minutes.") == "unsupported_date"
    turn = ModelTurn(text=json.dumps({"answer": "7 hours 8 minutes.", "evidence_ids": ["wrong"]}))
    assert narrative.validate(turn, facts())[1] == "evidence_coverage_mismatch"


def test_conversion_does_not_globally_whitelist_its_digits():
    assert validate("7 hours 8 minutes; a reading of 7 bpm.") == "unsupported_number"


def test_diagnostic_preserves_invalid_span_for_editor_feedback():
    assert checked_duration_text("About 9 hours 28 minutes.", facts("34900.204502"))[2] == [
        "9 hours 28 minutes"
    ]


def test_dimensional_check_does_not_claim_to_verify_source_attribution():
    evidence = facts()
    evidence["sources"].append(
        {
            "evidence_id": "e2.google_health",
            "unit": "s",
            "days": [{"quality": "value", "value": "28800"}],
        }
    )
    # Quantity membership alone cannot prove which source it describes.
    assert checked_duration_text("HealthKit recorded 8 hours.", evidence)[2] == []


def test_approximation_marker_does_not_expand_the_two_minute_tolerance():
    assert validate("About 7 hours.") == "unsupported_number"
