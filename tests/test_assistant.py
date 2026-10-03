"""Orchestrator boundaries with a scripted model and a fake toolbox; no database, no real LLM.

These tests prove engineering behaviour (identity injection, budgets, status codes, numeric
diagnostic). They say nothing about model quality, which is measured separately by
scripts/eval_assistant.py against a real local model.
"""

import asyncio
import json
from datetime import date
from uuid import UUID

import pytest

from healthops.assistant import numeric
from healthops.assistant.claims import FALLBACK_EXPLANATION, FindingsParams
from healthops.assistant.contracts import AskRequest, ToolCallRecord
from healthops.assistant.model import ModelError, ModelTurn, ScriptedChatModel, ToolCallRequest
from healthops.assistant.orchestrator import Assistant, AssistantSettings, TodayOverrideNotAllowed
from healthops.assistant.time_semantics import resolve
from healthops.assistant.toolbox import Toolbox, ToolOutcome, has_usable_data

USER = UUID("00000000-0000-4000-8000-000000000001")
NY = "America/New_York"


class FakeToolbox(Toolbox):
    """Validates with the real schemas, but returns canned results instead of touching a DB."""

    def __init__(self, results: dict[str, dict]):
        super().__init__(session_factory=None)
        self.results = results
        self.calls: list[tuple[str, dict, UUID]] = []

    async def execute(self, name, arguments_json, user_id, timezone=None, provider=None):
        spec = self.registry.get(name)
        if spec is None:
            return await super().execute(name, arguments_json, user_id, timezone, provider)
        raw = json.loads(arguments_json or "{}")
        model_arguments = dict(raw)
        if spec.takes_timezone and timezone is not None:
            raw["timezone"] = timezone
        notes = []
        if "provider" in raw and "provider" in spec.params.model_fields:
            raw.pop("provider")
            notes.append("provider_filter_ignored")
        if provider is not None and "provider" in spec.params.model_fields:
            raw["provider"] = provider
            notes.append("provider_from_request")
        windows = ("baseline_start", "baseline_end", "current_start", "current_end")
        if (
            name == "period_comparison"
            and all(isinstance(raw.get(f), str) for f in windows)
            and raw["baseline_start"] > raw["current_start"]
        ):
            raw["baseline_start"], raw["current_start"] = (
                raw["current_start"],
                raw["baseline_start"],
            )
            raw["baseline_end"], raw["current_end"] = raw["current_end"], raw["baseline_end"]
            notes.append("windows_reordered_baseline_is_earlier")
        try:
            params = spec.params.model_validate(raw)
        except Exception:
            return await super().execute(name, arguments_json, user_id, timezone, provider)
        arguments = params.model_dump(mode="json")
        self.calls.append((name, arguments, user_id))
        if spec.run is None:
            record = ToolCallRecord(
                name=name, arguments=arguments, model_arguments=model_arguments, status="ok"
            )
            if isinstance(params, FindingsParams):
                return ToolOutcome(record, None, None, findings=params)
            return ToolOutcome(record, None, None, declined=True)
        result = self.results[name]
        record = ToolCallRecord(
            name=name,
            arguments=arguments,
            model_arguments=model_arguments,
            status="ok",
            warnings=notes + result.get("warnings", []),
        )
        return ToolOutcome(record, result, result, has_usable_data=has_usable_data(name, result))


def call(name, **arguments):
    return ToolCallRequest(id="c1", name=name, arguments_json=json.dumps(arguments))


def run(coro):
    return asyncio.run(coro)


