"""Structured UI hints stay subordinate to the existing router and scope checks."""

import asyncio
from datetime import UTC, datetime

from test_assistant_routing import USER, app, decision, request

from healthops.assistant.routing import RequestSpec, clarification_form


def test_only_missing_fields_are_offered_without_defaults():
    spec = RequestSpec(
        intent="summary",
        metrics=[],
        window=None,
        baseline=None,
        language="en",
        unresolved_fields=["metrics", "time_range"],
    )
    form = clarification_form(spec)
    assert [f.id for f in form.fields] == ["metrics", "time_range"]
    assert form.fields[0].selection == "multiple"
    assert form.fields[1].selection == "single"
    assert [o.reply_text for o in form.fields[1].options] == [
        "past 7 days",
        "past 15 days",
        "past 30 days",
    ]
    assert "selected" not in form.model_dump_json()
    spec.unresolved_fields = ["baseline"]
    assert clarification_form(spec).fields[0].options == []


def test_pending_response_has_form_and_fixed_expiry_but_no_tools():
    async def scenario():
        assistant, _, tools = app(
            [decision("needs_clarification"), decision("needs_clarification")]
        )
        first = await assistant.ask(request("How am I doing lately?"), USER)
        assert first.clarification.version == 1
        assert first.conversation_expires_at > datetime.now(UTC)
        second = await assistant.ask(request("Still not sure", first), USER)
        assert second.conversation_expires_at == first.conversation_expires_at
        assert second.conversation_version == 2
        assert not tools.calls

    asyncio.run(scenario())


def test_empty_reference_ends_without_invented_claim_or_retry():
    from test_assistant import ask, call, make

    from healthops.assistant.model import ModelTurn

    assistant, model, _ = make(
        [ModelTurn(tool_calls=[call("lookup_reference", query="unlisted reference")])],
        results={
            "lookup_reference": {
                "query": "unlisted reference",
                "references": [],
                "warnings": ["no_reference_found"],
                "definitions": {},
            }
        },
    )
    response = ask(assistant, "Define the unlisted reference.")
    assert response.status == "insufficient_data"
    assert response.explanation_status == "not_applicable"
    assert not response.cards and not response.claims
    assert "No matching reference" in response.answer
    assert len(model.requests) == 1
