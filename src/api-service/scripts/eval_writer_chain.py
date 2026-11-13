"""Capture real writer inputs through the authenticated API using isolated synthetic users.

HTTP uses ASGI in-process; routing, PostgreSQL tools, clarification persistence,
the pinned CPU predictor and remote chat model are real. No production endpoint,
personal database or handwritten evidence cards are used. This is not a trainer.
"""

import argparse
import asyncio
import copy
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from eval.writer_chain_cases import (
    ACCOUNTS,
    EDGE_ACCOUNT,
    cases,
    edge_diagnostics,
    edge_observations,
)
from healthops.assistant.cards import build_cards
from healthops.assistant.contracts import AskResponse
from healthops.assistant.evidence_facts import enriched_packet
from healthops.assistant.narrative import SYSTEM_V5, SYSTEM_V6, is_writer_system, packet
from healthops.assistant.presentation import presentation_packet
from healthops.cli import issue_token
from healthops.config import Settings
from healthops.main import create_app
from healthops.models import Observation, User
from healthops.synthetic_import import NAMESPACE, digest

ROOT = Path(__file__).resolve().parents[1]
EDGE_ID = uuid5(NAMESPACE, EDGE_ACCOUNT)


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n")
    path.chmod(0o600)


def guard_database(url):
    parsed = make_url(url)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.database != "healthops_test"
        or parsed.host not in {"127.0.0.1", "localhost", "::1"}
        or parsed.query
    ):
        raise ValueError("Only an isolated loopback healthops_test database is accepted")


