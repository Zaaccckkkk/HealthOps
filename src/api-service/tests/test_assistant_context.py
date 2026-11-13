"""Synthetic request-size tests: preserve evidence while sharing repeated definitions."""

import copy
import json

from eval.pretraining_cases import multibudget_cases
from healthops.assistant.context import compact_messages, request_size


def test_compaction_preserves_values_warnings_ids_and_conflicting_definitions():
    definitions = {"basis": "Recorded interval total; not full-day coverage. " * 30}
    original = [
        {"role": "system", "content": "Fixture"},
        *[
            {
                "role": "tool",
                "name": "daily_summary",
                "tool_call_id": str(i),
                "content": json.dumps(
                    {
                        "definitions": definitions if i < 4 else {"basis": "Changed meaning"},
                        "hint": "Use evidence references.",
                        "evidence": [{"evidence_id": f"e{i}", "provider": "healthkit"}],
                        "providers": [{"days": [{"date": "2026-01-01", "value": 0}]}],
                        "warnings": ["partial_day", "overlap"],
                    }
                ),
            }
            for i in range(5)
        ],
    ]
    snapshot = copy.deepcopy(original)
    compact = compact_messages(original)
    assert original == snapshot
    assert request_size(compact) < request_size(original) / 2
    for before, after in zip(original[1:], compact[1:], strict=True):
        a, b = json.loads(before["content"]), json.loads(after["content"])
        for key in ("providers", "warnings", "evidence"):
            assert a[key] == b[key]
        assert before["tool_call_id"] == after["tool_call_id"]
    assert json.loads(compact[2]["content"])["reuse_prior_tool_definitions"] == ["basis"]
    assert json.loads(compact[-1]["content"])["definitions"]["basis"] == "Changed meaning"


def test_unicode_count_does_not_charge_six_characters_per_escaped_character():
    messages = [{"role": "user", "content": "\u7761\u7720" * 100}]
    assert request_size(messages) < 300
    assert len(json.dumps(messages)) > 1000


def test_multibudget_probes_preserve_three_cohorts_and_exact_scope():
    rows = multibudget_cases()
    assert len(rows) == 6 and len({r["source_user_id"] for r in rows}) == 3
    for row in rows:
        assert row["split"] == "development" and row["data_kind"] == "synthetic"
        assert len(row["questions"]) == 3
        assert len(row["expected_queries"]) == int(row["family"].split("_")[-1])
