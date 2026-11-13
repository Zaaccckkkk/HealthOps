"""Reconciliation contract and safety tests. Fake quantiles are not an accuracy evaluation."""

import asyncio
import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_assistant import call

from healthops.assistant.cards import build_cards
from healthops.assistant.claims import Claim, FindingsParams, render_explanation, verify_claim
from healthops.assistant.guards import coverage, scope_error
from healthops.assistant.model import ModelTurn, ScriptedChatModel
from healthops.assistant.orchestrator import card_hint
from healthops.assistant.routing import RequestSpec, clarification_form, route
from healthops.assistant.time_semantics import resolve
from healthops.assistant.toolbox import Toolbox
from healthops.reconciliation.model import feature_rows
from healthops.tools.contracts import ReconcileParams
from healthops.tools.local_days import ToolInputError
from healthops.tools.reconcile import RED_FLAG, reconcile_from_summaries

DAY = date(2026, 8, 20)


class Predictor:
    window_days = 14
    version = "fixture-transformer-w14"
    data_version = "synthetic-fixture"
    artifact_sha256 = "a" * 64

    def __init__(self, quantiles=(6500, 7000, 7500)):
        self.quantiles = quantiles
        self.calls = []

    def predict(self, days, readings, first_day, metric):
        self.calls.append((days, readings, first_day, metric))
        return self.quantiles


def summaries(hk_days=range(14), gh_days=range(14), metric="steps", hk=6000, gh=10000):
    # Pure daily-summary contract fixtures; DB aggregation is tested separately.
    from healthops.schemas import Metric

    providers = []
    for provider, indices, value in (("healthkit", hk_days, hk), ("google_health", gh_days, gh)):
        providers.append(
            SimpleNamespace(
                provider=provider,
                days=[
                    SimpleNamespace(
                        date=DAY - timedelta(days=13 - i),
                        warnings=[],
                        sample_mean=Decimal(value) if metric == "resting_heart_rate" else None,
                        daily_summary=None,
                        daily_total=Decimal(value) if metric != "resting_heart_rate" else None,
                        interval_total_sum=None,
                    )
                    for i in indices
                ],
            )
        )
    return [SimpleNamespace(metric=Metric(metric), providers=providers, warnings=[])]


def result(
    hk_days=range(14),
    gh_days=range(14),
    first_day=DAY - timedelta(days=13),
    metric="steps",
    predictor=None,
):
    return reconcile_from_summaries(
        ReconcileParams(metric=metric, timezone="America/New_York", date=DAY),
        predictor or Predictor(),
        summaries(hk_days, gh_days, metric),
        first_day,
    )


def card(r):
    return build_cards([{"tool": "reconcile_devices", "result": r.model_dump(mode="json")}])[0]


def test_complete_window_readings_deviations_attribution_and_provenance():
    r = result()
    assert (r.q05, r.q50, r.q95) == (6500, 7000, 7500)
    assert r.attribution == "google_health"
    assert [p.deviation for p in r.providers] == [-1000, 3000]
    assert r.window_days_with_any == r.window_days_with_both == r.history_days == 14
    assert r.window.start_date == DAY - timedelta(days=13)
    assert r.window.start_utc.hour == 4
    assert r.model_version == Predictor.version and r.artifact_sha256 == "a" * 64
    assert r.warnings == sorted(set(r.warnings))
    assert all(f"warning:{w}" in r.definitions for w in r.warnings)


@pytest.mark.parametrize("count", [0, 1, 3, 6, 7, 13, 14])
def test_short_history_and_sparse_thresholds(count):
    r = result(range(14 - count, 14), [], DAY - timedelta(days=count - 1) if count else None)
    assert ("history_short" in r.warnings) == (count < 14)
    assert ("sparse_window" in r.warnings) == (count < 7)
    assert r.window_days_with_any == count
    assert r.window_days_with_both == 0
    if count:
        answer, _, valid = render_explanation(
            FindingsParams(findings=[f"value_on_date e1 {DAY}"]), [card(r)]
        )
        assert valid and RED_FLAG in answer  # single_device also requires the red flag


def test_no_query_day_reading_never_calls_predictor_even_with_history():
    predictor = Predictor()
    r = result(range(13), range(13), predictor=predictor)
    assert (r.q05, r.q50, r.q95, r.attribution) == (None, None, None, None)
    assert not predictor.calls
    c = card(r)
    assert verify_claim(Claim(type="missing_data", evidence_id="e1", date=str(DAY)), [c]).valid
    assert not verify_claim(Claim(type="value_on_date", evidence_id="e1", date=str(DAY)), [c]).valid
    text, _, valid = render_explanation(FindingsParams(findings=["missing_data e1"]), [c])
    assert valid and "7,000" not in text and "prediction interval" not in text
    assert "no_records_in_window" in r.warnings