SUMMARY = {
    "metric": "resting_heart_rate",
    "unit": "bpm",
    "metric_class": "point",
    "window": {"timezone": NY, "start_date": "2026-09-26", "end_date": "2026-10-02"},
    "record_count": 7,
    "providers": [
        {
            "provider": "healthkit",
            "record_count": 7,
            "days": [
                {
                    "date": "2026-09-26",
                    "sample_count": 1,
                    "sample_mean": "60.500000",
                    "warnings": [],
                },
                {
                    "date": "2026-09-27",
                    "sample_count": 1,
                    "sample_mean": "62.000000",
                    "warnings": [],
                },
            ],
        }
    ],
    "warnings": ["sparse_window"],
    "definitions": {},
}
NO_DATA = {**SUMMARY, "providers": [], "record_count": 0, "warnings": ["no_records_in_window"]}
SUMMARY_WITH_GAP = {
    **SUMMARY,
    "window": {"timezone": NY, "start_date": "2026-09-26", "end_date": "2026-09-28"},
    "providers": [
        {
            "provider": "healthkit",
            "record_count": 2,
            "days": [
                {
                    "date": "2026-09-26",
                    "sample_count": 1,
                    "sample_mean": "60.500000",
                    "daily_summary": None,
                    "daily_total": None,
                    "interval_total_sum": None,
                    "warnings": [],
                },
                {
                    "date": "2026-09-27",
                    "sample_count": 1,
                    "sample_mean": "62.000000",
                    "daily_summary": None,
                    "daily_total": None,
                    "interval_total_sum": None,
                    "warnings": [],
                },
                {
                    "date": "2026-09-28",
                    "sample_count": 0,
                    "sample_mean": None,
                    "daily_summary": None,
                    "daily_total": None,
                    "interval_total_sum": None,
                    "warnings": [],
                },
            ],
        }
    ],
    "warnings": [],
}


def make(turns, results=None, **settings):
    model = ScriptedChatModel(turns)
    toolbox = FakeToolbox(results or {"daily_summary": SUMMARY})
    return Assistant(model, toolbox, AssistantSettings(**settings)), model, toolbox


def ask(assistant, question="How was my resting heart rate this week?", today=None, tz=NY):
    return run(
        assistant.ask(
            AskRequest(question=question, timezone=tz, today=today), USER, today=date(2026, 10, 2)
        )
    )


def test_tool_call_is_validated_and_executed_with_server_identity():
    assistant, model, toolbox = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="resting_heart_rate",
                        timezone=NY,
                        start_date="2026-09-26",
                        end_date="2026-10-02",
                    )
                ]
            ),
            ModelTurn(
                text="Your resting heart rate averaged 60.5 and 62 bpm on the two recorded "
                "days (healthkit); the window is sparse."
            ),
        ]
    )
    response = ask(assistant)
    assert response.status == "answered"
    assert response.explanation_status == "unverified"  # prose, not structured claims
    assert response.answer == FALLBACK_EXPLANATION
    assert response.model_text.startswith("Your resting heart rate averaged")
    assert response.cards and response.cards[0].evidence_id == "e1.healthkit"
    assert toolbox.calls[0][2] == USER  # identity injected by the server, never by the model
    # No identity field in any tool schema: the model cannot choose whose data to read.
    properties = {
        name
        for tool in model.requests[0][1]
        for name in tool["function"]["parameters"]["properties"]
    }
    assert not {p for p in properties if "user" in p}
    assert response.tool_calls[0].status == "ok" and response.tool_calls[0].warnings == [
        "sparse_window"
    ]
    assert response.evidence[0]["result"]["record_count"] == 7
    assert response.time_semantics.last_week_start == date(2026, 9, 21)
    assert "not medical advice" in response.disclaimer


def test_model_never_receives_dates_to_compute_itself():
    assistant, model, _ = make([ModelTurn(text="No numbers here.")])
    ask(assistant)
    system = model.requests[0][0][0]["content"]
    assert "Last week: 2026-09-21 to 2026-09-27" in system
    assert "Past 7 days: 2026-09-26 to 2026-10-02" in system
    assert "Timezone: America/New_York" in system


