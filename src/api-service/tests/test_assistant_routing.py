"""Synthetic multi-turn protocol tests; scripted routing is not a model-quality score."""

import asyncio
import copy
from datetime import date
from uuid import uuid4

import pytest
from test_assistant import USER, FakeToolbox, call

from healthops.assistant.contracts import AskRequest
from healthops.assistant.conversations import ConversationConflict, ConversationStore
from healthops.assistant.model import ModelTurn, ScriptedChatModel
from healthops.assistant.orchestrator import Assistant, AssistantSettings
from healthops.assistant.routed import RoutedAssistant
from healthops.assistant.routing import WindowProposal, resolve_window
from healthops.assistant.time_semantics import resolve


class MemoryStore:
    """Protocol double. SQL owner/CAS behavior is tested separately with PostgreSQL."""

    def __init__(self):
        self.rows = {}

    def load(self, cid, user, version):
        owner, v, pending, payload = self.rows[cid]
        if (owner, v, pending) != (user, version, True):
            raise ConversationConflict()
        return copy.deepcopy(payload)

    def save(self, cid, user, version, payload, pending):
        if cid:
            self.load(cid, user, version)
        elif not pending:
            return None, None
        else:
            cid, version = uuid4(), 0
        self.rows[cid] = (user, version + 1, pending, copy.deepcopy(payload))
        return (cid, version + 1) if pending else (None, None)


def decision(route="ready", **kwargs):
    return ModelTurn(
        tool_calls=[
            call(
                "route_request",
                decision=route,
                task=kwargs.get("intent", "summary"),
                metrics=kwargs.get("metrics", []),
                time_phrase=(kwargs.get("window") or {}).get("phrase", ""),
                baseline_phrase=(kwargs.get("baseline") or {}).get("phrase", ""),
                language=kwargs.get("language", "en"),
                reason=kwargs.get("reason", "out_of_scope" if route == "unsupported" else "none"),
            )
        ]
    )


def app(turns, store=None):
    model = ScriptedChatModel(turns)
    tools = FakeToolbox({})
    core = Assistant(model, tools, AssistantSettings(allow_today_override=True))
    return RoutedAssistant(core, store or MemoryStore()), model, tools


def request(question, response=None, **kwargs):
    return AskRequest(
        question=question,
        timezone="UTC",
        today=date(2026, 8, 10),
        conversation_id=response.conversation_id if response else None,
        conversation_version=response.conversation_version if response else None,
        **kwargs,
    )


def test_clarify_then_resume_through_new_instance():
    async def scenario():
        store = MemoryStore()
        first, _, tools = app([decision("needs_clarification")], store)
        pending = await first.ask(request("How have I been doing recently?"), USER)
        assert pending.status == "needs_clarification"
        assert pending.request_spec["unresolved_fields"] == ["metrics", "time_range"]
        assert not tools.calls and pending.conversation_id
        second, model, _ = app(
            [
                decision(
                    metrics=["sleep_duration"], window={"phrase": "7 days", "kind": "past_7_days"}
                ),
                ModelTurn(
                    tool_calls=[
                        call("decline_request", category="out_of_scope", detail="test exit")
                    ]
                ),
            ],
            store,
        )
        result = await second.ask(request("7 days, sleep duration only", pending), USER)
        assert result.routing_status == "ready" and result.conversation_id is None
        assert result.request_spec["window"] == {
            "start_date": "2026-08-04",
            "end_date": "2026-08-10",
        }
        assert "How have I been doing recently?" in str(model.requests[0])
        assert "confirmed_request" in str(model.requests[1])
        assert str(USER) not in str(model.requests)
        with pytest.raises(ConversationConflict):
            await second.ask(request("7 days", pending), USER)

    asyncio.run(scenario())