def test_single_device_cannot_claim_attribution_or_invent_other_channel():
    r = result(gh_days=[])
    c = card(r)
    assert r.q50 is not None and r.attribution is None
    assert r.providers[1].value is None and r.providers[1].deviation is None
    assert "device_attribution" not in card_hint(c)["claim_types"]
    claim = verify_claim(Claim(type="device_attribution", evidence_id="e1"), [c])
    assert not claim.valid
    text, _, valid = render_explanation(FindingsParams(findings=[f"value_on_date e1 {DAY}"]), [c])
    assert valid and "Google Health: no usable reading" in text
    assert text.startswith("A device comparison is not possible")
    assert "no device attribution" in text


def test_tie_has_no_attribution_and_zero_is_a_measurement():
    r = result(predictor=Predictor((6000, 8000, 10000)))
    assert r.attribution is None
    zero = reconcile_from_summaries(
        ReconcileParams(metric="steps", timezone="UTC", date=DAY),
        Predictor((0, 0, 1)),
        summaries(hk=0, gh=0),
        DAY - timedelta(days=13),
    )
    assert zero.q50 == 0 and zero.window_days_with_any == 14


@pytest.mark.parametrize(
    "metric,width", [("steps", 5000), ("sleep_duration", 5400), ("resting_heart_rate", 8)]
)
def test_wide_interval_threshold_and_sleep_units(metric, width):
    r = result(metric=metric, predictor=Predictor((0, 1, width)))
    assert "wide_interval" not in r.warnings
    r = result(metric=metric, predictor=Predictor((0, 1, width + 1)))
    assert "wide_interval" in r.warnings
    if metric == "sleep_duration":
        assert r.unit == "s" and "sleep_alignment_differs" in r.warnings


@pytest.mark.parametrize("q", [(9, 5, 10), (-1, 5, 10), (1, float("nan"), 10)])
def test_invalid_predictions_are_not_evidence(q):
    with pytest.raises(ToolInputError):
        result(predictor=Predictor(q))


def test_claim_always_renders_interval_warnings_and_exact_card_attribution():
    c = card(result(first_day=DAY - timedelta(days=2)))
    for finding in (f"value_on_date e1 {DAY}", "device_attribution e1"):
        text, _, valid = render_explanation(FindingsParams(findings=[finding]), [c])
        assert valid
        assert "nominal 90% prediction interval 6,500 count to 7,500 count" in text
        assert RED_FLAG in text and "Google Health is farther" in text
    assert not verify_claim(
        Claim(type="value_on_date", evidence_id="e1", date="2026-08-19"), [c]
    ).valid


def test_window_from_metadata_and_model_feature_order_padding_masks():
    predictor = Predictor()
    predictor.window_days = 30
    r = result(predictor=predictor)
    assert r.window_days == 30 and "history_short" in r.warnings
    stats = {k: {"mean": 0, "sd": 1} for k in ("sleep", "steps", "rhr")}
    days = [DAY - timedelta(days=1), DAY]
    values = {
        (DAY, "sleep_duration", "healthkit"): 3600,
        (DAY, "steps", "google_health"): 0,
        (DAY, "resting_heart_rate", "healthkit"): 60,
    }
    x = feature_rows(days, values, stats, DAY)
    assert x[0] == [0] * 30
    assert x[1][:6] == [60, 0, 0, 0, 60, 0]
    assert x[1][6:12] == [1, 0, 0, 1, 1, 0]
    assert x[1][12:22] == [1, 0, 0, 1, 0, 0, 0, 0, 0, 0.3]
    assert x[1][-1] == 1 and x[1][22 + DAY.weekday()] == 1


def test_schema_is_flat_and_unknown_fields_or_nested_metric_rejected():
    tools = Toolbox(None)
    schema = tools.registry["reconcile_devices"].schema()["function"]["parameters"]
    assert set(schema["properties"]) == {"metric", "date"}
    for args in (
        {"metric": {"type": "steps"}, "date": str(DAY)},
        {"metric": "hrv_sdnn", "date": str(DAY)},
        {"metric": "steps", "date": str(DAY), "user_id": str(uuid4())},
    ):
        outcome = asyncio.run(tools.execute("reconcile_devices", json.dumps(args), uuid4(), "UTC"))
        assert outcome.record.status == "invalid_arguments"


def spec():
    return RequestSpec(
        intent="device_reconciliation",
        metrics=["steps"],
        window={"start_date": DAY, "end_date": DAY},
        baseline=None,
        language="en",
        unresolved_fields=[],
    )


