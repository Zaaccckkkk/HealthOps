"""Output-boundary formatting and explicit non-guarantees, using synthetic evidence."""

import copy
from pathlib import Path

import pytest
from test_assistant_narrative import draft, run_writer

from healthops.assistant.output_format import format_answer


def facts():
    return {
        "sources": [
            {
                "evidence_id": "e1.healthkit",
                "provider": "healthkit",
                "unit": "s",
                "days": [{"date": "2026-08-05", "value": "23370"}],
                "recorded_values_summary": {"minimum": "23370", "maximum": "23940"},
            },
            {
                "evidence_id": "e2.google_health",
                "provider": "google_health",
                "unit": "ms",
                "days": [{"value": "25"}],
            },
        ]
    }


@pytest.mark.parametrize(
    "text,language,expected",
    [
        (
            "HealthKit recorded 23370 seconds on 2026-08-05.",
            "en",
            "Apple Health recorded about 6 hours 30 minutes on August 5, 2026.",
        ),
        (
            "HealthKit\u57282026-08-05\u8bb0\u5f55\u4e8623370\u79d2\u3002",
            "zh",
            "Apple Health\u57282026\u5e748\u67085\u65e5\u8bb0\u5f55\u4e86"
            "\u7ea66\u5c0f\u65f630\u5206\u949f\u3002",
        ),
        (
            "Apple's sleep; Google recorded 23940 seconds.",
            "en",
            "Apple Health's sleep; Google Health recorded 6 hours 39 minutes.",
        ),
        ("Apple Health and Google Health.", "en", "Apple Health and Google Health."),
        (
            "Apple Watch, Apple Music, Google Cloud and Google Fit.",
            "en",
            "Apple Watch, Apple Music, Google Cloud and Google Fit.",
        ),
        ("HRV was 25 ms and steps were 23370.", "en", "HRV was 25 ms and steps were 23370."),
    ],
)
def test_supported_prose_formats_without_changing_other_units(text, language, expected):
    evidence = facts()
    before = copy.deepcopy(evidence)
    result = format_answer(text, evidence, language)
    assert result.answer == expected
    assert evidence == before
    assert format_answer(result.answer, evidence, language).answer == expected


def test_explicit_units_and_date_format_override_only_final_user_request():
    context = (
        '{"user_dialogue":[{"role":"assistant","content":"Use ISO dates?"},'
        '{"role":"user","content":"Give the recorded sleep."}]}'
    )
    assert "August 5" in format_answer("2026-08-05", facts(), "en", context).answer
    result = format_answer(
        "HealthKit: about 23370 seconds on 2026-08-05.",
        facts(),
        "en",
        "Use ISO dates and exact seconds.",
    )
    assert result.answer == "Apple Health: 23370 seconds on 2026-08-05."
    assert (
        format_answer("\u7ea623370\u79d2", facts(), "zh", "\u6211\u9700\u8981\u79d2\u6570").answer
        == "23370\u79d2"
    )


def test_unverified_seconds_and_invalid_dates_are_not_silently_corrected():
    text = "99999 seconds on 2026-02-30; 25 seconds."
    result = format_answer(text, facts(), "en")
    assert result.answer == text
    assert set(result.warnings) == {
        "unsupported_seconds_left_unchanged",
        "invalid_calendar_date_left_unchanged",
    }


def test_providerless_missing_card_uses_explicit_question_source_only():
    evidence = {"sources": [{"evidence_id": "e1", "provider": None, "days": []}]}
    question = "Google\u8fd9\u4e00\u5929\u6709\u8bb0\u5f55\u5417\uff1f"
    assert format_answer("Google\u6ca1\u6709\u8bb0\u5f55", evidence, "zh", question).answer == (
        "Google Health\u6ca1\u6709\u8bb0\u5f55"
    )
    assert format_answer("Apple recorded.", evidence, "en").answer == "Apple recorded."


def test_machine_identifiers_code_urls_and_timestamps_remain_byte_identical():
    text = (
        "e1.healthkit `HealthKit 2026-08-05 23370 seconds` https://x.test/2026-08-05 "
        "2026-08-05T12:30:00Z"
    )
    assert format_answer(text, facts(), "en").answer == text


def test_formatter_does_not_claim_to_fix_facts_or_infer_an_unstated_unit():
    text = "Values were constant throughout. These changes are within normal ranges. Last was 25."
    assert format_answer(text, facts(), "en").answer == text


def test_formatting_applies_to_both_first_draft_and_editor_answer():
    for initial, editor in [
        (draft("HealthKit recorded changing resting heart rate."), None),
        (
            draft("Heart rate was 999999 bpm."),
            draft("HealthKit recorded changing resting heart rate."),
        ),
    ]:
        raw = initial.text
        response, _ = run_writer(initial, editor)
        assert response.narrative_status == "generated"
        assert "Apple Health recorded" in response.answer
        assert "HealthKit" not in response.answer
        assert initial.text == raw


def test_service_and_offline_formatter_are_identical():
    service = Path(__file__).parents[1] / "healthops/assistant/output_format.py"
    offline = Path(__file__).parents[2] / "model-training/writer_dpo/output_format.py"
    assert service.read_bytes() == offline.read_bytes()
