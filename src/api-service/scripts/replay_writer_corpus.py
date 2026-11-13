"""Rebuild legacy writer evidence with real SQL tools before a fresh model completion.

Daily histories are reconstructed synthetic fixtures uploaded through ingestion.
Reconciliation uses imported v0.2 accounts and the real predictor, not old quantiles.
This bypasses routing and claim selection and MUST NOT be scored as end-to-end QA.
Opening the former reserved questions for preference review retires their holdout use.
"""

import argparse
import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import httpx

from healthops.assistant.cards import build_cards
from healthops.assistant.narrative import messages, packet, validate
from healthops.cli import issue_token
from healthops.config import Settings
from healthops.main import create_app
from healthops.models import User
from healthops.schemas import ObservationIn
from healthops.synthetic_import import NAMESPACE, digest
from scripts.audit_writer_evidence import daily_observations, differing_fields
from scripts.eval_writer_chain import ROOT, guard_database, load_accounts, write


def load_legacy(*, open_holdout=False):
    if not open_holdout:
        raise ValueError("Explicit authorization to retire the former holdout is required")
    folder = ROOT / "eval/writer-corpus"
    names = ("development", "reserved_test", "known_failures")
    paths = [folder / f"{n}.synthetic.jsonl" for n in names]
    rows = [json.loads(line) for p in paths for line in p.read_text().splitlines()]
    if len(rows) != 79 or len({r["id"] for r in rows}) != 79:
        raise ValueError("Expected exactly 79 unique historical cases")
    if any(r["data_kind"] != "synthetic" for r in rows):
        raise ValueError("Only synthetic cases accepted")
    return rows, {p.name: digest(p) for p in paths}


def reconcile_scope(case):
    """Map historical questions to observed accounts, never force old winner labels."""
    family = case.get("family", "neither_reading")
    group = case.get("scenario_group", "")
    offset = int(group.rsplit("-", 1)[-1]) if group.rsplit("-", 1)[-1].isdigit() else 0
    source = "synthetic-00000" if family == "single_channel" else "synthetic-00001"
    end = date(2026, 8, 17) if source == "synthetic-00000" else date(2026, 8, 7)
    end -= timedelta(days=offset)
    if case["id"] == "known-neither_reading":
        end = date(2026, 1, 1)
    return source, {"metric": "steps", "date": str(end), "timezone": "America/New_York"}


def completion_input(case, cards, today):
    """Only production facts and original question/language, never reference answers."""
    facts = packet(cards, today)
    return facts, messages(case["question"], facts, case["language"])