def test_model_cannot_turn_recently_into_seven_days():
    async def scenario():
        routed, _, tools = app(
            [decision(metrics=["steps"], window={"phrase": "recently", "kind": "past_7_days"})]
        )
        result = await routed.ask(request("My steps recently?"), USER)
        assert result.routing_status == "needs_clarification"
        assert result.request_spec["window"] is None and not tools.calls

    asyncio.run(scenario())


def test_suggested_options_are_not_user_confirmation():
    s = resolve("UTC", date(2026, 8, 10))
    assert (
        resolve_window(
            WindowProposal(phrase="7 days", kind="past_7_days"), ["recently", "yes"], s, 93
        )
        is None
    )


@pytest.mark.parametrize("kind,phrase", [("explicit", "2026-08-09"), ("yesterday", "yesterday")])
def test_dates_are_grounded_and_resolved(kind, phrase):
    w = WindowProposal(
        kind=kind, phrase=phrase, start_date=date(2026, 8, 9), end_date=date(2026, 8, 9)
    )
    result = resolve_window(w, [phrase], resolve("UTC", date(2026, 8, 10)), 93)
    assert str(result.start_date) == "2026-08-09"


def test_unsupported_and_malformed_router_never_execute_tools():
    async def scenario():
        routed, _, tools = app([decision("unsupported", reason="medical_advice")])
        result = await routed.ask(request("Diagnose my illness"), USER)
        assert result.status == "refused" and not tools.calls
        routed, _, tools = app([ModelTurn(text="just trust me")])
        result = await routed.ask(request("My steps yesterday?"), USER)
        assert result.status == "model_error" and not tools.calls

    asyncio.run(scenario())


def test_partial_reply_and_round_limit():
    async def scenario():
        routed, _, tools = app([decision("needs_clarification") for _ in range(4)])
        response = None
        for _ in range(4):
            response = await routed.ask(request("not sure", response), USER)
        assert response.conversation_id is None
        assert "limit" in response.answer and not tools.calls

    asyncio.run(scenario())


def test_foreign_owner_scope_change_and_cancel():
    async def scenario():
        routed, model, _ = app([decision("needs_clarification")])
        pending = await routed.ask(request("recently?"), USER)
        with pytest.raises(ConversationConflict):
            await routed.ask(request("7 days", pending), uuid4())
        with pytest.raises(ConversationConflict):
            await routed.ask(request("7 days", pending, provider="healthkit"), USER)
        cancelled = await routed.ask(request("cancel", pending, cancel=True), USER)
        assert cancelled.routing_status == "cancelled"
        assert len(model.requests) == 1

    asyncio.run(scenario())


def test_changed_request_replaces_old_scope():
    async def scenario():
        routed, _, _ = app(
            [
                decision("needs_clarification", metrics=["sleep_duration"]),
                decision(metrics=["steps"], window={"phrase": "yesterday", "kind": "yesterday"}),
                ModelTurn(
                    tool_calls=[
                        call("decline_request", category="out_of_scope", detail="test exit")
                    ]
                ),
            ]
        )
        pending = await routed.ask(request("Sleep recently?"), USER)
        result = await routed.ask(request("Instead show steps yesterday", pending), USER)
        assert result.request_spec["metrics"] == ["steps"]
        assert result.request_spec["window"]["start_date"] == "2026-08-09"

    asyncio.run(scenario())


def test_sql_store_owner_version_expiry_and_recreation(engine):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.orm import sessionmaker

    from healthops.models import AssistantConversationState, User

    factory = sessionmaker(engine)
    with factory.begin() as session:
        user = User(display_name="synthetic-router")
        session.add(user)
        session.flush()
        owner = user.id
    store = ConversationStore(factory)
    cid, version = store.save(None, owner, None, {"test": "synthetic"}, True)
    assert ConversationStore(factory).load(cid, owner, version) == {"test": "synthetic"}
    with pytest.raises(ConversationConflict):
        store.load(cid, uuid4(), version)
    store.save(cid, owner, version, {}, True)
    with pytest.raises(ConversationConflict):
        store.save(cid, owner, version, {}, False)
    with factory.begin() as session:
        row = session.get(AssistantConversationState, cid)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ConversationConflict):
        store.load(cid, owner, version + 1)


