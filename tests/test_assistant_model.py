"""Protocol tests for the OpenAI-compatible HTTP adapter using an in-process mock transport."""

import asyncio
import json

import httpx
import pytest

from healthops.assistant.model import ModelError, OpenAICompatibleChatModel, parse_chat_completion


def model_with(handler):
    return OpenAICompatibleChatModel(
        "http://model.test",
        "qwen3:8b",
        prompt_version="test",
        transport=httpx.MockTransport(handler),
    )


def complete(model, tools=None):
    return asyncio.run(model.complete([{"role": "user", "content": "hi"}], tools or []))


def test_tool_call_response_is_parsed_and_request_carries_tools():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "daily_summary",
                                        "arguments": '{"metric": "steps"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    tools = [{"type": "function", "function": {"name": "daily_summary", "parameters": {}}}]
    turn = complete(model_with(handler), tools)
    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["tools"] == tools and seen["body"]["tool_choice"] == "auto"
    assert seen["body"]["temperature"] == 0.0 and seen["body"]["max_tokens"] == 800
    assert turn.tool_calls[0].name == "daily_summary"
    assert json.loads(turn.tool_calls[0].arguments_json) == {"metric": "steps"}
    assert (turn.prompt_tokens, turn.completion_tokens) == (12, 3)


def test_arguments_given_as_object_are_normalised_to_json_text():
    payload = {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": "a", "arguments": {"x": 1}}}]}}
        ]
    }
    assert parse_chat_completion(payload).tool_calls[0].arguments_json == '{"x": 1}'


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": [{"message": None}]},
        {"choices": []},
        {"choices": [{"message": {"content": 5}}]},
        {"choices": [{"message": {"tool_calls": [{"function": {"name": 3}}]}}]},
        {"choices": [{"message": {"tool_calls": "oops"}}]},
        [],
        {},
    ],
)
def test_malformed_response_shapes_become_model_error(payload):
    def handler(request):
        return httpx.Response(200, json=payload)

    with pytest.raises(ModelError):
        complete(model_with(handler))


def test_http_error_status_and_non_json_become_model_error():
    with pytest.raises(ModelError, match="HTTP 500"):
        complete(model_with(lambda request: httpx.Response(500, text="boom")))
    with pytest.raises(ModelError, match="non-JSON"):
        complete(model_with(lambda request: httpx.Response(200, text="<html>")))


def test_transport_failure_becomes_model_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ModelError, match="unreachable"):
        complete(model_with(handler))


def test_thinking_setting_is_refused_until_a_backend_control_is_implemented():
    with pytest.raises(ValueError, match="thinking control is not implemented"):
        OpenAICompatibleChatModel("http://model.test", "m", prompt_version="t", thinking=True)
    with pytest.raises(ValueError, match="thinking control is not implemented"):
        OpenAICompatibleChatModel("http://model.test", "m", prompt_version="t", thinking=False)
    model = OpenAICompatibleChatModel("http://model.test", "m", prompt_version="t")
    assert model.info.thinking is None  # not controlled, not "enabled"
