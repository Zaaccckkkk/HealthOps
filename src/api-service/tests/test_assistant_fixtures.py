"""Response fixtures for the iOS client, produced by the real orchestrator code.

Each fixture is an actual `AskResponse` from the orchestrator with a scripted model and the fake
toolbox (no database, no real LLM), so the Swift decoding tests see exactly what the server
sends, including the debug fields the phone must ignore. Set HEALTHOPS_UPDATE_FIXTURES=1 to
rewrite the files after a contract change; otherwise a stale fixture fails here.
"""

import json
import os
from pathlib import Path

import pytest
from test_assistant import (
    NO_DATA,
    NY,
    SUMMARY_WITH_GAP,
    ask,
    call,
    findings,
    make,
)

from healthops.assistant.contracts import AskResponse
from healthops.assistant.model import ModelTurn

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT.parent / "frontend-app/HealthOpsUploader/Tests/HealthOpsCoreTests/Fixtures/assistant"

DAILY = call(
    "daily_summary", metric="resting_heart_rate", start_date="2026-09-26", end_date="2026-09-28"
)
SLEEP_UNUSABLE = {
    "metric": "sleep_duration",
    "unit": "s",
    "metric_class": "cumulative",
    "window": {"timezone": NY, "start_date": "2026-10-01", "end_date": "2026-10-03"},
    "record_count": 3,
    "providers": [
        {
            "provider": "healthkit",
            "record_count": 3,
            "days": [
                {
                    "date": "2026-10-01",
                    "interval_total_count": 2,
                    "interval_total_sum": None,
                    "warnings": ["overlapping_intervals"],
                },
                {"date": "2026-10-02", "interval_total_count": 0, "interval_total_sum": None},
                {
                    "date": "2026-10-03",
                    "interval_total_count": 1,
                    "interval_total_sum": "23400.000000",
                },
            ],
        }
    ],
    "warnings": ["overlapping_intervals"],
    "definitions": {},
}


REFERENCE_RESULT = {
    "query": "SDNN versus RMSSD",
    "references": [
        {
            "source_id": "own-channels-and-hrv",
            "source_title": "Data channels and why HRV values are not comparable across them",
            "publisher": "HealthOps project",
            "url": "https://example.test/knowledge/own/channels-and-hrv.md",
            "license": "Project licence",
            "section": "HRV definitions differ by channel",
            "page": None,
            "quote_limit_chars": 1800,
            "text": "Apple Health reports heart rate variability as SDNN: the standard deviation "
            "of the intervals between heartbeats. Fitbit reports RMSSD: the root mean square "
            "of successive differences between heartbeat intervals.",
            "similarity": 0.81,
        }
    ],
    "warnings": [],
    "definitions": {},
}


def scenarios() -> dict[str, AskResponse]:
    out = {}
    a, _, _ = make(
        [
            ModelTurn(tool_calls=[DAILY]),
            ModelTurn(
                tool_calls=[
                    findings(
                        {
                            "type": "value_on_date",
                            "evidence_id": "e1.healthkit",
                            "date": "2026-09-26",
                        },
                        {
                            "type": "value_on_date",
                            "evidence_id": "e1.healthkit",
                            "date": "2026-09-27",
                        },
                        {
                            "type": "missing_data",
                            "evidence_id": "e1.healthkit",
                            "date": "2026-09-28",
                        },
                    )
                ]
            ),
        ],
        results={"daily_summary": SUMMARY_WITH_GAP},
    )
    out["answered_verified"] = ask(a)

    a, _, _ = make(
        [
            ModelTurn(tool_calls=[DAILY]),
            ModelTurn(
                tool_calls=[
                    call(
                        "state_findings",
                        findings=[
                            "difference e1.healthkit 450",
                            "value_on_date e1.healthkit 2026-09-28",
                        ],
                    )
                ]
            ),
        ],
        results={"daily_summary": SUMMARY_WITH_GAP},
    )
    out["answered_unverified"] = ask(a)

    a, _, _ = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="sleep_duration",
                        start_date="2026-10-01",
                        end_date="2026-10-03",
                    )
                ]
            ),
            ModelTurn(
                tool_calls=[
                    findings(
                        {
                            "type": "missing_data",
                            "evidence_id": "e1.healthkit",
                            "date": "2026-10-01",
                        },
                        {
                            "type": "value_on_date",
                            "evidence_id": "e1.healthkit",
                            "date": "2026-10-03",
                        },
                    )
                ]
            ),
        ],
        results={"daily_summary": SLEEP_UNUSABLE},
    )
    out["unusable_records"] = ask(a, "How much did I sleep this week?")

    a, _, _ = make(
        [ModelTurn(tool_calls=[DAILY]), ModelTurn(text="There are no records in that window.")],
        results={"daily_summary": NO_DATA},
    )
    out["insufficient_data"] = ask(a)

    a, _, _ = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "decline_request",
                        category="medical_advice",
                        detail="Your resting heart rate is 999 bpm.",
                    )
                ]
            )
        ]
    )
    out["refused"] = ask(a, "Do I have heart disease?")

    a, _, _ = make(
        [
            ModelTurn(
                tool_calls=[call("availability", start_date="2026-09-26", end_date="2026-10-02")]
            )
        ]
    )
    out["needs_clarification"] = ask(a, "Show my Fitbit steps for yesterday.")

    bad = call("daily_summary", metric="pulse", start_date="2026-09-26", end_date="2026-10-02")
    a, _, _ = make([ModelTurn(tool_calls=[bad]), ModelTurn(tool_calls=[bad])])
    out["tool_error"] = ask(a)

    # A definitional question answered from the reference library (synthetic passage).
    a, _, _ = make(
        [
            ModelTurn(tool_calls=[call("lookup_reference", query="SDNN versus RMSSD")]),
            ModelTurn(tool_calls=[call("state_findings", findings=["reference e1.r1"])]),
        ],
        results={"lookup_reference": REFERENCE_RESULT},
    )
    out["answered_reference"] = ask(a, "What is the difference between SDNN and RMSSD?")
    return out


def stable(response: AskResponse) -> dict:
    data = response.model_dump(mode="json")
    for record in data["tool_calls"]:
        record["duration_ms"] = 0  # the only field that varies between runs
    return data


def expected_files() -> dict[str, dict]:
    files = {name: stable(response) for name, response in scenarios().items()}
    # A future server value: the phone must decode it as unknown and never show success.
    unknown = json.loads(json.dumps(files["answered_verified"]))
    unknown["status"] = "future_status"
    unknown["cards"][0]["rows"][0]["quality"] = "future_quality"
    files["unknown_status"] = unknown
    return files


@pytest.mark.parametrize("name", sorted(expected_files()))
def test_fixture_matches_the_orchestrator_output(name):
    expected = expected_files()[name]
    path = FIXTURES / f"{name}.json"
    if os.environ.get("HEALTHOPS_UPDATE_FIXTURES") == "1":
        FIXTURES.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
    assert path.exists(), "run with HEALTHOPS_UPDATE_FIXTURES=1 to generate"
    assert json.loads(path.read_text()) == expected, "regenerate with HEALTHOPS_UPDATE_FIXTURES=1"


def test_fixtures_contain_only_synthetic_material():
    for path in FIXTURES.glob("*.json"):
        text = path.read_text()
        assert "00000000-0000-4000-8000" not in text or "user" not in text.lower()
        assert "Bearer" not in text


def test_refusal_fixture_keeps_the_model_detail_out_of_the_answer():
    refused = expected_files()["refused"]
    assert (
        "999" not in refused["answer"]
        and refused["model_text"] == "Your resting heart rate is 999 bpm."
    )
