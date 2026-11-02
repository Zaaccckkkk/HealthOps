"""Exploratory natural-language probes; no single gold call or accuracy score."""

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
from prepare_cohort_baseline import encoded, sha, snapshot, verify_artifacts
from run_cohort_baseline import PinnedTransport, preflight
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from healthops.assistant.contracts import AskRequest
from healthops.assistant.model import OllamaChatModel
from healthops.assistant.prompts import PROMPT_VERSION
from healthops.assistant.toolbox import Toolbox
from healthops.config import Settings
from healthops.main import build_assistant


async def run(args):
    cases = [json.loads(line) for line in args.questions.read_text().splitlines()]
    if not cases or len({q["id"] for q in cases}) != len(cases):
        raise ValueError("Probe IDs must be unique")
    with httpx.Client(base_url="http://127.0.0.1:11434", timeout=60, trust_env=False) as client:
        current = snapshot(client)
        lock = verify_artifacts(args.dataset, current)
    baseline = [
        json.loads(line) for line in (args.dataset / "questions.jsonl").read_text().splitlines()
    ]
    users = {r["source_user_id"]: r for r in baseline}
    engine = create_engine(
        "postgresql+psycopg://healthops:local-only-password@127.0.0.1:55432/healthops",
        connect_args={"options": "-c default_transaction_read_only=on"},
        hide_parameters=True,
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    preflight(baseline, lock, factory)
    if any(q["source_user_id"] not in users for q in cases):
        raise ValueError("Only frozen synthetic accounts may be probed")
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
    assistant.toolbox = Toolbox(factory)
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    metadata = {
        "status": "running",
        "purpose": "exploratory_not_accuracy",
        "questions_sha256": sha(args.questions.read_bytes()),
        "snapshot": current,
        "dataset_lock_sha256": sha((args.dataset / "lock.json").read_bytes()),
        "num_ctx": 32768,
        "knowledge": "unavailable_reference_routing_only",
    }
    (args.output / "run.json").write_bytes(encoded(metadata))
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
            raise ValueError("Unexpected loaded context")
        metadata["loaded_model"] = loaded
    for q in cases:
        item = users[q["source_user_id"]]
        transport.turns = []
        response = await assistant.ask(
            AskRequest(
                question=q["question"],
                timezone=item["timezone"],
                today=date.fromisoformat(item["today"]),
            ),
            UUID(item["api_user_id"]),
        )
        (args.output / f"{q['id']}.json").write_bytes(
            encoded(
                {
                    "probe": q,
                    "today": item["today"],
                    "timezone": item["timezone"],
                    "response": response.model_dump(mode="json"),
                    "turns": transport.turns,
                }
            )
        )
        print(
            f"{q['id']}: {response.status}, {response.explanation_status}, "
            f"tools={[c.name for c in response.tool_calls]}",
            flush=True,
        )
    preflight(baseline, lock, factory)
    with httpx.Client(base_url="http://127.0.0.1:11434", timeout=60, trust_env=False) as client:
        verify_artifacts(args.dataset, snapshot(client))
    metadata["status"] = "completed"
    (args.output / "run.json").write_bytes(encoded(metadata))
    engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        asyncio.run(run(parser.parse_args()))
    except Exception as exc:
        raise SystemExit(f"Probe stopped: {type(exc).__name__}; inspect local artifacts.") from None
