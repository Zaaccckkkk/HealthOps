"""Scripted writer tests: guarantees are protocol checks, not semantic correctness."""

import asyncio
import json

import pytest
from test_assistant import NY, USER, FakeToolbox, call
from test_assistant_cards import daily, day
from test_assistant_guards import data, findings, query, spec
from test_reconcile import card as reconcile_card
from test_reconcile import result as reconcile_result

from healthops.assistant import narrative
from healthops.assistant.cards import build_cards
from healthops.assistant.contracts import AskRequest
from healthops.assistant.model import ModelTurn, ScriptedChatModel
from healthops.assistant.orchestrator import Assistant, AssistantSettings


def facts():
    return narrative.packet(
        build_cards(
            [
                daily(
                    "steps",
                    "count",
                    "cumulative",
                    {
                        "healthkit": [
                            day("2026-10-01", daily_total="1000"),
                            day("2026-10-02", daily_total="2000"),
                        ]
                    },
                )
            ]
        ),
        "2026-10-03",
    )


def draft(answer="Recorded steps increased between the two available dates.", ids=None):
    return ModelTurn(
        text=json.dumps(
            {"answer": answer, "evidence_ids": ids if ids is not None else ["e1.healthkit"]}
        )
    )


def test_descriptive_facts_are_computed_without_filling_missing_days():
    summary = facts()["sources"][0]["recorded_values_summary"]
    assert summary["mean_of_available_daily_values"] == "1500.00"
    assert summary["first_to_last_change"] == "1000"
    assert summary["first_date"] == "2026-10-01"


@pytest.mark.parametrize(
    "turn,reason",
    [
        (draft("You recorded 999999 steps."), "unsupported_number"),
        (draft("On 2030-01-01 the count was 1000."), "unsupported_date"),
        (draft(ids=["invented"]), "evidence_coverage_mismatch"),
        (draft(ids=[]), "invalid_response_shape"),
        (ModelTurn(text="unstructured text"), "invalid_response_shape"),
        (ModelTurn(tool_calls=[call("daily_summary", metric="steps")]), "unexpected_tool_call"),
        (draft("<script>run()</script>"), "unexpected_markup"),
    ],
)
def test_writer_basic_checks_reject_invalid_outputs(turn, reason):
    answer, actual = narrative.validate(turn, facts())
    assert answer is None and actual == reason


def test_valid_numeric_prose_passes_basic_checks_but_not_semantic_certification():
    answer, error = narrative.validate(draft("The recorded daily mean was 1500 steps."), facts())
    assert answer and error is None
    # Deliberately document the limit: membership cannot prove meaning or attribution.
    swapped = draft("The first date had 2000 steps and the second had 1000 steps.")
    assert narrative.validate(swapped, facts())[1] is None


def test_single_channel_estimate_is_not_given_to_the_writer():
    packet = narrative.packet([reconcile_card(reconcile_result(gh_days=[]))], "2026-08-20")
    item = packet["sources"][0]
    assert not item["comparison_possible"]
    assert "model_estimate" not in item
    assert "deviation" not in json.dumps(item)


def run_writer(turn, editor=None, **settings):
    model = ScriptedChatModel(
        [ModelTurn(tool_calls=[query()]), findings("window_summary e1.healthkit"), turn]
        + ([editor] if editor is not None else [])
    )
    assistant = Assistant(
        model,
        FakeToolbox({"daily_summary": data()}),
        AssistantSettings(narrative_enabled=True, **settings),
    )
    response = asyncio.run(
        assistant.ask(
            AskRequest(question="Analyze my recorded resting heart rate.", timezone=NY),
            USER,
            request_spec=spec(),
        )
    )
    return response, model


def test_verified_tools_feed_one_tool_free_writer_and_new_status():
    response, model = run_writer(draft("The available readings differ; gaps limit the comparison."))
    assert response.explanation_status == "model_generated"
    assert response.narrative_status == "generated"
    assert response.completion_status == "complete"
    assert response.claims and all(c.valid for c in response.claims)
    assert len(model.requests) == 3 and model.requests[-1][1] == []
    assert "Analyze my recorded" in model.requests[-1][0][1]["content"]