def test_numeric_consistency_flags_numbers_absent_from_evidence_but_is_not_fact_checking():
    # The diagnostic now runs on the rendered answer; free prose is checked directly here.
    evidence = [{"result": SUMMARY}]
    diagnostic = numeric.check(
        "It dropped from 62 to 60.5 bpm, and 99 is a made-up number.", evidence
    )
    assert diagnostic.status == "unmatched" and diagnostic.unmatched == ["99"]
    assert "does not verify direction" in diagnostic.note


def _unused_prose_flow():
    assistant, _, _ = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="resting_heart_rate",
                        timezone=NY,
                        start_date="2026-09-26",
                        end_date="2026-10-02",
                    )
                ]
            ),
            # 62 and 60.5 both exist in the evidence, so a swapped direction still passes:
            # this is a diagnostic, not fact checking.
            ModelTurn(text="It dropped from 62 to 60.5 bpm, and 99 is a made-up number."),
        ]
    )
    response = ask(assistant)
    assert response.numeric_consistency.status == "unmatched"
    assert response.numeric_consistency.unmatched == ["99"]
    assert "does not verify direction" in response.numeric_consistency.note


def test_answers_without_numbers_are_not_applicable_not_passing():
    assistant, _, _ = make([ModelTurn(text="There is not enough information to say.")])
    assert ask(assistant).numeric_consistency.status == "not_applicable"


def test_dates_are_not_counted_as_numbers():
    assert numeric.numbers_in_text(
        "On 2026-10-01T12:00:00Z the mean was 60.5 bpm, 1,200 steps."
    ) == ["60.5", "1200"]
    # 60.5 rounds half-even to 60, so "60" matches, "61" does not, and "60.5" matches exactly.
    evidence = [{"result": {"sample_mean": "60.500000"}}]
    assert numeric.check("Mean 60 bpm", evidence).status == "consistent"
    assert numeric.check("Mean 61 bpm", evidence).unmatched == ["61"]
    assert numeric.check("Mean 60.5 bpm", evidence).status == "consistent"


def test_insufficient_data_status_when_every_tool_result_has_no_records():
    assistant, _, _ = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="active_energy",
                        timezone=NY,
                        start_date="2026-09-26",
                        end_date="2026-10-02",
                    )
                ]
            ),
            ModelTurn(text="There are no active energy records in that window."),
        ],
        results={"daily_summary": NO_DATA},
    )
    assert ask(assistant).status == "insufficient_data"


def test_decline_tool_produces_refused_status_with_fixed_text_only():
    assistant, _, toolbox = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "decline_request",
                        category="medical_advice",
                        detail="Your resting heart rate is 999 bpm.",  # must never reach answer
                    )
                ]
            ),
        ]
    )
    response = ask(assistant, "Do I have heart disease?")
    assert response.status == "refused"
    assert "999" not in response.answer and "medical advice" in response.answer
    assert response.model_text == "Your resting heart rate is 999 bpm."  # debug field only
    assert response.evidence == [] and toolbox.calls[0][0] == "decline_request"


def test_unknown_decline_category_maps_to_the_generic_fixed_text():
    assistant, _, _ = make([ModelTurn(tool_calls=[call("decline_request", category="weather")])])
    response = ask(assistant, "What is the weather?")
    assert response.status == "refused" and response.answer.endswith("outside that scope.")


def test_invalid_arguments_get_one_retry_then_tool_error():
    bad = call(
        "daily_summary", metric="pulse", timezone=NY, start_date="2026-09-26", end_date="2026-10-02"
    )
    assistant, model, _ = make([ModelTurn(tool_calls=[bad]), ModelTurn(tool_calls=[bad])])
    response = ask(assistant)
    assert response.status == "tool_error"
    assert [c.status for c in response.tool_calls] == ["invalid_arguments", "invalid_arguments"]
    assert "pulse" not in response.tool_calls[0].error  # field locations only, no values echoed
    # The model saw the structured error once before being stopped.
    tool_message = model.requests[1][0][-1]
    assert tool_message["role"] == "tool" and "invalid fields" in tool_message["content"]


