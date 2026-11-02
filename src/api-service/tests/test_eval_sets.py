"""The evaluation sets must agree with the server's time semantics and the synthetic data."""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from healthops.assistant.time_semantics import resolve
from healthops.assistant.toolbox import build_registry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

import build_sets  # noqa: E402

SETS = [
    "assistant_dev.synthetic.jsonl",
    "assistant_test.synthetic.jsonl",
    "assistant_heldout2.synthetic.jsonl",
    "assistant_protocol.synthetic.jsonl",
    "assistant_reference.synthetic.jsonl",
]
ROWS = {
    "assistant_dev.synthetic.jsonl": "DEV",
    "assistant_test.synthetic.jsonl": "TEST",
    "assistant_heldout2.synthetic.jsonl": "HELDOUT2",
    "assistant_protocol.synthetic.jsonl": "PROTOCOL",
    "assistant_reference.synthetic.jsonl": "REFERENCE",
}


def load(name):
    return [json.loads(line) for line in (ROOT / "eval" / name).read_text().splitlines() if line]


@pytest.mark.parametrize("name", SETS)
def test_committed_sets_match_the_generator(name):
    rows = getattr(build_sets, ROWS[name])
    regenerated = [
        {
            "id": i,
            "question": q,
            "timezone": build_sets.TZ,
            "today": build_sets.TODAY.isoformat(),
            "expected": e,
            "notes": n,
        }
        for i, q, e, n in rows
    ]
    assert load(name) == regenerated, "run python eval/build_sets.py"


@pytest.mark.parametrize("name", SETS)
def test_expected_arguments_validate_and_dates_come_from_listed_windows(name):
    registry = build_registry()
    for item in load(name):
        expected = item["expected"]
        if expected["tool"] in ("decline", "clarify", "lookup_reference"):
            assert expected["arguments"] is None
            continue
        spec = registry[expected["tool"]]
        semantics = resolve(item["timezone"], date.fromisoformat(item["today"]))
        listed = {
            semantics.today,
            semantics.yesterday,
            semantics.this_week_start,
            semantics.last_week_start,
            semantics.last_week_end,
            semantics.week_before_last_start,
            semantics.week_before_last_end,
            semantics.three_weeks_ago_start,
            semantics.three_weeks_ago_end,
            semantics.past_7_days_start,
            semantics.previous_7_days_start,
            semantics.previous_7_days_end,
            semantics.past_30_days_start,
            semantics.previous_30_days_start,
            semantics.previous_30_days_end,
        }
        params = spec.params.model_validate(expected["arguments"] | {"timezone": item["timezone"]})
        for field, value in params.model_dump().items():
            if isinstance(value, date):
                assert value in listed or value.isoformat() in item["question"], (
                    item["id"],
                    field,
                    value,
                )


def test_data_conditions_match_the_synthetic_history():
    for name in SETS:
        for item in load(name):
            expected = item["expected"]
            days = expected.get("expected_data_days")
            if not days:
                continue
            args = expected["arguments"]
            if expected["tool"] == "daily_summary":
                assert days["window"] == build_sets.days_with_data(
                    args["metric"],
                    date.fromisoformat(args["start_date"]),
                    date.fromisoformat(args["end_date"]),
                )
            else:
                assert days["baseline"] == build_sets.days_with_data(
                    args["metric"],
                    date.fromisoformat(args["baseline_start"]),
                    date.fromisoformat(args["baseline_end"]),
                )
                assert days["current"] == build_sets.days_with_data(
                    args["metric"],
                    date.fromisoformat(args["current_start"]),
                    date.fromisoformat(args["current_end"]),
                )
    # The notes about sparse and missing data are backed by the numbers.
    dev = {item["id"]: item for item in load("assistant_dev.synthetic.jsonl")}
    assert dev["dev-08"]["expected"]["expected_data_days"]["window"] == 2
    assert dev["dev-10"]["expected"]["expected_data_days"]["window"] == 0
    assert dev["dev-17"]["expected"]["expected_data_days"]["window"] == 0
    assert (
        dev["dev-12"]["expected"]["expected_data_days"]["window"] == 1
    )  # zero steps is still data


def test_question_sets_do_not_overlap():
    questions = [{item["question"] for item in load(name)} for name in SETS]
    for i in range(len(questions)):
        for j in range(i + 1, len(questions)):
            assert not (questions[i] & questions[j]), (SETS[i], SETS[j])


def test_protocol_set_covers_every_decline_category_and_every_tool():
    from healthops.assistant.toolbox import DECLINE_CATEGORIES

    rows = load("assistant_protocol.synthetic.jsonl")
    notes = " ".join(r["notes"] for r in rows if r["expected"]["tool"] == "decline")
    for category in DECLINE_CATEGORIES:
        assert category in notes, category
    tools = {r["expected"]["tool"] for r in rows}
    assert {"daily_summary", "period_comparison", "availability", "decline"} <= tools