def test_malformed_editor_keeps_verified_template_and_explains_fallback():
    response, _ = run_writer(draft("Your heart rate was 999999 bpm."), ModelTurn(text="bad JSON"))
    assert response.narrative_status == "fallback"
    assert response.narrative_reason == "invalid_response_shape"
    assert response.narrative_check_reason == "unsupported_number"
    assert response.explanation_status == "verified"
    assert "999999" not in response.answer


def test_writer_cannot_exceed_total_model_turn_budget():
    response, model = run_writer(draft(), max_model_turns=2)
    assert response.narrative_reason == "model_turn_budget"
    assert response.explanation_status == "verified"
    assert len(model.requests) == 2


def test_writer_timeout_keeps_existing_verified_answer():
    class SlowWriter(ScriptedChatModel):
        async def complete(self, messages, tools):
            if not tools:
                await asyncio.sleep(1)
            return await super().complete(messages, tools)

    model = SlowWriter([ModelTurn(tool_calls=[query()]), findings("window_summary e1.healthkit")])
    assistant = Assistant(
        model,
        FakeToolbox({"daily_summary": data()}),
        AssistantSettings(
            narrative_enabled=True,
            narrative_timeout_seconds=0.01,
        ),
    )
    response = asyncio.run(
        assistant.ask(AskRequest(question="q", timezone=NY), USER, request_spec=spec())
    )
    assert response.narrative_status == "fallback"
    assert response.narrative_reason == "model_unavailable_or_timeout"
    assert response.explanation_status == "verified"


def test_failed_claims_never_reach_writer():
    from test_assistant_guards import run

    response, model, _ = run(
        [
            ModelTurn(tool_calls=[query()]),
            findings("window_summary invalid"),
            findings("window_summary still_invalid"),
        ],
        narrative_enabled=True,
    )
    assert response.explanation_status == "unverified"
    assert response.narrative_status != "generated"
    assert len(model.requests) == 3


def test_committed_quality_set_is_reproducible_and_explicitly_synthetic():
    from pathlib import Path

    from eval.build_answer_quality_set import cases

    path = Path(__file__).parents[1] / "eval/answer_quality.synthetic.jsonl"
    committed = [json.loads(line) for line in path.read_text().splitlines()]
    assert committed == cases()
    assert len({c["id"] for c in committed}) == 10
    assert all(c["data_kind"] == "synthetic" and c["manual_rubric"] for c in committed)


@pytest.mark.parametrize(
    "apple,google,expected,tie",
    [
        ("4100", "5900", ["google_health", "healthkit"], True),
        ("4700", "6400", ["healthkit"], False),
        ("3600", "5100", ["google_health"], False),
    ],
)
def test_comparison_distance_is_computed_not_inferred_from_attribution(
    apple, google, expected, tie
):
    from eval.build_answer_quality_additional import comparison

    card = comparison(apple, google, "5000")
    card.reconciliation["attribution"] = "misleading_field_is_not_forwarded"
    item = narrative.packet([card], "2026-08-10")["sources"][0]
    assert "attribution" not in item["model_estimate"]
    assert item["comparison_to_estimate"]["closest_channels"] == expected
    assert item["comparison_to_estimate"]["tie"] == tie


def test_unsigned_decrease_is_supported_without_allowing_arbitrary_sign_changes():
    from eval.build_answer_quality_set import daily

    card = daily("healthkit", [4300, 2700, 1900])
    evidence = narrative.packet([card], "2026-08-10")
    summary = evidence["sources"][0]["recorded_values_summary"]
    assert summary["first_to_last_change"] == "-2400"
    assert summary["absolute_change"] == "2400"
    assert summary["change_direction"] == "decrease"
    assert narrative.validate(draft("Recorded steps decreased by 2400."), evidence)[1] is None
    assert narrative.validate(draft("The reading was -4300."), evidence)[1] == "unsupported_number"