def test_unknown_tool_is_rejected():
    assistant, _, _ = make(
        [ModelTurn(tool_calls=[call("run_sql", query="select 1")]), ModelTurn(text="ok")]
    )
    response = ask(assistant)
    assert response.tool_calls[0].status == "invalid_arguments"
    assert response.tool_calls[0].error == "unknown tool"


def test_tool_call_budget_is_enforced():
    good = call(
        "daily_summary", metric="resting_heart_rate", start_date="2026-09-26", end_date="2026-10-02"
    )
    assistant, _, _ = make([ModelTurn(tool_calls=[good, good])], max_tool_calls=1)
    response = ask(assistant)
    assert response.status == "budget_exceeded"
    assert [c.status for c in response.tool_calls] == ["ok", "not_executed"]


def test_model_turn_budget_is_enforced():
    good = call(
        "daily_summary", metric="resting_heart_rate", start_date="2026-09-26", end_date="2026-10-02"
    )
    assistant, _, _ = make([ModelTurn(tool_calls=[good])] * 3, max_model_turns=2, max_tool_calls=10)
    assert ask(assistant).status == "budget_exceeded"


def test_model_context_budget_counts_the_whole_request_before_any_model_call():
    assistant, model, _ = make([ModelTurn(text="never")], model_context_budget_chars=1)
    response = ask(assistant)
    assert response.status == "budget_exceeded" and model.requests == []
    assert "context budget" in response.answer


def test_model_context_budget_refuses_instead_of_truncating():
    good = call(
        "daily_summary", metric="resting_heart_rate", start_date="2026-09-26", end_date="2026-10-02"
    )
    # Measure the first request, then allow it but not the follow-up that carries the tool result.
    probe, probe_model, _ = make([ModelTurn(text="probe")], model_context_budget_chars=10**9)
    ask(probe)
    messages, tools = probe_model.requests[0]
    first_request = len(json.dumps(messages, separators=(",", ":"))) + len(
        json.dumps(tools, separators=(",", ":"))
    )
    assistant, model, _ = make(
        [ModelTurn(tool_calls=[good]), ModelTurn(text="never reached")],
        model_context_budget_chars=first_request + 50,
    )
    response = ask(assistant)
    assert response.status == "budget_exceeded"
    assert len(model.requests) == 1  # the model was asked once; the second request was refused
    assert "context budget" in response.answer
    assert response.evidence[0]["result"]["record_count"] == 7  # API evidence keeps the result


def test_evidence_budget_marks_omissions_explicitly():
    good = call(
        "daily_summary", metric="resting_heart_rate", start_date="2026-09-26", end_date="2026-10-02"
    )
    assistant, _, _ = make(
        [ModelTurn(tool_calls=[good]), ModelTurn(text="done")], evidence_budget_bytes=10
    )
    response = ask(assistant)
    assert response.evidence[0]["omitted"] is True and response.evidence[0]["result"] is None


def test_model_error_is_reported_not_hidden():
    class Broken(ScriptedChatModel):
        async def complete(self, messages, tools):
            raise ModelError("connection refused")

    toolbox = FakeToolbox({"daily_summary": SUMMARY})
    assistant = Assistant(Broken([]), toolbox, AssistantSettings())
    response = ask(assistant)
    assert response.status == "model_error" and "connection refused" in response.answer


def test_today_override_requires_evaluation_mode():
    assistant, _, _ = make([ModelTurn(text="x")])
    with pytest.raises(TodayOverrideNotAllowed):
        ask(assistant, today=date(2026, 9, 1))
    allowed, _, _ = make([ModelTurn(text="x")], allow_today_override=True)
    response = ask(allowed, today=date(2026, 9, 1))
    assert response.time_semantics.today == date(2026, 9, 1)


