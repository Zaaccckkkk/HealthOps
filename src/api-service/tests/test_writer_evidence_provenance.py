"""Evidence reachability checks using invented records, never personal/cohort data."""

import json

import pytest

from healthops.assistant.evidence_facts import enriched_packet
from healthops.assistant.narrative import messages, packet
from scripts.audit_writer_evidence import audit_case, replay_daily
from scripts.eval_answer_quality import load_cases


def case(family):
    return next(c for c in load_cases("development") if c["family"] == family)


def test_real_interval_label_reaches_exact_writer_message():
    original = case("partial_day")
    facts = packet(replay_daily(original), original["today"])
    payload = messages(original["question"], facts, original["language"])
    supplied = json.loads(payload[1]["content"])["evidence"]
    source = supplied["sources"][0]
    assert source["statistic"] == "sum of recorded intervals (not full-day coverage)"
    assert source["includes_today"] is True
    assert [d["value"] for d in source["days"]] == [
        r["value"] for r in original["cards"][0]["rows"]
    ]
    assert supplied == enriched_packet(facts, original["language"])
    assert (
        source["provider_display"]
        == {
            "healthkit": "Apple Health",
            "google_health": "Google Health",
        }[source["provider"]]
    )
    assert all("provider_display" not in item for item in facts["sources"])


def test_mixed_bases_have_row_bases_but_no_authored_hint_or_mean():
    original = case("mixed_bases")
    facts = packet(replay_daily(original), original["today"])
    source = facts["sources"][0]
    assert source["statistic"] == "mixed statistics; see each row"
    assert {d["basis"] for d in source["days"]} == {"daily_total", "interval_total_sum"}
    assert "recorded_values_summary" not in source
    assert "not comparable daily coverage" not in json.dumps(facts)


def test_overlaps_preserve_source_ownership_and_unusable_is_not_absent():
    original = case("overlap")
    cards = replay_daily(original)
    affected = next(c for c in cards if c.warnings)
    usable = next(c for c in cards if not c.warnings)
    assert affected.provider == original["cards"][0]["provider"]
    assert affected.warnings == ["overlapping_intervals"]
    assert affected.statistic == "no data"
    assert all(
        r.quality == "unusable" and r.value is None and r.records == 2 for r in affected.rows
    )
    assert all(r.quality == "value" and r.value is not None for r in usable.rows)


def test_no_records_produces_unscoped_empty_card_and_explicit_warning():
    original = case("missing")
    cards = replay_daily(original)
    assert len(cards) == 1
    assert cards[0].provider is None
    assert cards[0].rows == []
    assert cards[0].statistic is None
    assert cards[0].warnings == ["no_records_in_window"]


def test_all_development_cases_are_audited_without_certifying_provenance():
    reports = [audit_case(c) for c in load_cases("development")]
    daily = [r for r in reports if "changed_fields" in r]
    model = [r for r in reports if "quantiles_provenance" in r]
    assert len(daily) == 34
    assert all(r["changed_fields"] for r in daily)
    assert len(model) == 16
    assert all(r["missing_context_defaulted"] and r["nonproduction_statistic"] for r in model)
    assert all(not r["training_eligible"] and not r["cohort_replayed"] for r in reports)


def test_reserved_split_cannot_be_used_by_audit():
    # No reserved question is loaded: only the split metadata is changed.
    with pytest.raises(ValueError, match="development"):
        audit_case(case("missing") | {"split": "reserved_test"})
