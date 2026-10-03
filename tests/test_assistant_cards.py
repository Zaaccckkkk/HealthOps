"""Deterministic cards and claim verification; no model, no database."""

from healthops.assistant.cards import build_cards, format_value
from healthops.assistant.claims import (
    FALLBACK_EXPLANATION,
    Claim,
    FindingsParams,
    render_explanation,
    verify_claim,
)

NY = "America/New_York"


def daily(metric, unit, metric_class, provider_days, warnings=()):
    return {
        "tool": "daily_summary",
        "arguments": {},
        "result": {
            "metric": metric,
            "unit": unit,
            "metric_class": metric_class,
            "window": {"timezone": NY, "start_date": "2026-10-01", "end_date": "2026-10-03"},
            "record_count": 3,
            "providers": [
                {"provider": p, "record_count": 3, "days": days}
                for p, days in provider_days.items()
            ],
            "warnings": list(warnings),
            "definitions": {},
        },
    }


def day(date, **fields):
    base = {
        "date": date,
        "sample_count": 0,
        "sample_mean": None,
        "daily_total": None,
        "interval_total_sum": None,
        "daily_summary": None,
        "warnings": [],
    }
    return base | fields


def comparison(metric, unit, before, after, diff, warnings=()):
    return {
        "tool": "period_comparison",
        "arguments": {},
        "result": {
            "metric": metric,
            "unit": unit,
            "baseline_window": {
                "timezone": NY,
                "start_date": "2026-09-21",
                "end_date": "2026-09-27",
            },
            "current_window": {
                "timezone": NY,
                "start_date": "2026-09-28",
                "end_date": "2026-10-02",
            },
            "providers": [
                {
                    "provider": "healthkit",
                    "baseline": {
                        "value": before,
                        "method": "mean_of_daily_sample_means",
                        "days_with_data": 7,
                        "days_in_window": 7,
                    },
                    "current": {
                        "value": after,
                        "method": "mean_of_daily_sample_means",
                        "days_with_data": 5,
                        "days_in_window": 5,
                    },
                    "difference": diff,
                    "warnings": list(warnings),
                }
            ],
            "warnings": list(warnings),
        },
    }


def test_seconds_are_converted_by_integer_arithmetic_and_shown_with_the_raw_value():
    assert format_value("24300.000000", "s") == "6 h 45 min (24300 s)"
    assert format_value("3600", "s") == "1 h 0 min (3600 s)"
    assert format_value("0", "count") == "0 count"
    assert format_value("60.500000", "bpm") == "60.5 bpm"
    assert format_value("57.750000", "bpm") == "57.8 bpm"  # rounded half-even, not truncated
    assert format_value("55.000000", "bpm") == "55 bpm"
    assert format_value(None, "bpm") == "no data"


def test_daily_card_marks_missing_days_as_no_data_never_zero():
    cards = build_cards(
        [
            daily(
                "sleep_duration",
                "s",
                "cumulative",
                {
                    "healthkit": [
                        day(
                            "2026-10-01",
                            interval_total_sum="24300.000000",
                            warnings=["interval_crosses_day_boundary"],
                        ),
                        day("2026-10-02"),
                        day("2026-10-03", daily_total="0.000000"),
                    ]
                },
            ),
        ]
    )
    card = cards[0]
    assert card.evidence_id == "e1.healthkit" and card.kind == "daily_table"
    assert [r.display for r in card.rows] == ["6 h 45 min (24300 s)", "no data", "0 h 0 min (0 s)"]
    assert [r.kind for r in card.rows] == ["interval_total_sum", "none", "daily_total"]
    assert card.rows[1].value is None
    assert "mixed statistics" in card.statistic
    assert any("assigned to the day they start" in t for t in card.warning_text)


def test_interval_sums_are_labelled_as_not_full_day_coverage():
    cards = build_cards(
        [
            daily(
                "steps",
                "count",
                "cumulative",
                {
                    "google_health": [
                        day("2026-10-01", interval_total_sum="4500.000000"),
                        day("2026-10-02", interval_total_sum="800.000000"),
                        day("2026-10-03", interval_total_sum="100.000000"),
                    ]
                },
            )
        ]
    )
    assert cards[0].statistic == "sum of recorded intervals (not full-day coverage)"
    assert cards[0].rows[0].display == "4,500 count"


def test_comparison_card_direction_is_computed_from_the_difference():
    lower = build_cards(
        [comparison("resting_heart_rate", "bpm", "57.750000", "56.833333", "-0.916667")]
    )[0]
    assert lower.direction == "lower" and lower.current["display"] == "56.8 bpm"
    none = build_cards(
        [comparison("active_energy", "kcal", None, None, None, ["insufficient_data"])]
    )[0]
    assert none.direction == "unavailable"