@pytest.mark.parametrize(
    "text,accepted",
    [
        ("\u572810\u67081\u65e5\u8bb0\u5f55\u4e861000\u6b65\u3002", True),
        ("2026\u5e7410\u67082\u65e5\u8bb0\u5f55\u4e862000\u6b65\u3002", True),
        ("October 1, 2026 had 1000 steps.", True),
        ("2 October 2026 had 2000 steps.", True),
        ("\u572810\u67089\u65e5\u8bb0\u5f55\u4e861000\u6b65\u3002", False),
        ("2025\u5e7410\u67081\u65e5\u8bb0\u5f55\u4e861000\u6b65\u3002", False),
        ("October 9, 2026 had 1000 steps.", False),
        ("2026-02-30 had 1000 steps.", False),
    ],
)
def test_calendar_text_is_validated_before_removing_it_from_numeric_check(text, accepted):
    from healthops.assistant.narrative_checks import numeric_diagnostic

    _, error = narrative.validate(draft(text), facts())
    assert error == (None if accepted else "unsupported_date")
    if accepted:
        assert not numeric_diagnostic(text, facts()).unmatched


def test_chinese_adjacent_invented_measurement_is_not_hidden_by_word_boundary():
    assert (
        narrative.validate(draft("\u8bb0\u5f55\u4e86999999\u6b65\u3002"), facts())[1]
        == "unsupported_number"
    )


def test_yearless_date_with_two_possible_years_is_rejected():
    evidence = facts()
    evidence["other_date"] = "2025-10-01"
    assert narrative.validate(draft("October 1 had 1000 steps."), evidence)[1] == "unsupported_date"


def test_current_day_qualification_is_server_owned_and_historical_window_is_not_today():
    from eval.build_answer_quality_set import daily

    cards = [daily("healthkit", [2000, 3000, 100])]
    assert any("incomplete" in s for s in narrative.qualifications(cards, "en", "2026-08-03"))
    assert narrative.qualifications(cards, "en", "2026-08-04") == []
    assert any(
        "\u5c1a\u672a\u5b8c\u6574" in s for s in narrative.qualifications(cards, "zh", "2026-08-03")
    )


def test_graph_adds_today_qualification_even_when_writer_omits_it():
    model = ScriptedChatModel(
        [
            ModelTurn(tool_calls=[query()]),
            findings("window_summary e1.healthkit"),
            draft("The available readings vary."),
        ]
    )
    assistant = Assistant(
        model,
        FakeToolbox({"daily_summary": data()}),
        AssistantSettings(narrative_enabled=True, allow_today_override=True),
    )
    response = asyncio.run(
        assistant.ask(
            AskRequest(question="Summarize these readings.", timezone=NY, today="2026-09-27"),
            USER,
            request_spec=spec(),
        )
    )
    assert response.explanation_status == "model_generated"
    assert "Today's recording may still be incomplete" in response.answer
    assert response.narrative_prompt_version == narrative.PROMPT_VERSION


def test_additional_quality_set_is_reproducible_and_disjoint():
    from pathlib import Path

    from eval.build_answer_quality_additional import cases as additional
    from eval.build_answer_quality_set import cases as original

    path = Path(__file__).parents[1] / "eval/answer_quality.additional.synthetic.jsonl"
    committed = [json.loads(line) for line in path.read_text().splitlines()]
    assert committed == additional()
    assert len({c["id"] for c in committed}) == 12
    assert not {c["id"] for c in committed} & {c["id"] for c in original()}
    assert all(c["data_kind"] == "synthetic" and c["manual_rubric"] for c in committed)


def paired_writer(turns, **settings):
    from test_assistant_guards import run

    return run(
        [
            ModelTurn(tool_calls=[query()]),
            findings("window_summary e1.healthkit", "window_summary e1.google_health"),
            *turns,
        ],
        results={"resting_heart_rate": data(paired=True)},
        narrative_enabled=True,
        **settings,
    )


def test_writer_repairs_missing_source_once_using_unchanged_evidence():
    response, model, toolbox = paired_writer(
        [
            draft(ids=["e1.healthkit"]),
            draft(
                "Both Apple Health and Google Health have recorded readings.",
                ids=["e1.healthkit", "e1.google_health"],
            ),
        ]
    )
    assert response.narrative_status == "generated"
    assert response.narrative_reason is None
    assert response.explanation_status == "model_generated"
    assert len(model.requests) == 4
    original, repaired = model.requests[-2][0], model.requests[-1][0]
    assert repaired[:2] == original
    assert model.requests[-1][1] == []
    repair = json.loads(repaired[-1]["content"])
    assert repair["checker_feedback"][0]["missing_evidence_ids"] == ["e1.google_health"]
    assert response.narrative_edit_attempted
    assert response.narrative_check_reason == "evidence_coverage_mismatch"
    assert [c.name for c in response.tool_calls] == ["daily_summary", "state_findings"]