def test_time_semantics_definitions():
    semantics = resolve(NY, date(2026, 10, 2))  # a Friday
    assert semantics.this_week_start == date(2026, 9, 28)
    assert (semantics.last_week_start, semantics.last_week_end) == (
        date(2026, 9, 21),
        date(2026, 9, 27),
    )
    assert semantics.past_7_days_start == date(2026, 9, 26)
    assert "inclusive of today" in semantics.definitions["past_7_days"]


def test_invalid_timezone_is_rejected_before_any_model_call():
    assistant, model, _ = make([ModelTurn(text="x")])
    from healthops.tools.local_days import ToolInputError

    with pytest.raises(ToolInputError):
        ask(assistant, tz="Mars/Olympus")
    assert model.requests == []


def test_request_timezone_is_injected_and_overrides_the_model():
    assistant, model, toolbox = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="resting_heart_rate",
                        timezone="UTC",  # the model tries to change the day boundary
                        start_date="2026-09-26",
                        end_date="2026-10-02",
                    )
                ]
            ),
            ModelTurn(text="ok"),
        ]
    )
    ask(assistant)
    assert toolbox.calls[0][1]["timezone"] == NY
    for tool in model.requests[0][1]:
        assert "timezone" not in tool["function"]["parameters"]["properties"]
        assert "provider" not in tool["function"]["parameters"]["properties"]
        assert "timezone" not in tool["function"]["parameters"].get("required", [])


def test_deadline_covers_a_slow_model_and_reports_budget_exceeded():
    class Slow(ScriptedChatModel):
        async def complete(self, messages, tools):
            await asyncio.sleep(0.2)
            return ModelTurn(text="too late")

    assistant = Assistant(
        Slow([]), FakeToolbox({"daily_summary": SUMMARY}), AssistantSettings(deadline_seconds=0.02)
    )
    response = ask(assistant)
    assert response.status == "budget_exceeded" and "time budget" in response.answer


def test_deadline_includes_queueing_behind_the_concurrency_gate():
    class Slow(ScriptedChatModel):
        async def complete(self, messages, tools):
            await asyncio.sleep(0.15)
            return ModelTurn(text="done")

    assistant = Assistant(
        Slow([]),
        FakeToolbox({"daily_summary": SUMMARY}),
        AssistantSettings(deadline_seconds=0.1, max_concurrency=1),
    )

    async def two():
        request = AskRequest(question="q", timezone=NY)
        return await asyncio.gather(
            assistant.ask(request, USER, today=date(2026, 10, 2)),
            assistant.ask(request, USER, today=date(2026, 10, 2)),
        )

    first, second = run(two())
    assert {first.status, second.status} == {"budget_exceeded"}  # both exceed: 0.15 s > 0.1 s


@pytest.mark.parametrize(
    ("name", "result"),
    [
        ("availability", {"window": {}, "series": [], "notes": [], "warnings": []}),
        (
            "period_comparison",
            {
                "providers": [
                    {
                        "provider": "healthkit",
                        "baseline": {"value": None},
                        "current": {"value": None},
                        "difference": None,
                        "warnings": ["insufficient_data"],
                    }
                ],
                "warnings": ["insufficient_data"],
            },
        ),
        ("daily_summary", NO_DATA),
    ],
)
def test_insufficient_data_is_judged_per_tool_result(name, result):
    arguments = {
        "availability": dict(start_date="2026-09-26", end_date="2026-10-02"),
        "period_comparison": dict(
            metric="steps",
            baseline_start="2026-09-19",
            baseline_end="2026-09-25",
            current_start="2026-09-26",
            current_end="2026-10-02",
        ),
        "daily_summary": dict(metric="steps", start_date="2026-09-26", end_date="2026-10-02"),
    }[name]
    assistant, _, _ = make(
        [ModelTurn(tool_calls=[call(name, **arguments)]), ModelTurn(text="No data.")],
        results={name: result},
    )
    assert ask(assistant).status == "insufficient_data"


