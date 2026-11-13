"""Lossless removal of repeated tool definitions from a model-request copy."""

import copy
import json


def request_size(messages, tools=()):
    """Count Unicode characters, not JSON's optional ASCII escape expansion."""
    return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":"))) + len(
        json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
    )


def compact_messages(messages):
    """Keep every observation, warning and evidence ID; reuse identical definitions.

    Earlier tool messages stay in the same request. A changed definition is always
    retained. The stored transcript and evidence cards are never mutated.
    """
    result = copy.deepcopy(messages)
    definitions = {}
    hints = set()
    for message in result:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message.get("content", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        current = payload.get("definitions")
        if isinstance(current, dict):
            repeated = [
                key
                for key, value in current.items()
                if key in definitions and definitions[key] == value
            ]
            definitions.update(current)
            payload["definitions"] = {k: v for k, v in current.items() if k not in repeated}
            if repeated:
                payload["reuse_prior_tool_definitions"] = repeated
        hint = payload.get("hint")
        if isinstance(hint, str):
            if hint in hints:
                del payload["hint"]
            hints.add(hint)
        message["content"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return result