async def run(args):
    guard_database(args.database_url)
    rows, hashes = load_legacy(open_holdout=args.open_holdout)
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / ".local/eval").resolve()):
        raise ValueError("Raw artifacts must stay under .local/eval")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    settings = Settings(
        _env_file=None,
        environment="local",
        data_mode="synthetic",
        database_url=args.database_url,
        assistant_model_backend="ollama",
        assistant_model_base_url=args.model_url,
        assistant_model_name="qwen3:8b",
        assistant_thinking=False,
        reconcile_model_dir=str(args.model_dir),
    )
    app = create_app(settings)
    factory = app.state.session_factory
    report, accounts = load_accounts(args.import_dir, factory)
    core = app.state.assistant
    await core.model.describe()
    meta = {
        "status": "running",
        "scope": "SQL_tool_grounded_writer_only_not_end_to_end",
        "former_reserved_status": "opened_for_review_no_longer_independent_holdout",
        "corpus_sha256": hashes,
        "model": core.model.info.model_dump(mode="json"),
        "runner_sha256": digest(Path(__file__)),
        "training_eligible": False,
        "data_version": report["version"],
        "manifest_sha256": report["manifest_sha256"],
        "predictor": json.loads((args.model_dir / "serving.json").read_text()),
        "source_sha256": {
            str(p.relative_to(ROOT)): digest(p) for p in sorted((ROOT / "healthops").rglob("*.py"))
        },
    }
    write(output / "run.json", meta)
    results = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test"
    ) as client:
        for case in rows:
            if all(c["kind"] == "daily_table" for c in case["cards"]):
                observations, metric, window = daily_observations(case)
                uid = uuid5(NAMESPACE, "writer-replay-1108:" + case["id"])
                with factory() as session:
                    session.add(User(id=uid, display_name="synthetic-writer-replay:" + case["id"]))
                    session.commit()
                    credential_path = output / f"credential-{case['id']}.json"
                    issue_token(session, uid, credential_path)
                token = json.loads(credential_path.read_text())["token"]
                fields = tuple(ObservationIn.model_fields)
                records = [
                    ObservationIn.model_validate({k: getattr(o, k) for k in fields}).model_dump(
                        mode="json"
                    )
                    for o in observations
                ]
                if records:
                    uploaded = await client.post(
                        "/v1/observations/batch",
                        headers={
                            "Authorization": "Bearer " + token,
                            "X-HealthOps-Data-Kind": "synthetic",
                        },
                        json={"observations": records},
                    )
                    uploaded.raise_for_status()
                    ingestion = uploaded.json()
                else:
                    ingestion = {"empty_fixture": True}
                write(
                    output / f"observations-{case['id']}.json",
                    {"records": records, "ingestion": ingestion},
                )
                first = case["cards"][0]
                tool = "daily_summary"
                arguments = {
                    "metric": metric.value,
                    "start_date": first["start_date"],
                    "end_date": first["end_date"],
                    "timezone": first["timezone"],
                    "provider": first["provider"] if len(case["cards"]) == 1 else None,
                }
                today = case["today"]
                provenance = "reconstructed_synthetic_fixture_uploaded_via_API_not_cohort"
                source = str(uid)
            else:
                source, arguments = reconcile_scope(case)
                uid = UUID(accounts[source]["user_id"])
                tool = "reconcile_devices"
                today = str(date.fromisoformat(arguments["date"]) + timedelta(days=1))
                provenance = "synthetic_v0.2_cohort_real_predictor_old_conditions_not_guaranteed"
            spec = core.toolbox.registry[tool]
            with factory() as session:
                result = spec.run(session, uid, spec.params.model_validate(arguments))
            evidence = [{"tool": tool, "result": result.model_dump(mode="json")}]
            cards = build_cards(evidence)
            facts, payload = completion_input(case, cards, today)
            turn = await core.model.complete(payload, [])
            answer, reason = validate(turn, facts)
            item = {
                "id": case["id"],
                "original_split": case["split"],
                "source": source,
                "provenance": provenance,
                "original_case": case,
                "tool_query": {"tool": tool, "arguments": arguments},
                "evidence": evidence,
                "cards": [c.model_dump(mode="json") for c in cards],
                "evidence_packet": facts,
                "input_messages": payload,
                "raw_model_output": turn.text,
                "raw_tool_calls": [vars(c) for c in turn.tool_calls],
                "prompt_tokens": turn.prompt_tokens,
                "completion_tokens": turn.completion_tokens,
                "basic_checks_passed": answer is not None,
                "reason": reason,
                "changed_card_fields": differing_fields(
                    case["cards"], [c.model_dump(mode="json") for c in cards]
                ),
                "semantic_review": "pending",
                "training_eligible": False,
            }
            results.append(item)
            write(output / f"{case['id']}.json", item)
            write(output / "results.json", results)
            print(
                json.dumps(
                    {
                        "completed": len(results),
                        "id": case["id"],
                        "basic_checks": reason or "accepted",
                    }
                ),
                flush=True,
            )
    meta["status"] = "completed"
    meta["completed"] = len(results)
    write(output / "run.json", meta)
    factory.kw["bind"].dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default=os.environ.get("HEALTHOPS_TEST_DATABASE_URL"), required=False
    )
    parser.add_argument("--import-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--open-holdout", action="store_true")
    asyncio.run(run(parser.parse_args()))