class CaptureModel:
    """Observe the actual model interface without substituting inputs or responses."""

    def __init__(self, model):
        self.model = model
        self.calls = []

    @property
    def info(self):
        return self.model.info

    async def complete(self, messages, tools):
        item = {"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)}
        self.calls.append(item)
        try:
            turn = await self.model.complete(messages, tools)
            item.update(
                raw_text=turn.text,
                tool_calls=[vars(c) for c in turn.tool_calls],
                prompt_tokens=turn.prompt_tokens,
                completion_tokens=turn.completion_tokens,
            )
            return turn
        except (Exception, asyncio.CancelledError) as exc:
            item["error_type"] = type(exc).__name__
            raise


def writer_calls(calls):
    return [c for c in calls if c["messages"] and is_writer_system(c["messages"][0])]


def audit(response, calls, gold):
    """Check provenance/known scope, not clinical truth or prose semantics."""
    cards = build_cards(response.evidence)
    checks = {
        "ready": response.routing_status == "ready",
        "cards_from_tool_results": response.cards == cards,
        "expected_tool_result": expected_results_present(response.evidence, gold),
    }
    writers = writer_calls(calls)
    checks["writer_calls_bounded"] = 1 <= len(writers) <= 2
    checks["writer_input_matches_cards"] = False
    checks["writer_dialogue_matches_scope"] = False
    checks["writer_repair_matches_contract"] = len(writers) == 1
    if 1 <= len(writers) <= 2:
        from healthops.assistant.model import ModelTurn, ToolCallRequest
        from healthops.assistant.narrative import check_draft, editor_messages

        facts = packet(cards, str(response.time_semantics.today))
        supplied = [json.loads(w["messages"][1]["content"]) for w in writers]

        # Reconstruct the exact versioned input, including Python-derived fields.
        # Old captures retain their original contract; never strip fields to make
        # a mismatched or tampered evidence packet pass provenance checks.
        def expected_input(call, payload):
            system = call["messages"][0]["content"]
            if system == SYSTEM_V6:
                return enriched_packet(facts, payload["language"])
            if system == SYSTEM_V5:
                return presentation_packet(facts, payload["language"])
            return facts

        checks["writer_input_matches_cards"] = all(
            payload["evidence"] == expected_input(call, payload)
            for call, payload in zip(writers, supplied, strict=True)
        )
        checks["writer_dialogue_matches_scope"] = all(
            json.loads(p["user_request"])["confirmed_request"] == response.request_spec
            for p in supplied
        )
        if len(writers) == 2:
            first = ModelTurn(
                text=writers[0].get("raw_text"),
                tool_calls=[ToolCallRequest(**c) for c in writers[0].get("tool_calls", [])],
            )
            _, issues = check_draft(first, facts)
            checks["writer_repair_matches_contract"] = bool(issues) and writers[1][
                "messages"
            ] == editor_messages(writers[0]["messages"], first, issues)
    return checks


def expected_results_present(evidence, gold):
    """Require every requested metric/source query, not merely one successful tool."""
    queries = gold if isinstance(gold, list) else [gold]
    return bool(queries) and all(
        any(e.get("tool") == q["tool"] and e.get("result") == q["result"] for e in evidence)
        for q in queries
    )


def load_accounts(folder, factory, *, required_accounts=None, allow_edge=True):
    required_accounts = set(ACCOUNTS if required_accounts is None else required_accounts)
    report = json.loads((folder / "report.json").read_text())
    manifest = ROOT.parents[1] / "data/manifests/synthetic-v0.2.yaml"
    if (
        report["status"] != "completed"
        or report["version"] != "synthetic-v0.2"
        or report["manifest_sha256"] != digest(manifest)
        or {u["user_id"] for u in report["users"]} != required_accounts
        or len(report["users"]) != len(required_accounts)
    ):
        raise ValueError("Manifest-verified exact-account v0.2 import required")
    accounts = {}
    with factory() as session:
        allowed = {EDGE_ID} if allow_edge else set()
        for user in report["users"]:
            uid = uuid5(
                NAMESPACE, f"{report['version']}:{report['manifest_sha256']}:{user['user_id']}"
            )
            allowed.add(uid)
            stored = session.get(User, uid)
            count = session.scalar(
                select(func.count()).select_from(Observation).where(Observation.user_id == uid)
            )
            if (
                str(uid) != user["api_user_id"]
                or stored is None
                or stored.display_name != user["display_name"]
                or count != user["records"]
            ):
                raise ValueError("Imported identity or record-count mismatch")
            credential = json.loads((folder / f"{uid}.json").read_text())
            if credential["user_id"] != str(uid):
                raise ValueError("Credential identity mismatch")
            accounts[user["user_id"]] = credential
        if not set(session.scalars(select(User.id))).issubset(allowed):
            raise ValueError("Database contains an account outside this synthetic run")
    return report, accounts


async def run(args):
    guard_database(args.database_url)
    duration_holdout = getattr(args, "duration_holdout", False)
    holdout = getattr(args, "holdout", False) or duration_holdout
    multibudget = getattr(args, "multibudget", False)
    regression = getattr(args, "pretraining", False) or multibudget
    required_accounts = ACCOUNTS
    if duration_holdout:
        from eval.writer_duration_cases import cases as duration_cases
        from eval.writer_holdout_cases import ACCOUNTS as duration_import_accounts

        if args.limit:
            raise ValueError("Run the duration holdout as a whole")
        required_accounts = duration_import_accounts
        rows = duration_cases()
    elif holdout:
        from eval.writer_holdout_cases import ACCOUNTS as holdout_accounts
        from eval.writer_holdout_cases import frozen_cases as holdout_cases

        if args.limit:
            raise ValueError("Run the frozen holdout as a whole; use development for smoke tests")
        required_accounts = holdout_accounts
        rows = holdout_cases()
    elif regression:
        from eval.pretraining_cases import cases as pretraining_cases

        if multibudget:
            from eval.pretraining_cases import multibudget_cases

            rows = multibudget_cases()
        else:
            rows = pretraining_cases()
    elif args.multimetric:
        from eval.writer_multimetric_cases import cases as multimetric_cases

        rows = multimetric_cases()
    else:
        rows = edge_diagnostics() if args.edge_diagnostics else cases()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / ".local/eval").resolve()):
        raise ValueError("Raw traces must stay under api-service/.local/eval")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    settings = Settings(
        _env_file=None,
        environment="local",
        data_mode="synthetic",
        database_url=args.database_url,
        assistant_model_base_url=args.model_url,
        assistant_model_backend="ollama",
        assistant_model_name=getattr(args, "model_name", "qwen3:8b"),
        assistant_thinking=False,
        assistant_narrative_enabled=True,
        assistant_allow_today_override=True,
        assistant_rate_limit_per_minute=120,
        reconcile_model_dir=str(args.model_dir),
        google_oauth_client_id=None,
        google_oauth_client_secret=None,
        google_oauth_redirect_uri=None,
        connection_key=None,
    )
    app = create_app(settings)
    factory = app.state.session_factory
    report, accounts = load_accounts(
        args.import_dir, factory, required_accounts=required_accounts, allow_edge=not holdout
    )
    if not holdout:
        with factory() as session:
            user = session.get(User, EDGE_ID)
            if user and user.display_name != EDGE_ACCOUNT:
                raise ValueError("Edge-account collision")
            if user is None:
                session.add(User(id=EDGE_ID, display_name=EDGE_ACCOUNT))
                session.commit()
            issue_token(session, EDGE_ID, output / "edge-credential.json")
        accounts[EDGE_ACCOUNT] = json.loads((output / "edge-credential.json").read_text())
    core = app.state.assistant
    await core.model.describe()
    writer_url = getattr(args, "writer_url", None)
    writer_name = getattr(args, "writer_model", None)
    writer_identity = getattr(args, "writer_identity", None)
    if any((writer_url, writer_name, writer_identity)):
        if not all((writer_url, writer_name, writer_identity)):
            raise ValueError(
                "Writer URL, served model name and immutable identity are required together"
            )
        from scripts.eval_writer_checkpoint import WriterDispatchModel, writer_model

        core.model = WriterDispatchModel(core.model, writer_model(writer_url, writer_name))
    recorder = CaptureModel(core.model)
    core.model = recorder
    if args.limit:
        rows = rows[: args.limit]
    write(output / "cases.json", rows)
    metadata = {
        "status": "running",
        "execution": "authenticated_ASGI_API_real_PostgreSQL_and_models",
        "suite": (
            "heldout_duration_units_6"
            if duration_holdout
            else "heldout_new_accounts_20"
            if holdout
            else "development_multibudget_6"
            if multibudget
            else "development_pretraining_30"
            if regression
            else "development_multimetric_18"
            if args.multimetric
            else "supplemental_explicit_edge_probes"
            if args.edge_diagnostics
            else "development_50"
        ),
        "http_transport": "in_process_ASGI_not_Cloud_Run_or_phone",
        "data_version": report["version"],
        "manifest_sha256": report["manifest_sha256"],
        "model": core.model.info.model_dump(mode="json"),
        "writer_override": (
            {
                "name": writer_name,
                "checkpoint_identity": writer_identity,
                "enable_thinking": False,
                "max_tokens": 800,
                "temperature": 0.0,
            }
            if writer_url
            else None
        ),
        "predictor": json.loads((args.model_dir / "serving.json").read_text()),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "runner_sha256": digest(Path(__file__)),
        "source_sha256": {
            str(p.relative_to(ROOT)): digest(p) for p in sorted((ROOT / "healthops").rglob("*.py"))
        },
        "cases_sha256": digest(output / "cases.json"),
        "inference_settings": {
            k: getattr(settings, k)
            for k in (
                "assistant_max_model_turns",
                "assistant_max_tool_calls",
                "assistant_model_context_budget_chars",
                "assistant_max_output_tokens",
                "assistant_deadline_seconds",
                "assistant_temperature",
                "assistant_thinking",
                "assistant_narrative_enabled",
                "assistant_allow_today_override",
                "assistant_rate_limit_per_minute",
            )
        },
    }
    write(output / "run.json", metadata)
    results = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test"
    ) as client:
        if not holdout:
            headers = {
                "Authorization": "Bearer " + accounts[EDGE_ACCOUNT]["token"],
                "X-HealthOps-Data-Kind": "synthetic",
            }
            upload = await client.post(
                "/v1/observations/batch",
                headers=headers,
                json={"observations": edge_observations()},
            )
            upload.raise_for_status()
            write(
                output / "edge-upload.json",
                {"response": upload.json(), "observations": edge_observations()},
            )
        for case in rows:
            credential = accounts[case["source_user_id"]]
            queries = (
                case["expected_queries"]
                if "expected_queries" in case
                else [{"tool": case["expected_tool"], "arguments": case["expected_arguments"]}]
            )
            golds = []
            for query in queries:
                spec = core.toolbox.registry[query["tool"]]
                with factory() as session:
                    gold_result = spec.run(
                        session,
                        UUID(credential["user_id"]),
                        spec.params.model_validate(query["arguments"]),
                    )
                golds.append(query | {"result": gold_result.model_dump(mode="json")})
            gold = golds if "expected_queries" in case else golds[0]
            turns, continuation = [], {}
            for index, question in enumerate(case["questions"]):
                recorder.calls = []
                request = {
                    "question": question,
                    "timezone": case["timezone"],
                    "provider": case["provider"],
                    "today": case["today"],
                    **continuation,
                }
                started = time.monotonic()
                response = await client.post(
                    "/v1/assistant/ask",
                    json=request,
                    headers={"Authorization": "Bearer " + credential["token"]},
                )
                item = {
                    "request": request,
                    "http_status": response.status_code,
                    "response": response.json(),
                    "model_calls": copy.deepcopy(recorder.calls),
                    "seconds": round(time.monotonic() - started, 3),
                }
                turns.append(item)
                if response.status_code != 200:
                    break
                body = AskResponse.model_validate(response.json())
                if regression:
                    from eval.pretraining_cases import stage_checks

                    item["stage_checks"] = stage_checks(
                        body, case["expected_routes"][index], recorder.calls
                    )
                if index < len(case["questions"]) - 1:
                    if not body.conversation_id or body.routing_status != "needs_clarification":
                        item["blocked_reason"] = "expected_clarification_not_produced"
                        break
                    continuation = {
                        "conversation_id": str(body.conversation_id),
                        "conversation_version": body.conversation_version,
                    }
                else:
                    if queries:
                        item["evidence_checks"] = audit(body, recorder.calls, gold)
                    item["writer_review_status"] = (
                        "pending_semantic_review" if writer_calls(recorder.calls) else "not_reached"
                    )
            result = {
                "id": case["id"],
                "case": case,
                "gold_tool_query": gold,
                "turns": turns,
                "training_eligible": False,
            }
            results.append(result)
            write(output / f"{case['id']}.json", result)
            checks = turns[-1].get("evidence_checks", {})
            print(
                json.dumps(
                    {
                        "id": case["id"],
                        "writer_called": bool(writer_calls(turns[-1]["model_calls"])),
                        "evidence_ok": bool(checks) and all(checks.values()),
                    }
                ),
                flush=True,
            )
            write(
                output / "summary.json",
                {
                    "completed": len(results),
                    "planned": len(rows),
                    "evidence_ready_for_review": sum(
                        bool(r["turns"][-1].get("evidence_checks"))
                        and all(r["turns"][-1]["evidence_checks"].values())
                        for r in results
                    ),
                },
            )
    metadata["status"] = "completed"
    write(output / "run.json", metadata)
    app.state.session_factory.kw["bind"].dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("HEALTHOPS_TEST_DATABASE_URL"))
    parser.add_argument("--import-dir", type=Path, required=True)
    parser.add_argument("--model-url", required=True)
    parser.add_argument("--model-name", default="qwen3:8b")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--writer-url", help="Optional separate OpenAI-compatible final writer")
    parser.add_argument("--writer-model", help="Served writer model/adapter name")
    parser.add_argument("--writer-identity", help="Immutable writer checkpoint or adapter hash")
    suites = parser.add_mutually_exclusive_group()
    suites.add_argument("--edge-diagnostics", action="store_true")
    suites.add_argument("--multimetric", action="store_true")
    suites.add_argument(
        "--duration-holdout", action="store_true", help="Six supplemental bilingual duration cases"
    )
    suites.add_argument("--holdout", action="store_true", help="Frozen 20 new-account test cases")
    suites.add_argument(
        "--pretraining", action="store_true", help="New development stage regressions"
    )
    suites.add_argument("--multibudget", action="store_true", help="Three/five-metric regressions")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".local/eval" / datetime.now(UTC).strftime("writer-chain-%Y%m%dT%H%M%SZ"),
    )
    parser.add_argument("--limit", type=int)
    arguments = parser.parse_args()
    if not arguments.database_url:
        parser.error("Explicit isolated test database URL is required")
    asyncio.run(run(arguments))
