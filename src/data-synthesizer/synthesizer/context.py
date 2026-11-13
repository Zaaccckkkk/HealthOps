"""LLM-generated life-event context: an event library plus per-user schedules.

The LLM only writes semi-structured events (stress, workout, travel, illness, fatigue);
measurements are produced by the statistical generator. Requests use the same two backends as
the API's assistant (healthops/assistant/model.py): Ollama's native /api/chat with reasoning
control, or an OpenAI-compatible /v1/chat/completions endpoint.

Every response is cached by a hash of (model, system, user) in llm_cache.jsonl inside the
output version, so a rerun of the same version, or a new version that reuses the cache
(llm.reuse_cache_from), reproduces the same context without calling the model.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import httpx
import numpy as np

log = logging.getLogger("context")

FIELDS = ("stress", "workout", "travel", "illness", "subjective_fatigue")


class ContextError(ValueError):
    """An LLM answer that does not match the event schema."""


class LLMClient:
    def __init__(self, base_url: str, cfg: dict, cache: dict[str, str], cache_file: Path):
        self.base_url = base_url.rstrip("/")
        self.cfg = cfg
        self.cache = cache
        self.cache_file = cache_file

    def unavailable_reason(self) -> str | None:
        """None when the model can be called; otherwise why not (checked once, before use)."""
        try:
            if self.cfg["backend"] == "ollama":
                response = httpx.get(f"{self.base_url}/api/tags", timeout=10)
                response.raise_for_status()
                names = {m.get("name") for m in response.json().get("models", [])}
                model = self.cfg["model"]
                if model not in names and f"{model}:latest" not in names:
                    return f"model {model} is not pulled on {self.base_url} (ollama pull {model})"
            else:
                httpx.get(f"{self.base_url}/v1/models", timeout=10).raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            return f"{self.base_url} is unreachable ({type(exc).__name__})"
        return None

    def complete_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        """One JSON answer. With a schema, the backend constrains generation to it (valid JSON,
        enum values only); the answer is still validated by the caller."""
        key = hashlib.sha256(
            json.dumps([self.cfg["model"], system, user, schema], sort_keys=True).encode()
        ).hexdigest()
        if key not in self.cache:
            self.cache[key] = self._request(system, user, schema)
            with self.cache_file.open("a") as stream:
                stream.write(json.dumps({"key": key, "response": self.cache[key]}) + "\n")
        try:
            return json.loads(self.cache[key])
        except ValueError as exc:
            raise ContextError("response is not JSON") from exc

    def _request(self, system: str, user: str, schema: dict | None) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        cfg = self.cfg
        if cfg["backend"] == "ollama":
            url = f"{self.base_url}/api/chat"
            body = {
                "model": cfg["model"],
                "messages": messages,
                "stream": False,
                "format": schema or "json",
                "options": {
                    "temperature": cfg["temperature"],
                    "num_predict": cfg["max_output_tokens"],
                },
            }
            if cfg.get("thinking") is not None:
                body["think"] = cfg["thinking"]
        elif cfg["backend"] == "openai_compatible":
            url = f"{self.base_url}/v1/chat/completions"
            body = {
                "model": cfg["model"],
                "messages": messages,
                "temperature": cfg["temperature"],
                "max_tokens": cfg["max_output_tokens"],
                "response_format": (
                    {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
                    if schema
                    else {"type": "json_object"}
                ),
            }
        else:
            raise ValueError(f"unknown LLM backend {cfg['backend']!r}")
        response = httpx.post(url, json=body, timeout=cfg["timeout_seconds"])
        response.raise_for_status()
        payload = response.json()
        if cfg["backend"] == "ollama":
            return payload["message"]["content"]
        return payload["choices"][0]["message"]["content"]


def load_cache(*files: Path) -> dict[str, str]:
    cache = {}
    for path in files:
        if path.exists():
            for line in path.read_text().splitlines():
                entry = json.loads(line)
                cache[entry["key"]] = entry["response"]
    return cache


def context_json_schema(schema: dict) -> dict:
    low, high = schema["subjective_fatigue"]
    return {
        "type": "object",
        "properties": {
            "stress": {"type": "string", "enum": schema["stress"]},
            "workout": {"type": "string", "enum": schema["workout"]},
            "travel": {"type": "boolean"},
            "illness": {"type": "boolean"},
            "subjective_fatigue": {"type": "integer", "minimum": low, "maximum": high},
        },
        "required": list(FIELDS),
        "additionalProperties": False,
    }


def _list_schema(key: str, item_properties: dict, max_items: int) -> dict:
    item = {
        "type": "object",
        "properties": item_properties,
        "required": list(item_properties),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {key: {"type": "array", "items": item, "maxItems": max_items}},
        "required": [key],
    }


def library_json_schema(cfg: dict) -> dict:
    low, high = cfg["schema"]["duration_days"]
    return _list_schema(
        "templates",
        {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "duration_days": {"type": "integer", "minimum": low, "maximum": high},
            "weight": {"type": "number", "exclusiveMinimum": 0},
            "context": context_json_schema(cfg["schema"]),
        },
        cfg["library"]["batch_size"],
    )


def schedule_json_schema(cfg: dict, n_days: int, max_events: int) -> dict:
    low, high = cfg["schema"]["duration_days"]
    return _list_schema(
        "events",
        {
            "start_day": {"type": "integer", "minimum": 0, "maximum": n_days - 1},
            "name": {"type": "string"},
            "duration_days": {"type": "integer", "minimum": low, "maximum": high},
            "context": context_json_schema(cfg["schema"]),
        },
        max_events,
    )


def validate_context(context: dict, schema: dict) -> dict:
    if not isinstance(context, dict) or set(context) != set(FIELDS):
        raise ContextError(f"context must have exactly {FIELDS}")
    low, high = schema["subjective_fatigue"]
    fatigue = context["subjective_fatigue"]
    if (
        context["stress"] not in schema["stress"]
        or context["workout"] not in schema["workout"]
        or not isinstance(context["travel"], bool)
        or not isinstance(context["illness"], bool)
        or not isinstance(fatigue, int)
        or not low <= fatigue <= high
    ):
        raise ContextError(f"context values out of schema: {context}")
    return dict(context)


def _duration(value, schema: dict) -> int:
    low, high = schema["duration_days"]
    if not isinstance(value, int) or not low <= value <= high:
        raise ContextError(f"duration_days must be an integer in [{low}, {high}]")
    return value


def validate_template(item: dict, schema: dict) -> dict:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise ContextError("template needs a name")
    weight = item.get("weight")
    if not isinstance(weight, int | float) or weight <= 0:
        raise ContextError("weight must be a positive number")
    return {
        "name": item["name"].strip().lower().replace(" ", "_"),
        "description": str(item.get("description", ""))[:200],
        "duration_days": _duration(item.get("duration_days"), schema),
        "weight": float(weight),
        "context": validate_context(item.get("context"), schema),
    }


def build_library(client: LLMClient | None, cfg: dict) -> tuple[list[dict], str]:
    """Templates from the LLM, or the seed library when no LLM is available."""
    schema, seed = cfg["schema"], [validate_template(t, cfg["schema"]) for t in cfg["seed_library"]]
    if client is None:
        return seed, "seed_library"
    library = {t["name"]: t for t in seed}
    generated = 0
    prompts, size = cfg["prompts"], cfg["library"]["size"]
    examples = json.dumps(cfg["seed_library"][:3])
    for batch in range(int(np.ceil(size / cfg["library"]["batch_size"])) * 2):
        if len(library) >= size:
            break
        user = prompts["library_user"].format(
            count=cfg["library"]["batch_size"], existing=", ".join(library), examples=examples
        )
        try:
            answer = client.complete_json(prompts["library_system"], user, library_json_schema(cfg))
            items = answer["templates"] if isinstance(answer, dict) else []
        except (ContextError, KeyError, httpx.HTTPError) as exc:
            log.warning("library batch %d failed: %s", batch, exc)
            continue
        for item in items:
            try:
                template = validate_template(item, schema)
            except ContextError as exc:
                log.warning("dropped template: %s", exc)
                continue
            if template["name"] not in library:
                library[template["name"]] = template
                generated += 1
    return list(library.values())[:size], "llm" if generated else "seed_library"


def per_user_schedule(
    client: LLMClient, cfg: dict, prompt_fields: dict, n_days: int, library: list[dict]
) -> list[dict]:
    """Events for one user from the LLM; raises ContextError when no valid answer arrives."""
    prompts, schema = cfg["prompts"], cfg["schema"]
    expected = cfg["sampling"]["events_per_week"] * n_days / 7
    max_events = min(cfg["per_user"]["max_events"], max(2, int(np.ceil(expected * 1.5))))
    user = prompts["per_user_user"].format(
        **prompt_fields,
        n_days=n_days,
        min_events=max(1, min(max_events - 1, int(expected * 0.5))),
        max_events=max_events,
        library_names=", ".join(t["name"] for t in library),
    )
    error: Exception | None = None
    for attempt in range(cfg["llm"]["max_attempts"]):
        system = prompts["per_user_system"] + (
            "" if attempt == 0 else f"\n(Attempt {attempt + 1}.)"
        )
        try:
            answer = client.complete_json(
                system, user, schedule_json_schema(cfg, n_days, max_events)
            )
            events = []
            for item in answer["events"]:
                start = item["start_day"]
                if not isinstance(start, int) or not 0 <= start < n_days:
                    raise ContextError("start_day outside the schedule")
                events.append(
                    {
                        "start_day": start,
                        "name": str(item["name"]).strip().lower().replace(" ", "_"),
                        "duration_days": _duration(item["duration_days"], schema),
                        "context": validate_context(item["context"], schema),
                        "source": "llm_per_user",
                    }
                )
            return sorted(events, key=lambda e: e["start_day"])
        except (ContextError, KeyError, TypeError, httpx.HTTPError) as exc:
            error = exc
    raise ContextError(f"no valid schedule after {cfg['llm']['max_attempts']} attempts: {error}")


def sample_events(
    rng: np.random.Generator, n_days: int, library: list[dict], cfg: dict
) -> list[dict]:
    weights = np.array([t["weight"] for t in library])
    probability = cfg["sampling"]["events_per_week"] / 7
    events, day = [], 0
    while day < n_days:
        if rng.random() < probability:
            template = library[rng.choice(len(library), p=weights / weights.sum())]
            events.append(
                {
                    "start_day": day,
                    "name": template["name"],
                    "duration_days": template["duration_days"],
                    "context": template["context"],
                    "source": "library",
                }
            )
            day += template["duration_days"]
        else:
            day += 1
    return events


def daily_context(n_days: int, events: list[dict], default: dict) -> list[dict]:
    """One context per day; later events never overwrite days an earlier event covers."""
    days = [{**default, "event": None} for _ in range(n_days)]
    taken = [False] * n_days
    for event in events:
        for day in range(
            event["start_day"], min(n_days, event["start_day"] + event["duration_days"])
        ):
            if not taken[day]:
                days[day] = {**event["context"], "event": event["name"]}
                taken[day] = True
    return days
