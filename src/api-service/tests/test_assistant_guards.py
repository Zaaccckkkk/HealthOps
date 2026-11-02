"""Confirmed-scope and bounded repair tests; all data and model turns are synthetic."""

import asyncio
import copy
import json

import pytest
from test_assistant import NY, SUMMARY, USER, FakeToolbox, call

from healthops.assistant.claims import FALLBACK_EXPLANATION
from healthops.assistant.contracts import AskRequest, ToolCallRecord
from healthops.assistant.guards import scope_error
from healthops.assistant.model import ModelTurn, ScriptedChatModel
from healthops.assistant.orchestrator import Assistant, AssistantSettings
from healthops.assistant.routing import RequestSpec
from healthops.assistant.toolbox import ToolOutcome

START, END = "2026-09-26", "2026-09-27"


def spec(metrics=("resting_heart_rate",), intent="summary"):
    return RequestSpec(
        intent=intent,
        metrics=list(metrics),
        window={"start_date": START, "end_date": END},
        baseline=None,
        language="en",
        unresolved_fields=[],
    )


def query(metric="resting_heart_rate", **override):
    return call(
        "daily_summary", **({"metric": metric, "start_date": START, "end_date": END} | override)
    )


def findings(*items):
    return ModelTurn(tool_calls=[call("state_findings", findings=list(items))])


def data(metric="resting_heart_rate", empty=False, paired=False):
    result = copy.deepcopy(SUMMARY)
    result["metric"] = metric
    result["window"] = {"timezone": NY, "start_date": START, "end_date": END}
    result["unit"] = "s" if metric == "sleep_duration" else "bpm"
    if empty:
        result.update(providers=[], record_count=0, warnings=["no_records_in_window"])
    if paired:
        other = copy.deepcopy(result["providers"][0])
        other["provider"] = "google_health"
        result["providers"].append(other)
    return result


class MetricToolbox(FakeToolbox):
    def __init__(self, results, fail=False):
        super().__init__({})
        self.by_metric = results
        self.fail = fail

    async def execute(self, name, arguments_json, user_id, timezone=None, provider=None):
        if name == "daily_summary":
            raw = json.loads(arguments_json)
            if self.fail:
                return ToolOutcome(
                    ToolCallRecord(
                        name=name, arguments=raw, status="error", error="synthetic database failure"
                    ),
                    None,
                    None,
                )
            self.results[name] = self.by_metric[raw["metric"]]
        return await super().execute(name, arguments_json, user_id, timezone, provider)


def run(turns, request_spec=None, results=None, fail=False, **settings):
    model = ScriptedChatModel(turns)
    toolbox = MetricToolbox(results or {"resting_heart_rate": data()}, fail)
    assistant = Assistant(model, toolbox, AssistantSettings(**settings))
    response = asyncio.run(
        assistant.ask(
            AskRequest(question="Synthetic confirmed question", timezone=NY),
            USER,
            request_spec=request_spec or spec(),
        )
    )
    return response, model, toolbox


@pytest.mark.parametrize(
    "override",
    [
        {"metric": "steps"},
        {"start_date": "2026-09-01"},
        {"end_date": START},
        {"timezone": "UTC"},
        {"provider": "google_health"},
    ],
)
def test_scope_rejections_never_reach_database(override):
    result, _, tools = run(
        [
            ModelTurn(tool_calls=[query(**override)]),
            ModelTurn(tool_calls=[query()]),
            findings("window_summary e1.healthkit"),
        ]
    )
    assert result.tool_calls[0].warnings == ["scope_guard_rejected"]
    assert result.tool_calls[0].arguments == {}
    assert len([c for c in tools.calls if c[0] == "daily_summary"]) == 1
    assert result.explanation_status == "verified"
    assert result.completion_status == "complete"


def test_reference_cannot_read_personal_data_and_comparison_cannot_swap_dates():
    assert scope_error("daily_summary", query().arguments_json, spec(intent="reference"), NY, None)
    comparison = spec(intent="comparison").model_copy(
        update={
            "baseline": spec().window.model_copy(
                update={
                    "start_date": spec().window.start_date.replace(day=20),
                    "end_date": spec().window.end_date.replace(day=21),
                }
            )
        }
    )
    args = {
        "metric": "resting_heart_rate",
        "baseline_start": START,
        "baseline_end": END,
        "current_start": "2026-09-20",
        "current_end": "2026-09-21",
    }
    assert scope_error("period_comparison", json.dumps(args), comparison, NY, None)