def test_mixed_results_with_some_data_are_answered():
    assistant, _, _ = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="steps",
                        start_date="2026-09-26",
                        end_date="2026-10-02",
                    )
                ]
            ),
            ModelTurn(text="60.5"),
        ],
        results={"daily_summary": SUMMARY},
    )
    assert ask(assistant).status == "answered"


def test_unsupported_relative_phrases_are_listed_as_unsupported_in_the_prompt():
    assistant, model, _ = make([ModelTurn(text="x")])
    ask(assistant)
    system = model.requests[0][0][0]["content"]
    assert "Week before last (two weeks ago): 2026-09-14 to 2026-09-20" in system
    assert "Three weeks ago: 2026-09-07 to 2026-09-13" in system
    assert "Previous 7 days (the 7 days before that): 2026-09-19 to 2026-09-25" in system
    assert "must be declined" in system


def test_last_night_is_defined_as_yesterday_for_sleep():
    assistant, model, _ = make([ModelTurn(text="x")])
    ask(assistant)
    system = model.requests[0][0][0]["content"]
    assert "Last night (sleep, assigned to its start day): 2026-10-01" in system
    assert "do not compute averages, totals or differences across days yourself" in system.lower()


def test_text_without_any_tool_call_is_not_reported_as_answered():
    assistant, _, _ = make([ModelTurn(text="I cannot help with that.")])
    response = ask(assistant)
    assert response.status == "no_tool_answer" and response.evidence == []
    assert response.answer == FALLBACK_EXPLANATION  # the prose is kept only as model_text
    assert response.model_text == "I cannot help with that."


def test_prose_dates_are_not_counted_as_numbers():
    text = "From September 21 to Sep 27, 2026 the mean was 56.83 bpm; on October 2nd it was 60."
    assert numeric.numbers_in_text(text) == ["56.83", "60"]


def test_provider_filter_from_the_model_is_dropped_and_recorded():
    assistant, model, toolbox = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="resting_heart_rate",
                        provider="healthkit",
                        start_date="2026-09-26",
                        end_date="2026-10-02",
                    )
                ]
            ),
            ModelTurn(text="ok"),
        ]
    )
    response = ask(assistant)
    assert "provider" not in toolbox.calls[0][1] or toolbox.calls[0][1]["provider"] is None
    assert "provider_filter_ignored" in response.tool_calls[0].warnings
    tool_message = model.requests[1][0][-1]
    assert "provider filter was ignored" in tool_message["content"]


def test_reversed_comparison_windows_are_reordered_deterministically():
    comparison = {
        "providers": [
            {
                "provider": "healthkit",
                "baseline": {"value": "1"},
                "current": {"value": "2"},
                "difference": "1",
                "warnings": [],
            }
        ],
        "warnings": [],
    }
    assistant, model, toolbox = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "period_comparison",
                        metric="steps",
                        baseline_start="2026-09-21",
                        baseline_end="2026-09-27",  # later window
                        current_start="2026-09-14",
                        current_end="2026-09-20",
                    )
                ]
            ),
            ModelTurn(text="ok"),
        ],
        results={"period_comparison": comparison},
    )
    response = ask(assistant)
    arguments = toolbox.calls[0][1]
    assert (arguments["baseline_start"], arguments["current_start"]) == ("2026-09-14", "2026-09-21")
    assert "windows_reordered_baseline_is_earlier" in response.tool_calls[0].warnings
    assert "reordered" in model.requests[1][0][-1]["content"]


def test_text_after_only_failed_tool_calls_is_not_an_answer():
    bad = call("daily_summary", metric="pulse", start_date="2026-09-26", end_date="2026-10-02")
    assistant, _, _ = make([ModelTurn(tool_calls=[bad]), ModelTurn(text="Your heart rate is 65.")])
    response = ask(assistant)
    assert response.status == "no_tool_answer"
    assert response.tool_calls[0].status == "invalid_arguments" and response.evidence == []


