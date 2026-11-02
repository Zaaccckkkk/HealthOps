"""Toolbox argument handling without a database."""

import asyncio
from uuid import UUID

from healthops.assistant.toolbox import Toolbox

USER = UUID("00000000-0000-4000-8000-000000000001")
NY = "America/New_York"


def execute(name, arguments_json):
    return asyncio.run(Toolbox(None).execute(name, arguments_json, USER, timezone=NY))


def test_reversed_windows_with_a_missing_field_report_validation_not_a_crash():
    outcome = execute(
        "period_comparison",
        '{"metric": "steps", "baseline_start": "2026-09-21", '
        '"current_start": "2026-09-14", "current_end": "2026-09-20"}',
    )
    assert outcome.record.status == "invalid_arguments"
    assert "baseline_end" in outcome.record.error
    assert outcome.record.model_arguments["baseline_start"] == "2026-09-21"


def test_complete_reversed_windows_are_reordered_and_both_argument_sets_recorded():
    outcome = execute(
        "period_comparison",
        '{"metric": "steps", "baseline_start": "2026-09-21", "baseline_end": "2026-09-27", '
        '"current_start": "2026-09-14", "current_end": "2026-09-20"}',
    )
    # No database here: the reordering happened, then execution reported the missing database.
    assert outcome.record.status == "error" and outcome.record.error == "no database"
    assert outcome.record.arguments["baseline_start"] == "2026-09-14"
    assert outcome.record.model_arguments["baseline_start"] == "2026-09-21"


def test_provider_written_by_the_model_is_dropped_but_remembered():
    outcome = execute(
        "daily_summary",
        '{"metric": "steps", "provider": "healthkit", '
        '"start_date": "2026-10-01", "end_date": "2026-10-01"}',
    )
    assert outcome.record.arguments["provider"] is None
    assert outcome.record.model_arguments["provider"] == "healthkit"
