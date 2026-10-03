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


class OllamaChatModel:
    """Ollama's native ``/api/chat``: the only backend here with verified reasoning control.

    ``think`` is honoured by this endpoint (verified: with ``think=False`` the response carries
    no ``thinking`` field and far fewer generated tokens), whereas Ollama's OpenAI-compatible
    endpoint ignores it. Tool-call arguments arrive as JSON objects and are normalised to text.
    """

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
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._transport = transport
        self.info = ModelInfo(
            name=model,
            endpoint_host=urlparse(base_url).hostname,
            prompt_version=prompt_version,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            thinking=thinking,
        )

    async def describe(self) -> ModelInfo:
        """Record digest (/api/tags), quantization and maximum context (/api/show) and the
        context the running instance was loaded with (/api/ps). Each lookup is independent;
        a failure leaves that field null."""
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            tags = await self._get_json(client, "GET", "/api/tags")
            for entry in (tags or {}).get("models", []) if isinstance(tags, dict) else []:
                if isinstance(entry, dict) and entry.get("name") in (
                    self._model,
                    f"{self._model}:latest",
                ):
                    if isinstance(entry.get("digest"), str):
                        self.info.digest = entry["digest"]
            show = await self._get_json(client, "POST", "/api/show", {"model": self._model})
            if isinstance(show, dict):
                details = show.get("details")
                if isinstance(details, dict) and isinstance(details.get("quantization_level"), str):
                    self.info.quantization = details["quantization_level"]
                model_info = show.get("model_info")
                if isinstance(model_info, dict):
                    for key, value in model_info.items():
                        if key.endswith(".context_length") and isinstance(value, int):
                            self.info.model_max_context = value
            ps = await self._get_json(client, "GET", "/api/ps")
            for entry in (ps or {}).get("models", []) if isinstance(ps, dict) else []:
                if isinstance(entry, dict) and entry.get("name") in (
                    self._model,
                    f"{self._model}:latest",
                ):
                    if isinstance(entry.get("context_length"), int):
                        self.info.loaded_context = entry["context_length"]
        return self.info

    async def _get_json(self, client: httpx.AsyncClient, method: str, path: str, body: Any = None):
        try:
            response = await client.request(method, f"{self._base_url}{path}", json=body)
            return response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    @staticmethod
    def to_native_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        native = []
        for message in messages:
            item: dict[str, Any] = {
                "role": message["role"],
                "content": message.get("content") or "",
            }
            if message.get("tool_calls"):
                calls = []
                for call in message["tool_calls"]:
                    arguments = call["function"].get("arguments", "{}")
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments) if arguments else {}
                        except ValueError:
                            arguments = {"_raw": arguments}
                    calls.append(
                        {"function": {"name": call["function"]["name"], "arguments": arguments}}
                    )
                item["tool_calls"] = calls
            if message["role"] == "tool" and message.get("name"):
                item["tool_name"] = message["name"]
            native.append(item)
        return native

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": self.to_native_messages(messages),
            "stream": False,
            "options": {
                "temperature": self.info.temperature,
                "num_predict": self.info.max_output_tokens,
            },
        }
        if tools:
            body["tools"] = tools
        if self.info.thinking is not None:
            body["think"] = self.info.thinking
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(f"{self._base_url}/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise ModelError(f"model server unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ModelError(f"model server returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelError("model server returned a non-JSON body") from exc
        return parse_ollama_chat(payload)


def parse_ollama_chat(payload: Any) -> ModelTurn:
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
        raise ModelError("model server returned an unexpected response shape: no message")
    message = payload["message"]
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
        arguments = function.get("arguments", {})
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
    prompt_tokens = payload.get("prompt_eval_count")
    completion_tokens = payload.get("eval_count")
    return ModelTurn(
        text=content,
        tool_calls=calls,
        prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
        completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
    )