def test_model_arguments_are_kept_separately_from_executed_arguments():
    assistant, _, _ = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "period_comparison",
                        metric="steps",
                        provider="healthkit",
                        baseline_start="2026-09-21",
                        baseline_end="2026-09-27",
                        current_start="2026-09-14",
                        current_end="2026-09-20",
                    )
                ]
            ),
            ModelTurn(text="ok"),
        ],
        results={"period_comparison": {"providers": [], "warnings": []}},
    )
    record = ask(assistant).tool_calls[0]
    assert record.model_arguments["baseline_start"] == "2026-09-21"
    assert record.model_arguments["provider"] == "healthkit"
    assert record.arguments["baseline_start"] == "2026-09-14"
    assert record.arguments["provider"] is None


def findings(*claims):
    return call(
        "state_findings",
        findings=[
            " ".join(str(v) for v in (c["type"], c["evidence_id"], c.get("date")) if v)
            for c in claims
        ],
    )


def test_structured_findings_are_verified_and_rendered_by_templates():
    assistant, _, _ = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="resting_heart_rate",
                        start_date="2026-09-26",
                        end_date="2026-10-02",
                    )
                ]
            ),
            ModelTurn(
                tool_calls=[
                    findings(
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
    response = ask(assistant)
    assert response.status == "answered" and response.explanation_status == "verified"
    assert response.answer == (
        "On 2026-09-27, your resting_heart_rate (healthkit) was 62 bpm, mean of recorded samples "
        "(not an all-day average). No resting_heart_rate records on 2026-09-28 (healthkit)."
    )
    assert [c.valid for c in response.claims] == [True, True]
    assert response.numeric_consistency.status == "consistent"


def test_one_invalid_claim_gives_fallback_text_but_keeps_cards_and_data_status():
    assistant, _, _ = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="resting_heart_rate",
                        start_date="2026-09-26",
                        end_date="2026-10-02",
                    )
                ]
            ),
            ModelTurn(
                tool_calls=[
                    findings(
                        {
                            "type": "value_on_date",
                            "evidence_id": "e1.healthkit",
                            "date": "2026-09-28",
                        }
                    )
                ]
            ),
        ],
        results={"daily_summary": SUMMARY_WITH_GAP},
    )
    response = ask(assistant)
    assert response.status == "answered"  # the data query succeeded
    assert response.explanation_status == "unverified" and response.answer == FALLBACK_EXPLANATION
    assert response.claims[0].reason == "no recorded value on that date"
    assert response.cards[0].rows[2].display == "no data"


def test_channel_names_in_the_question_require_a_ui_selection():
    assistant, model, _ = make([ModelTurn(text="never")])
    response = ask(assistant, "Show my Fitbit steps for yesterday.")
    assert response.status == "needs_clarification" and model.requests == []
    assert "Choose the data source in the interface" in response.answer
    assert response.explanation_status == "not_applicable"


def test_ui_provider_selection_is_injected_into_tool_calls():
    assistant, _, toolbox = make(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "daily_summary",
                        metric="steps",
                        start_date="2026-10-01",
                        end_date="2026-10-01",
                    )
                ]
            ),
            ModelTurn(text="x"),
        ]
    )
    request = AskRequest(question="my steps yesterday", timezone=NY, provider="google_health")
    response = run(assistant.ask(request, USER, today=date(2026, 10, 2)))
    assert toolbox.calls[0][1]["provider"] == "google_health"
    assert "provider_from_request" in response.tool_calls[0].warnings


def test_debug_page_has_no_secrets_and_renders_api_text_as_text_nodes():
    from healthops.assistant.debug_page import DEBUG_PAGE_HTML

    assert "Bearer " + "x" not in DEBUG_PAGE_HTML
    assert "innerHTML" not in DEBUG_PAGE_HTML and "textContent" in DEBUG_PAGE_HTML
    assert "not the iOS app" in DEBUG_PAGE_HTML