def test_editor_missing_evidence_keeps_feedback_without_a_second_quality_veto():
    response, model, _ = paired_writer([draft(), draft(), draft()])
    assert response.narrative_status == "generated"
    assert response.narrative_reason is None
    assert response.narrative_check_reason == "evidence_coverage_mismatch"
    assert response.explanation_status == "model_generated"
    assert len(model.requests) == 4


def test_writer_repair_cannot_consume_a_turn_beyond_request_budget():
    response, model, _ = paired_writer([draft(), draft()], max_model_turns=3)
    assert response.narrative_status == "fallback"
    assert response.narrative_reason == "model_turn_budget"
    assert len(model.requests) == 3


def test_writer_repair_shares_original_timeout():
    class SlowRepair(ScriptedChatModel):
        async def complete(self, messages, tools):
            if not tools and len(messages) > 2:
                await asyncio.sleep(1)
            return await super().complete(messages, tools)

    model = SlowRepair(
        [
            ModelTurn(tool_calls=[query()]),
            findings("window_summary e1.healthkit"),
            draft(ids=["other"]),
        ]
    )
    assistant = Assistant(
        model,
        FakeToolbox({"daily_summary": data()}),
        AssistantSettings(narrative_enabled=True, narrative_timeout_seconds=0.05),
    )
    response = asyncio.run(
        assistant.ask(AskRequest(question="q", timezone=NY), USER, request_spec=spec())
    )
    assert response.narrative_status == "fallback"
    assert response.narrative_reason == "model_unavailable_or_timeout"
    assert response.explanation_status == "verified"


def test_editor_can_introduce_unsupported_values_without_second_quality_check(monkeypatch):
    count = 0
    original = narrative.check_draft

    def counted(*args):
        nonlocal count
        count += 1
        return original(*args)

    monkeypatch.setattr(narrative, "check_draft", counted)
    response, model, _ = paired_writer(
        [draft(), draft("Your heart rate is 999999 bpm.", ids=["e1.healthkit", "e1.google_health"])]
    )
    assert count == 1
    assert response.narrative_status == "generated"
    assert response.narrative_reason is None
    assert response.explanation_status == "model_generated"
    assert "999999" in response.answer
    assert response.numeric_consistency.status == "not_applicable"
    assert len(model.requests) == 4


@pytest.mark.parametrize(
    "bad",
    [
        draft("999999 bpm"),
        draft("On 2030-01-01 the value was 1000."),
        ModelTurn(text="broken JSON"),
        ModelTurn(tool_calls=[call("daily_summary", metric="steps")]),
        draft("<b>Unsupported markup</b>"),
    ],
)
def test_every_checker_failure_can_reach_final_editor(bad):
    response, model = run_writer(
        bad, draft("The available readings do not establish health status.")
    )
    assert response.narrative_status == "generated"
    assert response.narrative_check_reason is not None
    assert response.narrative_edit_attempted
    assert len(model.requests) == 4
    before, after = model.requests[-2][0], model.requests[-1][0]
    assert after[:2] == before
    assert json.loads(after[-1]["content"])["checker_feedback"]


def test_checker_feedback_reports_multiple_issues_and_possible_false_positive():
    _, issues = narrative.check_draft(draft("999999 steps", ids=["invented"]), facts())
    assert [i["code"] for i in issues] == ["evidence_coverage_mismatch", "unsupported_number"]
    assert issues[0]["missing_evidence_ids"] == ["e1.healthkit"]
    assert issues[0]["unknown_evidence_ids"] == ["invented"]
    assert issues[1]["unmatched_numbers"] == ["999999"]
    assert "false positives" in issues[1]["explanation"]


def test_final_editor_tool_calls_are_not_executed():
    response, model = run_writer(draft("999999 bpm"), ModelTurn(tool_calls=[query()]))
    assert response.narrative_status == "fallback"
    assert response.narrative_reason == "unexpected_tool_call"
    assert [r.name for r in response.tool_calls] == ["daily_summary", "state_findings"]
    assert len(model.requests) == 4
