"""Run the frozen synthetic diagnostic set locally; never seed data or alter the app."""

import argparse
import asyncio
import json
import statistics
import time
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import httpx
from prepare_cohort_baseline import encoded, sha, snapshot, verify_artifacts
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from healthops.assistant.contracts import AskRequest
from healthops.assistant.model import OllamaChatModel
from healthops.assistant.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from healthops.assistant.time_semantics import describe, resolve
from healthops.assistant.toolbox import Toolbox, build_registry
from healthops.config import Settings
from healthops.main import build_assistant
from healthops.models import User
from healthops.synthetic_import import NAMESPACE


class PinnedTransport(httpx.AsyncBaseTransport):
    """Pin context for every chat request and retain local raw model turns for diagnosis."""

    def __init__(self):
        self.turns = []

    async def handle_async_request(self, request):
        body = json.loads(request.content)
        body["options"]["num_ctx"] = 32768
        body["keep_alive"] = "10m"
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            response = await client.post(str(request.url), json=body)
        self.turns.append(
            {"request": body, "status_code": response.status_code, "response": response.json()}
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers={"content-type": "application/json"},
            request=request,
        )


def argument_match(expected, actual, raw=False):
    if expected is None:
        return None
    ignored = {"detail"} if "category" in expected else set()
    if not raw:
        ignored |= {"timezone"}
        if "provider" not in expected and actual.get("provider") is None:
            ignored.add("provider")
    return {k: str(v) for k, v in actual.items() if k not in ignored} == {
        k: str(v) for k, v in expected.items()
    }


def grade(item, response):
    expected = item["expected"]
    first = response.tool_calls[0] if response.tool_calls else None
    selected = bool(first and first.name == expected["tool"])
    raw_ok = selected and argument_match(expected["model_arguments"], first.model_arguments, True)
    executed_ok = selected and argument_match(expected["arguments"], first.arguments)
    if expected["tool"] == "lookup_reference":
        query = str(first.model_arguments.get("query", "")).lower() if first else ""
        raw_ok = selected and all(t.lower() in query for t in expected["query_topics"])
        executed_ok = None
    gold = expected.get("tool_result")
    evidence_ok = (
        any(e["tool"] == expected["tool"] and e["result"] == gold for e in response.evidence)
        if gold is not None
        else None
    )
    return {
        "id": item["id"],
        "category": item["category"],
        "cohort": item["cohort"],
        "status": response.status,
        "first_tool": first.name if first else None,
        "tool_selected_ok": selected,
        "raw_arguments_ok": raw_ok,
        "executed_arguments_ok": executed_ok,
        "gold_evidence_ok": evidence_ok,
        "explanation_status": response.explanation_status,
        "valid_claims": sum(c.valid for c in response.claims),
        "total_claims": len(response.claims),
        "answer": response.answer,
        "adjustments": first.warnings if first else [],
        "refusal_ok": (selected and executed_ok and response.status == "refused")
        if expected["tool"] == "decline_request"
        else None,
    }


def preflight(rows, lock, factory):
    registry = build_registry()
    if len(rows) != 30 or len({r["id"] for r in rows}) != 30 or lock.get("format_version") != 2:
        raise ValueError("Expected the corrected 30-case v2 set")
    with Session(factory.kw["bind"]) as session:
        for item in rows:
            uid = uuid5(
                NAMESPACE,
                f"{lock['data_version']}:{lock['manifest_sha256']}:{item['source_user_id']}",
            )
            user = session.get(User, uid)
            label = f"Synthetic {item['cohort']} {str(uid)[:8]}"
            if str(uid) != item["api_user_id"] or user is None or user.display_name != label:
                raise ValueError("Synthetic identity mismatch")
            prompt = SYSTEM_PROMPT.format(
                time_semantics=describe(
                    resolve(item["timezone"], date.fromisoformat(item["today"]))
                )
            )
            if sha(prompt.encode()) != item["rendered_system_prompt_sha256"]:
                raise ValueError("Rendered prompt mismatch")
            expected = item["expected"]
            if "tool_result" in expected:
                spec = registry[expected["tool"]]
                params = spec.params.model_validate(
                    {**expected["arguments"], "timezone": item["timezone"]}
                )
                actual = spec.run(session, uid, params).model_dump(mode="json")
                if actual != expected["tool_result"]:
                    raise ValueError("Database/tool output differs from frozen gold")