def test_value_claim_renders_from_the_card_not_from_the_model():
    cards = build_cards(
        [
            daily(
                "resting_heart_rate",
                "bpm",
                "point",
                {
                    "healthkit": [
                        day("2026-10-01", sample_count=1, sample_mean="55.000000"),
                        day("2026-10-02"),
                        day("2026-10-03"),
                    ]
                },
            )
        ]
    )
    ok = verify_claim(
        Claim(type="value_on_date", evidence_id="e1.healthkit", date="2026-10-01"), cards
    )
    assert ok.valid and "55 bpm" in ok.sentence and "mean of recorded samples" in ok.sentence
    missing_value = verify_claim(
        Claim(type="value_on_date", evidence_id="e1.healthkit", date="2026-10-02"), cards
    )
    assert not missing_value.valid and missing_value.reason == "no recorded value on that date"
    outside = verify_claim(
        Claim(type="value_on_date", evidence_id="e1.healthkit", date="2026-09-01"), cards
    )
    assert not outside.valid
    unknown = verify_claim(Claim(type="value_on_date", evidence_id="e9", date="2026-10-01"), cards)
    assert unknown.reason == "unknown evidence id"


def test_missing_data_claims_require_actual_absence():
    cards = build_cards(
        [
            daily(
                "resting_heart_rate",
                "bpm",
                "point",
                {
                    "healthkit": [
                        day("2026-10-01", sample_count=1, sample_mean="55.000000"),
                        day("2026-10-02"),
                        day("2026-10-03"),
                    ]
                },
            )
        ]
    )
    assert verify_claim(
        Claim(type="missing_data", evidence_id="e1.healthkit", date="2026-10-02"), cards
    ).valid
    assert not verify_claim(
        Claim(type="missing_data", evidence_id="e1.healthkit", date="2026-10-01"), cards
    ).valid
    assert not verify_claim(
        Claim(type="missing_data", evidence_id="e1.healthkit"), cards
    ).valid  # window has values


def test_difference_claim_uses_backend_direction_and_window_text():
    cards = build_cards(
        [comparison("resting_heart_rate", "bpm", "57.750000", "56.833333", "-0.916667")]
    )
    verified = verify_claim(Claim(type="window_difference", evidence_id="e1.healthkit"), cards)
    assert verified.valid
    assert (
        "was lower in 2026-09-28 to 2026-10-02 than in 2026-09-21 to 2026-09-27"
        in verified.sentence
    )
    assert "56.8 bpm versus 57.8 bpm" in verified.sentence
    unavailable = build_cards(
        [comparison("active_energy", "kcal", None, None, None, ["insufficient_data"])]
    )
    assert not verify_claim(
        Claim(type="window_difference", evidence_id="e1.healthkit"), unavailable
    ).valid
    assert verify_claim(Claim(type="missing_data", evidence_id="e1.healthkit"), unavailable).valid


def test_one_invalid_claim_degrades_the_whole_explanation():
    cards = build_cards(
        [
            daily(
                "resting_heart_rate",
                "bpm",
                "point",
                {
                    "healthkit": [
                        day("2026-10-01", sample_count=1, sample_mean="55.000000"),
                        day("2026-10-02"),
                        day("2026-10-03"),
                    ]
                },
            )
        ]
    )
    findings = FindingsParams(
        findings=[
            "value_on_date e1.healthkit 2026-10-01",
            "value_on_date e1.healthkit 2026-10-02",  # no data there
        ]
    )
    text, verified, all_valid = render_explanation(findings, cards)
    assert not all_valid and text == FALLBACK_EXPLANATION
    assert [v.valid for v in verified] == [True, False]
    good = FindingsParams(findings=["value_on_date e1.healthkit 2026-10-01"])
    text, _, all_valid = render_explanation(good, cards)
    assert all_valid and text.startswith(
        "On 2026-10-01, your resting_heart_rate (healthkit) was 55 bpm"
    )


def test_findings_grammar_is_tolerant_but_verification_is_strict():
    params = FindingsParams(
        findings=[
            "value_on_date e1.healthkit 2026-10-01",
            "value_on_date, e1.healthkit, 2026-10-01",  # same claim, commas: deduplicated
            "window_difference e1.healthkit",
            "something else entirely",
        ]
    )
    parsed = params.claims
    assert (
        len(parsed) == 3
        and isinstance(parsed[0], Claim)
        and parsed[-1] == "something else entirely"
    )
    cards = build_cards(
        [
            daily(
                "resting_heart_rate",
                "bpm",
                "point",
                {
                    "healthkit": [
                        day("2026-10-01", sample_count=1, sample_mean="55.000000"),
                        day("2026-10-02"),
                        day("2026-10-03"),
                    ]
                },
            )
        ]
    )
    text, verified, all_valid = render_explanation(params, cards)
    assert not all_valid and text == FALLBACK_EXPLANATION
    assert (
        verified[-1].reason == "unparseable finding"
        and verified[-1].raw == "something else entirely"
    )


