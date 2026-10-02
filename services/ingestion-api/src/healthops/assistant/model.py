"""Chat-model interface. The orchestrator depends only on ``ChatModel``.

Any OpenAI-compatible server (local Ollama now, vLLM later) can back
``OpenAICompatibleChatModel``, but a compatible HTTP shape does not guarantee identical
behaviour: tool-call parsing, chat templates and context settings differ per server. Changing
the server requires rerunning the protocol tests and the model evaluation.
"""

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from healthops.assistant.contracts import ModelInfo


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments_json: str


@dataclass
class ModelTurn:
    text: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ModelError(Exception):
    """The model server failed or returned something the client cannot use."""


class ChatModel(Protocol):
    info: ModelInfo

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        """One model turn; may return text, tool calls, or both."""


class OpenAICompatibleChatModel:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        prompt_version: str,
        timeout_seconds: float = 120,
        max_output_tokens: int = 800,
        temperature: float = 0.0,
        thinking: bool | None = None,
        extra_body: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if thinking is not None:
            # Backends control reasoning differently (Ollama `think`, vLLM chat_template_kwargs)
            # and none is implemented or verified here. Refuse rather than record a setting
            # that has no effect on the request.
            raise ValueError(
                "thinking control is not implemented for the OpenAI-compatible adapter; "
                "leave assistant_thinking unset"
            )
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._extra_body = extra_body or {}
        self._transport = transport
        self.info = ModelInfo(
            name=model,
            endpoint_host=urlparse(base_url).hostname,
            prompt_version=prompt_version,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            thinking=None,  # None means "not controlled by this adapter", never "enabled"
        )

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self.info.temperature,
            "max_tokens": self.info.max_output_tokens,
            **self._extra_body,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(f"{self._base_url}/v1/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise ModelError(f"model server unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ModelError(f"model server returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelError("model server returned a non-JSON body") from exc
        return parse_chat_completion(payload)


def parse_chat_completion(payload: Any) -> ModelTurn:
    """Validate the whole response shape; anything unexpected becomes ModelError."""
    if not isinstance(payload, dict):
        raise ModelError("model server returned an unexpected response shape: not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelError("model server returned an unexpected response shape: no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ModelError("model server returned an unexpected response shape: no message")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ModelError("model server returned an unexpected response shape: content")
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise ModelError("model server returned an unexpected response shape: tool_calls")
    calls = []
    for index, call in enumerate(raw_calls):
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ModelError("model server returned an unexpected response shape: tool call")
        arguments = function.get("arguments", "{}")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments)
        if not isinstance(arguments, str):
            raise ModelError("model server returned an unexpected response shape: arguments")
        calls.append(
            ToolCallRequest(
                id=str(call.get("id") or f"call-{index}"),
                name=function["name"],
                arguments_json=arguments,
            )
        )
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return ModelTurn(
        text=content,
        tool_calls=calls,
        prompt_tokens=usage.get("prompt_tokens")
        if isinstance(usage.get("prompt_tokens"), int)
        else None,
        completion_tokens=usage.get("completion_tokens")
        if isinstance(usage.get("completion_tokens"), int)
        else None,
    )


class ScriptedChatModel:
    """Deterministic stand-in for tests: returns the scripted turns in order."""

    def __init__(self, turns: list[ModelTurn], name: str = "scripted"):
        self._turns = list(turns)
        self.requests: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
        self.info = ModelInfo(
            name=name,
            endpoint_host=None,
            prompt_version="test",
            max_output_tokens=0,
            temperature=0.0,
        )

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        self.requests.append((copy.deepcopy(messages), tools))  # snapshot, not a live reference
        if not self._turns:
            raise ModelError("scripted model has no more turns")
        return self._turns.pop(0)