async def run(args):
    with httpx.Client(base_url="http://127.0.0.1:11434", timeout=120, trust_env=False) as client:
        current = snapshot(client)
        lock = verify_artifacts(args.dataset, current)
    rows = [
        json.loads(line) for line in (args.dataset / "questions.jsonl").read_text().splitlines()
    ]
    engine = create_engine(
        "postgresql+psycopg://healthops:local-only-password@127.0.0.1:55432/healthops",
        connect_args={"options": "-c default_transaction_read_only=on"},
        hide_parameters=True,
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    preflight(rows, lock, factory)
    settings = Settings(
        _env_file=None,
        **(
            lock["observed_api_settings"]
            | {
                "environment": "local",
                "assistant_model_base_url": "http://127.0.0.1:11434",
                "assistant_allow_today_override": True,
                "assistant_max_concurrency": 1,
            }
        ),
    )
    transport = PinnedTransport()
    model = OllamaChatModel(
        "http://127.0.0.1:11434",
        "qwen3:8b",
        prompt_version=PROMPT_VERSION,
        temperature=settings.assistant_temperature,
        thinking=settings.assistant_thinking,
        max_output_tokens=settings.assistant_max_output_tokens,
        transport=transport,
    )
    assistant = build_assistant(settings, factory, model)
    # This set grades reference routing only; no unversioned retrieval is included.
    assistant.toolbox = Toolbox(factory)
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    metadata = {
        "status": "running",
        "dataset_lock_sha256": sha((args.dataset / "lock.json").read_bytes()),
        "model": current["model"],
        "num_ctx": 32768,
        "concurrency": 1,
        "reference_mode": "routing_only_knowledge_unavailable",
        "execution": "in_process_orchestrator_read_only_local_postgresql",
        "settings": settings.model_dump(mode="json", include=set(lock["observed_api_settings"])),
        "warmup": "empty prompt load, not scored",
    }
    (args.output / "run.json").write_bytes(encoded(metadata))
    # Load explicitly before scoring, then verify the actual loaded context.
    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
        response = await client.post(
            "http://127.0.0.1:11434/api/generate",
            json={
                "model": "qwen3:8b",
                "prompt": "",
                "stream": False,
                "keep_alive": "10m",
                "options": {"num_ctx": 32768},
            },
        )
        response.raise_for_status()
        ps = (await client.get("http://127.0.0.1:11434/api/ps")).json()
        loaded = next(m for m in ps["models"] if m["digest"] == current["model"]["digest"])
        if loaded.get("context_length") != 32768:
            raise ValueError("Actual loaded model context differs")
        metadata["loaded_model"] = loaded
    results = []
    for item in rows:
        transport.turns = []
        started = time.monotonic()
        response = await assistant.ask(
            AskRequest(
                question=item["question"],
                timezone=item["timezone"],
                today=date.fromisoformat(item["today"]),
                provider=item.get("request_provider"),
            ),
            UUID(item["api_user_id"]),
        )
        result = grade(item, response)
        result["latency_s"] = round(time.monotonic() - started, 3)
        results.append(result)
        (args.output / f"{item['id']}.json").write_bytes(
            encoded(
                {
                    "grade": result,
                    "response": response.model_dump(mode="json"),
                    "turns": transport.turns,
                }
            )
        )
        (args.output / "results.json").write_bytes(encoded(results))
        print(
            f"{item['id']}: {response.status}; tool={result['tool_selected_ok']} "
            f"raw_args={result['raw_arguments_ok']} gold={result['gold_evidence_ok']}",
            flush=True,
        )
    with httpx.Client(base_url="http://127.0.0.1:11434", timeout=60, trust_env=False) as client:
        verify_artifacts(args.dataset, snapshot(client))
    preflight(rows, lock, factory)
    metadata["status"] = "completed"
    (args.output / "run.json").write_bytes(encoded(metadata))
    summary = {
        "questions": len(results),
        "latency_p50_s": statistics.median(r["latency_s"] for r in results),
        "verified_explanations": sum(r["explanation_status"] == "verified" for r in results),
    }
    for field in (
        "tool_selected_ok",
        "raw_arguments_ok",
        "executed_arguments_ok",
        "gold_evidence_ok",
        "refusal_ok",
    ):
        scored = [r[field] for r in results if r[field] is not None]
        summary[field] = {"passed": sum(bool(v) for v in scored), "total": len(scored)}
    (args.output / "summary.json").write_bytes(encoded(summary))
    engine.dispose()
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        asyncio.run(run(parser.parse_args()))
    except Exception as exc:
        raise SystemExit(f"Run stopped: {type(exc).__name__}; inspect local artifacts.") from None
