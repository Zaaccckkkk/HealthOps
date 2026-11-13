"""Legacy evidence reconstruction and label isolation, without remote inference."""

import copy
import json

import pytest

from healthops.assistant.cards import Card, build_cards
from healthops.assistant.evidence_facts import enriched_packet
from healthops.assistant.toolbox import build_registry
from healthops.schemas import ObservationIn
from healthops.tools.daily_summary import summarize_days
from scripts.audit_writer_evidence import daily_observations
from scripts.replay_writer_corpus import completion_input, load_legacy, reconcile_scope


def test_former_holdout_requires_explicit_opening():
    with pytest.raises(ValueError, match="retire"):
        load_legacy()


def test_all_daily_histories_validate_and_generate_production_cards():
    rows, hashes = load_legacy(open_holdout=True)
    assert len(rows) == 79 and len(hashes) == 3
    count = 0
    for case in rows:
        if case["cards"][0]["kind"] != "daily_table":
            continue
        observations, metric, window = daily_observations(case)
        for observation in observations:
            ObservationIn.model_validate(
                {key: getattr(observation, key) for key in ObservationIn.model_fields}
            )
        result = summarize_days(observations, metric, window)
        cards = build_cards([{"tool": "daily_summary", "result": result.model_dump(mode="json")}])
        facts, payload = completion_input(case, cards, case["today"])
        supplied = json.loads(payload[1]["content"])
        assert supplied["evidence"] == enriched_packet(facts, supplied["language"])
        assert all("provider_display" not in item for item in facts["sources"])
        # Overlap records exist but must never supply an invented usable total.
        for card in cards:
            for row in card.rows:
                if "overlapping_intervals" in row.warnings:
                    assert row.quality == "unusable" and row.value is None
        count += 1
    assert count == 54


def test_reference_answers_and_legacy_quantiles_do_not_leak_to_completion():
    cases, _ = load_legacy(open_holdout=True)
    original = cases[0]
    changed = copy.deepcopy(original)
    changed.update(reference_answer="LEAK_ME", required_facts=["LEAK_ME"], cards=[])
    cards = [Card.model_validate(c) for c in original["cards"]]
    assert completion_input(original, cards, original["today"]) == completion_input(
        changed, cards, original["today"]
    )


def test_reconciliation_maps_scope_without_reusing_invented_predictions():
    rows, _ = load_legacy(open_holdout=True)
    registry = build_registry()
    count = 0
    for case in rows:
        if case["cards"][0]["kind"] != "reconciliation":
            continue
        account, arguments = reconcile_scope(case)
        assert account in {"synthetic-00000", "synthetic-00001"}
        registry["reconcile_devices"].params.model_validate(arguments)
        changed = copy.deepcopy(case)
        changed["cards"][0]["reconciliation"] = {"q50": "999999"}
        assert reconcile_scope(changed) == (account, arguments)
        if case["id"] == "known-neither_reading":
            assert arguments["date"] == "2026-01-01"
        count += 1
    assert count == 25
