"""Presentation arithmetic and immutable evidence at the model-input boundary."""

import copy
import json

import pytest

from healthops.assistant import narrative
from healthops.assistant.cards import format_value
from healthops.assistant.presentation import duration_text, presentation_packet


@pytest.mark.parametrize(
    "seconds,english,chinese",
    [
        ("23940", "6 hours 39 minutes", "6\u5c0f\u65f639\u5206\u949f"),
        ("23370", "about 6 hours 30 minutes", "\u7ea66\u5c0f\u65f630\u5206\u949f"),
        ("21720", "6 hours 2 minutes", "6\u5c0f\u65f62\u5206\u949f"),
        ("29880", "8 hours 18 minutes", "8\u5c0f\u65f618\u5206\u949f"),
        ("29.999999", "about 0 minutes", "\u7ea60\u5206\u949f"),
        ("30", "about 1 minute", "\u7ea61\u5206\u949f"),
        ("3599", "about 1 hour", "\u7ea61\u5c0f\u65f6"),
        ("3600", "1 hour", "1\u5c0f\u65f6"),
        ("-900", "-15 minutes", "-15\u5206\u949f"),
        ("0", "0 minutes", "0\u5206\u949f"),
        (None, None, None),
    ],
)
def test_exact_and_rounded_duration_boundaries(seconds, english, chinese):
    assert duration_text(seconds) == english
    assert duration_text(seconds, "zh") == chinese


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_duration_is_rejected(value):
    with pytest.raises(ValueError, match="finite"):
        duration_text(value)


def test_card_display_shares_rounding_but_keeps_original_seconds():
    assert format_value("23370.000000", "s") == "about 6 h 30 min (23370 s)"
    assert format_value("29.5", "s") == "about 0 h 0 min (29.5 s)"


def evidence():
    return {
        "today": "2024-04-10",
        "sources": [
            {
                "evidence_id": "e1.healthkit",
                "provider": "healthkit",
                "metric": "sleep_duration",
                "unit": "s",
                "days": [
                    {"date": "2024-04-09", "value": "23370", "quality": "value"},
                    {"date": "2024-04-10", "value": None, "quality": "none"},
                ],
                "recorded_values_summary": {
                    "minimum": "21720",
                    "maximum": "29880",
                    "mean_of_available_daily_values": "23370",
                    "first_to_last_change": "-900",
                    "absolute_change": "900",
                },
                "baseline": {"value": "23940"},
                "current": {"value": "23370"},
                "difference": "-570",
                "readings": [{"provider": "google_health", "value": "23940"}],
                "model_estimate": {"q05": "21720", "q50": "23940", "q95": "29880"},
                "comparison_to_estimate": {
                    "absolute_distances": {"healthkit": "900"},
                    "closest_channels": ["healthkit"],
                },
            },
            {
                "evidence_id": "e2.google_health",
                "provider": "google_health",
                "unit": "ms",
                "days": [{"value": "23940", "quality": "value"}],
            },
        ],
    }


def test_all_duration_relationships_are_converted_without_mutating_raw_evidence():
    raw = evidence()
    original = copy.deepcopy(raw)
    converted = presentation_packet(raw, "en")
    assert raw == original
    source = converted["sources"][0]
    assert source["provider"] == "healthkit" and source["evidence_id"] == "e1.healthkit"
    assert source["provider_display"] == "Apple Health"
    assert source["days"][0]["value"] == "23370"
    assert source["days"][0]["value_display"] == "about 6 hours 30 minutes"
    assert source["days"][1]["value_display"] is None
    summary = source["recorded_values_summary"]
    assert summary["minimum_display"] == "6 hours 2 minutes"
    assert summary["maximum_display"] == "8 hours 18 minutes"
    assert summary["first_to_last_change_display"] == "-15 minutes"
    assert summary["absolute_change_display"] == "15 minutes"
    assert source["baseline"]["value_display"] == "6 hours 39 minutes"
    assert source["current"]["value_display"] == "about 6 hours 30 minutes"
    assert source["difference_display"] == "about -10 minutes"
    assert source["readings"][0]["provider_display"] == "Google Health"
    assert source["model_estimate"]["q50_display"] == "6 hours 39 minutes"
    comparison = source["comparison_to_estimate"]
    assert comparison["absolute_distances_display"] == {"healthkit": "15 minutes"}
    assert comparison["closest_sources_display"] == ["Apple Health"]
    assert "value_display" not in converted["sources"][1]["days"][0]
    assert converted == presentation_packet(converted, "en")


def test_actual_writer_messages_include_python_display_and_seconds_exception():
    raw = evidence()
    payload = narrative.messages("Keep the original seconds.", raw, "zh")
    user = json.loads(payload[1]["content"])
    row = user["evidence"]["sources"][0]["days"][0]
    assert row["value"] == "23370"  # explicit seconds requests still have the original value
    assert row["value_display"] == "\u7ea66\u5c0f\u65f630\u5206\u949f"
    assert user["evidence"]["sources"][0]["provider_display"] == "Apple Health"
    assert "value_display" not in raw["sources"][0]["days"][0]
    assert payload[0]["content"] == narrative.SYSTEM_V6