def test_missing_metric_must_be_reported_even_when_no_records():
    result, model, _ = run(
        [
            ModelTurn(tool_calls=[query("sleep_duration"), query()]),
            findings("window_summary e1.healthkit"),
            findings("window_summary e1.healthkit", "missing_data e2"),
        ],
        spec(("sleep_duration", "resting_heart_rate")),
        {"sleep_duration": data("sleep_duration"), "resting_heart_rate": data(empty=True)},
    )
    assert result.verification_attempts[0].missing_targets == ["resting_heart_rate"]
    assert len(result.verification_attempts) == 2
    assert result.completion_status == "complete"
    assert result.coverage[1].query_status == "no_records"
    assert "No resting_heart_rate records" in result.answer
    feedback = json.loads(model.requests[-1][0][-1]["content"])
    assert feedback["verification_failed"]
    assert [s["function"]["name"] for s in model.requests[-1][1]] == ["state_findings"]


def test_premature_finalization_can_query_unqueried_target_within_same_budget():
    result, _, tools = run(
        [
            ModelTurn(tool_calls=[query("sleep_duration")]),
            findings("window_summary e1.healthkit"),
            ModelTurn(tool_calls=[query()]),
            findings("window_summary e1.healthkit", "missing_data e2"),
        ],
        spec(("sleep_duration", "resting_heart_rate")),
        {"sleep_duration": data("sleep_duration"), "resting_heart_rate": data(empty=True)},
    )
    assert result.completion_status == "complete"
    assert len(tools.calls) == 4


def test_bad_reference_gets_one_repair_and_no_second_retry():
    result, model, _ = run(
        [
            ModelTurn(tool_calls=[query()]),
            findings("window_summary invented"),
            findings("window_summary invented_again"),
        ]
    )
    assert result.explanation_status == "unverified"
    assert result.answer == FALLBACK_EXPLANATION
    assert result.completion_status == "incomplete"
    assert len(result.verification_attempts) == 2 and len(model.requests) == 3
    assert result.verification_attempts[0].invalid_reasons == ["unknown evidence id"]


def test_corrected_reference_passes_without_requerying():
    result, _, tools = run(
        [
            ModelTurn(tool_calls=[query()]),
            findings("window_summary invented"),
            findings("window_summary e1.healthkit"),
        ]
    )
    assert result.completion_status == "complete"
    assert len([c for c in tools.calls if c[0] == "daily_summary"]) == 1


def test_one_day_does_not_cover_a_two_day_window():
    result, _, _ = run(
        [
            ModelTurn(tool_calls=[query()]),
            findings(f"value_on_date e1.healthkit {START}"),
            findings("window_summary e1.healthkit"),
        ]
    )
    assert result.verification_attempts[0].valid_claims == 1
    assert result.verification_attempts[0].missing_targets == ["resting_heart_rate"]
    assert result.completion_status == "complete"


def test_all_returned_sources_must_be_reported():
    result, _, _ = run(
        [
            ModelTurn(tool_calls=[query()]),
            findings("window_summary e1.healthkit"),
            findings("window_summary e1.healthkit", "window_summary e1.google_health"),
        ],
        results={"resting_heart_rate": data(paired=True)},
    )
    assert result.verification_attempts[0].missing_targets
    assert result.completion_status == "complete"
    assert len(result.coverage[0].evidence_ids) == 2


def test_query_failure_is_not_missing_data():
    result, _, _ = run([ModelTurn(tool_calls=[query()])], fail=True)
    assert result.status == "tool_error"
    assert result.coverage[0].query_status == "query_failed"
    assert result.completion_status == "incomplete"
    assert "No resting_heart_rate records" not in result.answer


def test_repair_cannot_requery_already_fetched_evidence():
    result, _, tools = run(
        [
            ModelTurn(tool_calls=[query()]),
            findings("window_summary invented"),
            ModelTurn(tool_calls=[query()]),
            findings("window_summary e1.healthkit"),
        ]
    )
    assert result.tool_calls[2].status == "rejected"
    assert len([c for c in tools.calls if c[0] == "daily_summary"]) == 1
    assert result.completion_status == "complete"


def test_repair_does_not_reset_call_budget():
    result, _, _ = run(
        [
            ModelTurn(tool_calls=[query()]),
            findings("window_summary invented"),
            findings("window_summary e1.healthkit"),
        ],
        max_tool_calls=2,
    )
    assert result.status == "budget_exceeded"
    assert result.completion_status == "incomplete"