def test_findings_parser_ignores_extra_tokens_and_takes_the_first_iso_date():
    params = FindingsParams(
        findings=[
            "window_difference e1.healthkit sleep_duration 2026-09-03 2026-10-02",
            "value_on_date e1.healthkit resting_heart_rate 2026-09-26",
        ]
    )
    first, second = params.claims
    assert first.type == "window_difference" and first.date == "2026-09-03"
    assert second.type == "value_on_date" and second.date == "2026-09-26"


def test_missing_data_on_a_no_records_card_is_valid_inside_its_window():
    no_records = {
        "tool": "daily_summary",
        "arguments": {},
        "result": {
            "metric": "heart_rate",
            "unit": "bpm",
            "metric_class": "point",
            "window": {"timezone": NY, "start_date": "2026-09-11", "end_date": "2026-09-11"},
            "record_count": 0,
            "providers": [],
            "warnings": ["no_records_in_window"],
            "definitions": {},
        },
    }
    cards = build_cards([no_records])
    assert cards[0].evidence_id == "e1" and cards[0].rows == []
    inside = verify_claim(Claim(type="missing_data", evidence_id="e1", date="2026-09-11"), cards)
    assert inside.valid and inside.sentence == "No heart_rate records on 2026-09-11."
    outside = verify_claim(Claim(type="missing_data", evidence_id="e1", date="2026-09-12"), cards)
    assert not outside.valid
    assert verify_claim(Claim(type="missing_data", evidence_id="e1"), cards).valid


def test_long_finding_lists_degrade_instead_of_failing_the_protocol():
    params = FindingsParams(findings=[f"missing_data e1 2026-09-{d:02d}" for d in range(1, 30)])
    assert len(params.claims) == 29


def test_records_without_a_reliable_value_are_not_reported_as_no_records():
    cards = build_cards(
        [
            daily(
                "sleep_duration",
                "s",
                "cumulative",
                {
                    "healthkit": [
                        day(
                            "2026-10-01",
                            interval_total_count=2,
                            interval_total_sum=None,
                            warnings=["overlapping_intervals"],
                        ),
                        day("2026-10-02"),
                        day(
                            "2026-10-03", interval_total_count=1, interval_total_sum="21600.000000"
                        ),
                    ]
                },
            )
        ]
    )
    rows = cards[0].rows
    assert [r.quality for r in rows] == ["unusable", "none", "value"]
    assert rows[0].display == "no reliable value" and rows[0].records == 2
    unusable = verify_claim(
        Claim(type="missing_data", evidence_id="e1.healthkit", date="2026-10-01"), cards
    )
    assert unusable.valid and "records exist (healthkit) but no reliable value" in unusable.sentence
    assert "overlap" in unusable.sentence
    none = verify_claim(
        Claim(type="missing_data", evidence_id="e1.healthkit", date="2026-10-02"), cards
    )
    assert none.valid and none.sentence == "No sleep_duration records on 2026-10-02 (healthkit)."
    whole = verify_claim(Claim(type="missing_data", evidence_id="e1.healthkit"), cards)
    assert not whole.valid  # the window has a recorded value on 10-03


def test_window_missing_data_distinguishes_unusable_records_from_none():
    cards = build_cards(
        [
            daily(
                "sleep_duration",
                "s",
                "cumulative",
                {
                    "healthkit": [
                        day(
                            "2026-10-01", interval_total_count=2, warnings=["overlapping_intervals"]
                        ),
                        day("2026-10-02"),
                        day("2026-10-03"),
                    ]
                },
            )
        ]
    )
    whole = verify_claim(Claim(type="missing_data", evidence_id="e1.healthkit"), cards)
    assert whole.valid and "records exist on 1 day(s) but could not be summarised" in whole.sentence


def test_dates_must_be_real_calendar_days():
    no_records = {
        "tool": "daily_summary",
        "arguments": {},
        "result": {
            "metric": "heart_rate",
            "unit": "bpm",
            "metric_class": "point",
            "window": {"timezone": NY, "start_date": "2026-09-28", "end_date": "2026-10-02"},
            "record_count": 0,
            "providers": [],
            "warnings": ["no_records_in_window"],
            "definitions": {},
        },
    }
    cards = build_cards([no_records])
    bogus = verify_claim(Claim(type="missing_data", evidence_id="e1", date="2026-09-31"), cards)
    assert not bogus.valid and bogus.reason == "not a calendar date"
    assert verify_claim(
        Claim(type="missing_data", evidence_id="e1", date="2026-09-30"), cards
    ).valid
    assert not verify_claim(
        Claim(type="missing_data", evidence_id="e1", date="2026-10-03"), cards
    ).valid
    leap = {
        **no_records,
        "result": {
            **no_records["result"],
            "window": {"timezone": NY, "start_date": "2028-02-28", "end_date": "2028-03-01"},
        },
    }
    assert verify_claim(
        Claim(type="missing_data", evidence_id="e1", date="2028-02-29"), build_cards([leap])
    ).valid
    text, _, all_valid = render_explanation(
        FindingsParams(findings=["missing_data e1 2026-09-31"]), cards
    )
    assert not all_valid and text == FALLBACK_EXPLANATION