def test_scope_blocks_wrong_tool_date_metric_and_selected_channel():
    s = spec()
    args = {"metric": "steps", "date": str(DAY)}
    assert scope_error("reconcile_devices", json.dumps(args), s, "UTC", None) is None
    for name, data, provider in (
        ("daily_summary", args, None),
        ("reconcile_devices", args | {"date": "2026-08-19"}, None),
        ("reconcile_devices", args | {"metric": "sleep_duration"}, None),
        ("reconcile_devices", args, "healthkit"),
    ):
        assert scope_error(name, json.dumps(data), s, "UTC", provider)


@pytest.mark.parametrize(
    "metric,phrase,question,expected",
    [
        (["steps"], "yesterday", "Reconcile my steps yesterday", "ready"),
        ([], "", "Which device is closer?", "needs_clarification"),
        (["steps"], "past 30 days", "Reconcile steps past 30 days", "unsupported"),
        (["hrv_sdnn"], "yesterday", "Reconcile HRV SDNN yesterday", "unsupported"),
        (["sleep_duration"], "last night", "Reconcile sleep duration last night", "ready"),
        (
            ["sleep_duration"],
            "\u6628\u665a",
            "\u6628\u665a\u7761\u7720\u54ea\u53f0\u8bbe\u5907\u66f4\u51c6",
            "ready",
        ),
    ],
)
def test_router_single_day_scope_and_unsupported_metrics(metric, phrase, question, expected):
    model = ScriptedChatModel(
        [
            ModelTurn(
                tool_calls=[
                    call(
                        "route_request",
                        decision="ready",
                        task="device_reconciliation",
                        metrics=metric,
                        time_phrase=phrase,
                        baseline_phrase="",
                        language="en",
                        reason="none",
                    )
                ]
            )
        ]
    )
    decision, s, _ = asyncio.run(
        route(model, [{"role": "user", "content": question}], resolve("UTC", DAY))
    )
    assert decision == expected
    if expected == "ready" and phrase in {"last night", "\u6628\u665a"}:
        assert s.window.start_date == s.window.end_date == DAY - timedelta(days=1)
    if expected == "needs_clarification":
        form = clarification_form(s)
        time = next(f for f in form.fields if f.id == "time_range")
        assert [o.id for o in time.options] == ["today", "yesterday"]
        assert next(f for f in form.fields if f.id == "metrics").selection == "single"


def test_bare_sleep_is_grounded_only_for_explicit_device_comparison():
    from healthops.assistant.routing import grounded_reconciliation_sleep

    assert grounded_reconciliation_sleep(["Which device is closer on sleep yesterday?"])
    assert not grounded_reconciliation_sleep(["How did I sleep last night?"])
    assert not grounded_reconciliation_sleep(["My watch recorded sleep last night"])


def test_failed_claim_repair_still_preserves_warning_without_marking_answer_verified():
    from test_assistant import USER, FakeToolbox

    from healthops.assistant.contracts import AskRequest
    from healthops.assistant.orchestrator import Assistant, AssistantSettings

    model = ScriptedChatModel(
        [
            ModelTurn(tool_calls=[call("reconcile_devices", metric="steps", date=str(DAY))]),
            ModelTurn(tool_calls=[call("state_findings", findings=["reference e1"])]),
            ModelTurn(tool_calls=[call("state_findings", findings=["reference e1"])]),
        ]
    )
    assistant = Assistant(
        model,
        FakeToolbox({"reconcile_devices": result(first_day=DAY).model_dump(mode="json")}),
        AssistantSettings(),
    )
    response = asyncio.run(
        assistant.ask(
            AskRequest(question=f"Reconcile steps on {DAY}", timezone="UTC"),
            USER,
            today=DAY,
            request_spec=spec(),
        )
    )
    assert response.explanation_status == "unverified"
    assert response.completion_status == "incomplete"
    assert len(response.verification_attempts) == 2
    assert RED_FLAG in response.answer
    assert "7,000" not in response.answer  # no failed-model estimate appears in the fallback
    assert response.cards[0].reconciliation["q50"] == "7000.000000"


def test_coverage_requires_reconciliation_evidence_and_verified_claim():
    from healthops.assistant.contracts import ToolCallRecord

    c = card(result())
    record = ToolCallRecord(name="reconcile_devices", arguments={"metric": "steps"}, status="ok")
    assert not coverage(spec(), [c], [record], [])[0].reported
    claim = verify_claim(Claim(type="value_on_date", evidence_id="e1", date=str(DAY)), [c])
    report = coverage(spec(), [c], [record], [claim])[0]
    assert report.reported and report.query_status == "available"
