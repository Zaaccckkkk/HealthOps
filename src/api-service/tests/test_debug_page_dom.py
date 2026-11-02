"""The debug page must keep rendering the data cards when a finding is unparseable.

Runs the page's own script under Node with a minimal DOM stand-in (tests/debug_page_dom.js).
Skipped when Node is not installed; the page is otherwise exercised manually in a browser.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "healthops/assistant/debug_page.html"
HARNESS = ROOT / "tests/debug_page_dom.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")


def render(response: dict, tmp_path: Path) -> dict:
    payload = tmp_path / "response.json"
    payload.write_text(json.dumps(response))
    result = subprocess.run(
        ["node", str(HARNESS), str(PAGE), str(payload)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def base_response(**overrides):
    response = {
        "status": "answered",
        "explanation_status": "unverified",
        "answer": "An explanation could not be generated reliably for this answer.",
        "claims": [],
        "cards": [
            {
                "evidence_id": "e1.healthkit",
                "kind": "daily_table",
                "title": "sleep_duration per day (healthkit)",
                "tool": "daily_summary",
                "timezone": "America/New_York",
                "start_date": "2026-10-01",
                "end_date": "2026-10-02",
                "provider": "healthkit",
                "metric": "sleep_duration",
                "unit": "s",
                "statistic": "sum of recorded intervals (not full-day coverage)",
                "rows": [
                    {
                        "date": "2026-10-01",
                        "kind": "interval_total_sum",
                        "value": "23400.000000",
                        "display": "6 h 30 min (23400 s)",
                        "quality": "value",
                        "records": 1,
                        "warnings": [],
                    },
                    {
                        "date": "2026-10-02",
                        "kind": "none",
                        "value": None,
                        "display": "no reliable value",
                        "quality": "unusable",
                        "records": 2,
                        "warnings": ["overlapping_intervals"],
                    },
                ],
                "warnings": ["overlapping_intervals"],
                "warning_text": ["Intervals overlap."],
                "series": [],
                "baseline": None,
                "current": None,
                "difference": None,
                "direction": None,
            }
        ],
        "model_text": None,
        "tool_calls": [
            {
                "name": "daily_summary",
                "status": "ok",
                "arguments": {},
                "model_arguments": {},
                "warnings": [],
                "duration_ms": 12,
                "error": None,
            }
        ],
        "numeric_consistency": {
            "status": "not_applicable",
            "numbers_in_answer": [],
            "unmatched": [],
            "note": "",
        },
        "time_semantics": {
            "timezone": "America/New_York",
            "today": "2026-10-02",
            "last_week_start": "2026-09-21",
            "last_week_end": "2026-09-27",
            "past_7_days_start": "2026-09-26",
        },
        "model": {
            "name": "scripted",
            "quantization": None,
            "thinking": None,
            "prompt_version": "test",
        },
        "disclaimer": "Not medical advice.",
    }
    response.update(overrides)
    return response


def test_unparseable_finding_keeps_the_cards_on_the_page(tmp_path):
    response = base_response(
        claims=[
            {
                "claim": None,
                "raw": "difference e1.healthkit 450.000000",
                "valid": False,
                "reason": "unparseable finding",
                "sentence": None,
            },
            {
                "claim": {"type": "window_difference", "evidence_id": "e1.healthkit", "date": None},
                "raw": None,
                "valid": True,
                "reason": None,
                "sentence": "x",
            },
        ]
    )
    rendered = render(response, tmp_path)
    assert "6 h 30 min (23400 s)" in rendered["text"]  # the card is still there
    assert (
        "(unparseable)" in rendered["text"]
        and "difference e1.healthkit 450.000000" in rendered["text"]
    )
    assert "Could not render" not in rendered["text"]


def test_unusable_rows_are_labelled_and_not_shown_as_zero(tmp_path):
    rendered = render(base_response(), tmp_path)
    assert (
        "no reliable value" in rendered["text"] and "records, no reliable value" in rendered["text"]
    )
    assert " 0 s" not in rendered["text"]


def test_a_broken_section_does_not_remove_the_rest(tmp_path):
    response = base_response(tool_calls=None)  # malformed: forces an error in one section
    rendered = render(response, tmp_path)
    assert "6 h 30 min (23400 s)" in rendered["text"]
    assert "Could not render tool calls" in rendered["text"]