def test_resume_freezes_original_reference_date():
    async def scenario():
        routed, _, _ = app(
            [
                decision("needs_clarification", metrics=["steps"]),
                decision(metrics=["steps"], window={"phrase": "yesterday", "kind": "yesterday"}),
                ModelTurn(
                    tool_calls=[
                        call("decline_request", category="out_of_scope", detail="test exit")
                    ]
                ),
            ]
        )
        pending = await routed.ask(request("Steps recently?"), USER)
        followup = request("yesterday", pending).model_copy(update={"today": None})
        result = await routed.ask(followup, USER)
        assert result.time_semantics.today == date(2026, 8, 10)
        assert result.request_spec["window"]["end_date"] == "2026-08-09"

    asyncio.run(scenario())


def test_sql_compare_and_swap_allows_only_one_consumer(engine):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy.orm import sessionmaker

    from healthops.models import User

    factory = sessionmaker(engine)
    with factory.begin() as session:
        user = User(display_name="synthetic-concurrent-router")
        session.add(user)
        session.flush()
        owner = user.id
    store = ConversationStore(factory)
    cid, version = store.save(None, owner, None, {}, True)

    def consume():
        try:
            store.save(cid, owner, version, {}, False)
            return "accepted"
        except ConversationConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: consume(), range(2)))
    assert sorted(outcomes) == ["accepted", "conflict"]


def test_guessed_metrics_and_numeric_substrings_require_clarification():
    async def scenario():
        routed, _, tools = app(
            [decision(metrics=["steps"], window={"phrase": "7 days", "kind": "past_7_days"})]
        )
        result = await routed.ask(request("How am I doing over 17 days?"), USER)
        assert result.request_spec["unresolved_fields"] == ["metrics", "time_range"]
        assert not tools.calls

    asyncio.run(scenario())


def test_flat_schema_is_complete_and_has_no_references():
    import json

    from healthops.assistant.routing import router_tool_schema

    schema = router_tool_schema()["function"]["parameters"]
    assert set(schema["required"]) == {
        "decision",
        "task",
        "metrics",
        "time_phrase",
        "baseline_phrase",
        "language",
        "reason",
    }
    assert "$ref" not in json.dumps(schema) and "$defs" not in json.dumps(schema)
    assert schema["properties"]["metrics"]["items"]["enum"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "phrase,start,end",
    [
        ("2026-09-02", "2026-09-02", "2026-09-02"),
        ("2026-09-02 through 2026-09-04", "2026-09-02", "2026-09-04"),
        ("past 15 days", "2026-07-27", "2026-08-10"),
    ],
)
def test_flat_quotes_resolve_without_model_date_arithmetic(phrase, start, end):
    from healthops.assistant.routing import quoted_window

    result = resolve_window(quoted_window(phrase), [phrase], resolve("UTC", date(2026, 8, 10)), 93)
    assert str(result.start_date) == start and str(result.end_date) == end


@pytest.mark.parametrize("phrase", ["recently", "usual", "2026-02-30", "2026-09-02 or 2026-09-04"])
def test_ambiguous_or_invalid_flat_dates_remain_unresolved(phrase):
    from healthops.assistant.routing import quoted_window

    assert quoted_window(phrase) is None


def test_missing_decision_and_task_confusion_remain_errors():
    from healthops.assistant.routing import RouterArguments

    payload = {
        "decision": "ready",
        "task": "summary",
        "metrics": ["steps"],
        "time_phrase": "yesterday",
        "baseline_phrase": "",
        "language": "en",
        "reason": "none",
    }
    with pytest.raises(ValueError):
        RouterArguments.model_validate({k: v for k, v in payload.items() if k != "decision"})
    with pytest.raises(ValueError):
        RouterArguments.model_validate(payload | {"task": "unsupported"})
    with pytest.raises(ValueError):
        RouterArguments.model_validate(payload | {"decision": "unsupported"})