def test_finalization_in_same_batch_as_query_is_rejected_before_execution():
    result, _, tools = run(
        [
            ModelTurn(
                tool_calls=[
                    query(),
                    call("state_findings", findings=["window_summary e1.healthkit"]),
                ]
            ),
            ModelTurn(tool_calls=[query()]),
            findings("window_summary e1.healthkit"),
        ]
    )
    assert all(r.status == "rejected" for r in result.tool_calls[:2])
    assert result.completion_status == "complete"
    assert len([c for c in tools.calls if c[0] == "daily_summary"]) == 1


def test_no_data_window_requires_whole_window_statement():
    result, _, _ = run(
        [
            ModelTurn(tool_calls=[query()]),
            findings(f"missing_data e1 {START}"),
            findings("missing_data e1"),
        ],
        results={"resting_heart_rate": data(empty=True)},
    )
    assert result.verification_attempts[0].valid_claims == 1
    assert result.verification_attempts[0].missing_targets == ["resting_heart_rate"]
    assert result.completion_status == "complete"


def test_availability_and_reference_have_distinct_coverage():
    from healthops.assistant.cards import Card
    from healthops.assistant.claims import Claim, verify_claim
    from healthops.assistant.guards import coverage

    card = Card(
        evidence_id="e1",
        kind="availability",
        tool="availability",
        title="Inventory",
        timezone=NY,
        start_date=START,
        end_date=END,
        provider=None,
        metric=None,
        unit=None,
        statistic=None,
        series=[{"metric": "steps", "provider": "healthkit", "aggregation": "daily_total"}],
    )
    claim = verify_claim(Claim(type="available_series", evidence_id="e1"), [card])
    records = [ToolCallRecord(name="availability", arguments={}, status="ok")]
    assert claim.valid and "steps" in claim.sentence
    assert coverage(spec(intent="availability"), [card], records, [claim])[0].reported
    assert not coverage(spec(intent="reference"), [card], records, [claim])[0].reported
    assert not verify_claim(Claim(type="window_summary", evidence_id="e1"), [card]).valid


def test_unusable_records_are_not_no_records_or_zero():
    from test_assistant import SUMMARY_WITH_GAP

    result_data = copy.deepcopy(SUMMARY_WITH_GAP)
    result_data["window"] = {"timezone": NY, "start_date": START, "end_date": END}
    result_data["providers"][0]["days"] = [
        {
            "date": START,
            "sample_count": 0,
            "sample_mean": None,
            "daily_summary": None,
            "daily_total": None,
            "interval_total_sum": None,
            "unsupported_count": 1,
            "warnings": ["unsupported_aggregation"],
        }
    ]
    result, _, _ = run(
        [ModelTurn(tool_calls=[query()]), findings("window_summary e1.healthkit")],
        results={"resting_heart_rate": result_data},
    )
    assert result.completion_status == "complete"
    assert result.coverage[0].query_status == "unusable"
    assert "records but no reliable value" in result.answer
    assert "No resting_heart_rate records" not in result.answer


def test_reference_requires_a_valid_citation_not_every_retrieval_candidate():
    from healthops.assistant.cards import Card
    from healthops.assistant.claims import Claim, verify_claim
    from healthops.assistant.guards import coverage

    card = Card(
        evidence_id="e1.r1",
        kind="reference",
        tool="lookup_reference",
        title="Synthetic note",
        timezone=NY,
        start_date=None,
        end_date=None,
        provider=None,
        metric=None,
        unit=None,
        statistic=None,
        text="A synthetic definition.",
        publisher="Test",
        source_url="https://example.org/synthetic",
    )
    other = card.model_copy(update={"evidence_id": "e1.r2"})
    verified = verify_claim(Claim(type="reference", evidence_id="e1.r1"), [card, other])
    report = coverage(
        spec(intent="reference"),
        [card, other],
        [ToolCallRecord(name="lookup_reference", arguments={}, status="ok")],
        [verified],
    )
    assert report[0].reported and not report[0].unreported_evidence_ids


@pytest.mark.parametrize("metric", [{"type": "resting_heart_rate"}, ["resting_heart_rate"], None])
def test_malformed_metric_feedback_allows_correction_without_executing_bad_scope(metric):
    result, _, tools = run(
        [
            ModelTurn(tool_calls=[query(metric=metric)]),
            ModelTurn(tool_calls=[query()]),
            findings("window_summary e1.healthkit"),
        ]
    )
    rejected = result.tool_calls[0]
    assert rejected.status == "rejected"
    assert "plain enum string" in rejected.error
    assert len([c for c in tools.calls if c[0] == "daily_summary"]) == 1
    assert result.explanation_status == "verified"
    assert result.completion_status == "complete"
