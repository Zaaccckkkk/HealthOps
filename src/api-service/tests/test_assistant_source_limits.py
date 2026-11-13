"""Synthetic source-boundary regressions; no personal history or live model calls."""

import asyncio

import pytest
from test_assistant import NY, USER, FakeToolbox
from test_assistant_cards import comparison, daily, day
from test_assistant_guards import data, findings, query, run, spec

from healthops.assistant import numeric
from healthops.assistant.cards import WARNING_TEXT, answer_source_warnings, build_cards
from healthops.assistant.claims import FALLBACK_EXPLANATION
from healthops.assistant.contracts import AskRequest
from healthops.assistant.model import ModelTurn, ScriptedChatModel
from healthops.assistant.orchestrator import Assistant, AssistantSettings
from healthops.connectors.google_health import DATA_TYPES

CODE = "google_heart_rate_not_synced"


@pytest.mark.parametrize("provider", [None, "google_health", "healthkit"])
@pytest.mark.parametrize("metric", ["heart_rate", "resting_heart_rate", "steps"])
def test_source_notice_matches_requested_channel_and_metric(provider, metric):
    evidence = daily(metric, "bpm", "point", {})
    evidence["arguments"]["provider"] = provider
    card = build_cards([evidence])[0]
    expected = metric == "heart_rate" and provider != "healthkit"
    assert CODE not in card.warnings
    assert WARNING_TEXT[CODE] not in card.warning_text
    assert bool(answer_source_warnings([evidence])) == expected
    assert card.rows == []  # Never fabricate a measurement for an unsupported import.


def test_notice_is_kept_on_comparison_and_does_not_leak_to_other_metrics():
    evidence = comparison("heart_rate", "bpm", "62", "64", "2")
    rest = daily("resting_heart_rate", "bpm", "point", {})
    cards = build_cards([evidence, rest])
    assert all(CODE not in card.warnings for card in cards)
    assert answer_source_warnings([evidence, evidence, rest]) == [WARNING_TEXT[CODE]]
    assert answer_source_warnings([rest]) == []


def test_existing_google_heart_rate_records_remain_visible_without_relabeling():
    evidence = daily(
        "heart_rate",
        "bpm",
        "point",
        {"google_health": [day("2026-10-01", sample_count=1, sample_mean="72")]},
    )
    card = build_cards([evidence])[0]
    assert card.provider == "google_health" and card.metric == "heart_rate"
    assert card.rows[0].value == "72"
    assert CODE not in card.warnings
    assert answer_source_warnings([evidence]) == [WARNING_TEXT[CODE]]


@pytest.mark.parametrize("valid", [True, False])
def test_source_limit_is_visible_even_if_model_omits_it_or_claims_fail(valid):
    result, _, _ = run(
        [
            ModelTurn(tool_calls=[query("heart_rate")]),
            findings("window_summary e1.healthkit" if valid else "window_summary invented"),
            findings("window_summary still_invented"),
        ],
        spec(("heart_rate",)),
        {"heart_rate": data("heart_rate")},
    )
    assert result.answer.count(WARNING_TEXT[CODE]) == 1
    assert all(CODE not in c.warnings for c in result.cards)
    assert "Apple Health" in result.cards[0].title
    assert result.cards[0].provider == "healthkit"
    assert result.cards[0].evidence_id == "e1.healthkit"
    if valid:
        assert "(Apple Health)" in result.answer
        assert "(healthkit)" not in result.answer
    assert result.explanation_status == ("verified" if valid else "unverified")
    assert result.completion_status == ("complete" if valid else "incomplete")
    if not valid:
        assert result.answer.startswith(FALLBACK_EXPLANATION)
    else:
        assert result.numeric_consistency == numeric.check(
            result.answer.replace(WARNING_TEXT[CODE], ""), result.evidence
        )  # The source notice adds no measurement or numeric claim.


def test_explicit_apple_selection_does_not_add_google_notice():
    model = ScriptedChatModel(
        [
            ModelTurn(tool_calls=[query("heart_rate")]),
            findings("window_summary e1.healthkit"),
        ]
    )
    assistant = Assistant(
        model, FakeToolbox({"daily_summary": data("heart_rate")}), AssistantSettings()
    )
    result = asyncio.run(
        assistant.ask(
            AskRequest(question="Recorded heart rate", timezone=NY, provider="healthkit"),
            USER,
            request_spec=spec(("heart_rate",)),
        )
    )
    assert result.explanation_status == "verified"
    assert WARNING_TEXT[CODE] not in result.answer


def test_connector_capability_notice_matches_current_import_contract():
    assert "daily-resting-heart-rate" in DATA_TYPES
    assert "heart-rate" not in DATA_TYPES


def test_apple_overlap_warning_does_not_contaminate_google_card():
    evidence = daily(
        "steps",
        "count",
        "cumulative",
        {
            "healthkit": [day("2026-10-01", warnings=["overlapping_intervals"])],
            "google_health": [day("2026-10-01", interval_total_sum="1200")],
        },
        warnings=["overlapping_intervals", "interval_totals_used"],
    )
    apple, google = build_cards([evidence])
    assert "overlapping_intervals" in apple.warnings
    assert "overlapping_intervals" not in google.warnings
    assert not any("overlap" in text for text in google.warning_text)
    assert "interval_totals_used" in google.warnings
    assert google.rows[0].value == "1200"