def test_prompt_examples_are_complete_valid_and_not_user_scope():
    import json
    import re

    from healthops.assistant.routing import ROUTER_PROMPT, RouterArguments

    examples = re.findall(r"route_request\((\{.*?\})\)", ROUTER_PROMPT, re.DOTALL)
    assert len(examples) >= 8
    for value in examples:
        RouterArguments.model_validate(json.loads(value))
    # A date appearing only in a system example is never consent from the user.
    from healthops.assistant.routing import quoted_window

    assert (
        resolve_window(
            quoted_window("2026-09-02"), ["recently"], resolve("UTC", date(2026, 8, 10)), 93
        )
        is None
    )


@pytest.mark.parametrize("phrase", ["The past 7 days", "The past 7 days.", "the past 7 days?!"])
def test_surface_time_variants_preserve_original_grounding(phrase):
    from healthops.assistant.routing import quoted_window

    proposal = quoted_window(phrase)
    assert proposal.phrase == phrase
    semantics = resolve("UTC", date(2026, 8, 10))
    result = resolve_window(proposal, [phrase], semantics, 93)
    assert result.start_date == date(2026, 8, 4)
    assert result.end_date == date(2026, 8, 10)
    # Neither prompt examples nor assistant suggestions establish user scope.
    assert resolve_window(proposal, ["How are my steps recently?"], semantics, 93) is None
    # Do not ground a phrase the model expanded with extra words.
    assert resolve_window(proposal, ["past 7 days"], semantics, 93) is None


@pytest.mark.parametrize(
    "phrase",
    ["The recently.", "The usual!", "not the past 7 days", "past 7 days or 30 days", "2026-02-30."],
)
def test_surface_normalization_does_not_guess_or_extract_ambiguous_ranges(phrase):
    from healthops.assistant.routing import quoted_window

    assert quoted_window(phrase) is None


def test_article_in_confirmed_reply_resumes_rather_than_repeating_clarification():
    async def scenario():
        store = MemoryStore()
        first, _, _ = app([decision("needs_clarification", metrics=["steps"])], store)
        pending = await first.ask(request("How are my steps lately?"), USER)
        resumed, _, _ = app(
            [
                decision(metrics=["steps"], window={"phrase": "The past 7 days"}),
                ModelTurn(
                    tool_calls=[
                        call("decline_request", category="out_of_scope", detail="test exit")
                    ]
                ),
            ],
            store,
        )
        result = await resumed.ask(request("The past 7 days.", pending), USER)
        assert result.routing_status == "ready"
        assert result.request_spec["window"] == {
            "start_date": "2026-08-04",
            "end_date": "2026-08-10",
        }
        assert result.conversation_id is None
        with pytest.raises(ConversationConflict):
            store.load(pending.conversation_id, USER, pending.conversation_version)

    asyncio.run(scenario())


def test_connection_failure_is_reported_as_availability_not_question_interpretation():
    import httpx

    from healthops.assistant.model import MODEL_UNAVAILABLE, OllamaChatModel

    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectTimeout("synthetic host offline", request=request)

    core = Assistant(
        OllamaChatModel(
            "http://model.test",
            "qwen3:8b",
            prompt_version="test",
            transport=httpx.MockTransport(handler),
        ),
        FakeToolbox({}),
        AssistantSettings(allow_today_override=True),
    )
    routed = RoutedAssistant(core, MemoryStore())
    response = asyncio.run(routed.ask(request("Show steps yesterday."), USER))
    assert response.status == "model_error" and response.answer == MODEL_UNAVAILABLE
    assert not response.cards and not response.tool_calls and len(calls) == 1
    direct = asyncio.run(core.ask(request("Show steps yesterday."), USER))
    assert direct.status == "model_error" and direct.answer == MODEL_UNAVAILABLE
    assert not direct.cards and len(calls) == 2
