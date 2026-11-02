"""Protocol tests for the OpenAI-compatible HTTP adapter using an in-process mock transport."""

import asyncio
import json

import httpx
import pytest

from healthops.assistant.model import (
    ModelError,
    OllamaChatModel,
    OpenAICompatibleChatModel,
    parse_chat_completion,
    parse_ollama_chat,
)


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


# ---- Ollama native adapter ----


def ollama_with(handler, **kwargs):
    return OllamaChatModel(
        "http://model.test",
        "qwen3:8b",
        prompt_version="test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_ollama_request_carries_think_flag_options_and_native_message_shapes():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "done"},
                "prompt_eval_count": 10,
                "eval_count": 2,
            },
        )

    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "daily_summary", "arguments": '{"metric": "steps"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "daily_summary", "content": '{"x":1}'},
    ]
    turn = asyncio.run(
        ollama_with(handler, thinking=False).complete(messages, [{"type": "function"}])
    )
    body = seen["body"]
    assert seen["path"] == "/api/chat"
    assert body["think"] is False and body["stream"] is False
    assert body["options"] == {"temperature": 0.0, "num_predict": 800}
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == {"metric": "steps"}
    assert body["messages"][3]["tool_name"] == "daily_summary"
    assert turn.text == "done" and (turn.prompt_tokens, turn.completion_tokens) == (10, 2)


def test_ollama_think_flag_is_omitted_when_unset_and_recorded_when_set():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "x"}})

    asyncio.run(ollama_with(handler).complete([{"role": "user", "content": "q"}], []))
    assert "think" not in seen["body"]
    model = ollama_with(handler, thinking=True)
    assert model.info.thinking is True
    asyncio.run(model.complete([{"role": "user", "content": "q"}], []))
    assert seen["body"]["think"] is True


def test_ollama_tool_call_arguments_object_is_normalised_and_thinking_text_ignored():
    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "thinking": "internal reasoning text",
            "tool_calls": [
                {
                    "function": {
                        "index": 0,
                        "name": "daily_summary",
                        "arguments": {"metric": "steps", "start_date": "2026-10-01"},
                    }
                }
            ],
        },
        "prompt_eval_count": 191,
        "eval_count": 52,
    }
    turn = parse_ollama_chat(payload)
    assert json.loads(turn.tool_calls[0].arguments_json) == {
        "metric": "steps",
        "start_date": "2026-10-01",
    }
    assert turn.text == "" and turn.completion_tokens == 52


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": None},
        {"message": {"content": 3}},
        {"message": {"tool_calls": [{"function": {}}]}},
        [],
    ],
)
def test_ollama_malformed_shapes_become_model_error(payload):
    with pytest.raises(ModelError):
        parse_ollama_chat(payload)


def test_ollama_describe_records_digest_quantization_and_both_contexts():
    def handler(request: httpx.Request):
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "qwen3:8b", "digest": "500a1f067a9f" + "0" * 52}]}
            )
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "details": {"quantization_level": "Q4_K_M"},
                    "model_info": {"qwen3.context_length": 40960},
                },
            )
        if request.url.path == "/api/ps":
            return httpx.Response(
                200, json={"models": [{"name": "qwen3:8b", "context_length": 32768}]}
            )
        return httpx.Response(404)

    info = asyncio.run(ollama_with(handler).describe())
    assert info.digest == "500a1f067a9f" + "0" * 52  # full digest, shortened only for display
    assert info.quantization == "Q4_K_M" and info.name == "qwen3:8b"  # not mixed into the name
    assert info.model_max_context == 40960 and info.loaded_context == 32768


def test_ollama_describe_leaves_loaded_context_null_when_nothing_is_running():
    def handler(request: httpx.Request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b", "digest": "abc"}]})
        return httpx.Response(500)

    info = asyncio.run(ollama_with(handler).describe())
    assert info.loaded_context is None and info.model_max_context is None and info.digest == "abc"


def test_ollama_describe_failure_leaves_fields_null():
    def handler(request):
        raise httpx.ConnectError("down")

    info = asyncio.run(ollama_with(handler).describe())
    assert info.digest is None and info.model_max_context is None and info.loaded_context is None


@pytest.mark.parametrize("adapter", [OpenAICompatibleChatModel, OllamaChatModel])
@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ConnectError])
def test_connection_failure_is_distinct_and_not_retried(adapter, error):
    from healthops.assistant.model import ModelConnectionError

    requests = []

    def handler(request):
        requests.append(request)
        assert request.extensions["timeout"] == {"connect": 5, "read": 120, "write": 120, "pool": 5}
        raise error("synthetic unreachable host", request=request)

    model = adapter(
        "http://model.test",
        "qwen3:8b",
        prompt_version="test",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ModelConnectionError):
        complete(model)
    assert len(requests) == 1


@pytest.mark.parametrize("adapter", [OpenAICompatibleChatModel, OllamaChatModel])
def test_inference_read_timeout_is_not_classified_as_connection_failure(adapter):
    from healthops.assistant.model import ModelConnectionError

    def handler(request):
        assert request.extensions["timeout"]["read"] == 90
        assert request.extensions["timeout"]["connect"] == 2
        raise httpx.ReadTimeout("synthetic slow generation", request=request)

    model = adapter(
        "http://model.test",
        "qwen3:8b",
        prompt_version="test",
        timeout_seconds=90,
        connect_timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ModelError) as caught:
        complete(model)
    assert not isinstance(caught.value, ModelConnectionError)


def test_timeout_settings_do_not_extend_a_shorter_existing_budget():
    from pydantic import ValidationError

    from healthops.assistant.model import model_timeout
    from healthops.config import Settings

    assert model_timeout(0.1, 5).connect == 0.1
    with pytest.raises(ValidationError):
        Settings(assistant_model_connect_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(assistant_model_timeout_seconds=0)
