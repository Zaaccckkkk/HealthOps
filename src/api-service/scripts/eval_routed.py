"""Evaluate real model routing and execution on existing synthetic cohort accounts.

Run from api-service. No seeding, migrations, provisioning or HTTP deployment is performed.
The local database must already contain the imported cohort and clarification migration.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid5

from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from healthops.assistant.cards import build_cards  # noqa: E402
from healthops.assistant.claims import verify_claim  # noqa: E402
from healthops.assistant.contracts import AskRequest  # noqa: E402
from healthops.assistant.conversations import ConversationStore  # noqa: E402
from healthops.assistant.guards import coverage, scope_error  # noqa: E402
from healthops.assistant.prompts import SYSTEM_PROMPT  # noqa: E402
from healthops.assistant.routed import RoutedAssistant  # noqa: E402
from healthops.assistant.routing import ROUTER_PROMPT, RequestSpec  # noqa: E402
from healthops.assistant.toolbox import build_registry  # noqa: E402
from healthops.config import Settings  # noqa: E402
from healthops.db import build_engine  # noqa: E402
from healthops.main import build_assistant  # noqa: E402
from healthops.models import User  # noqa: E402
from healthops.synthetic_import import NAMESPACE  # noqa: E402

ACCOUNTS = {"synthetic-00000", "synthetic-00003", "synthetic-00001"}
TOOLS = {
    "summary": "daily_summary",
    "comparison": "period_comparison",
    "availability": "availability",
    "reference": "lookup_reference",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def load_cases(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Empty or duplicate scenarios")
    for row in rows:
        if row["source_user_id"] not in ACCOUNTS:
            raise ValueError("Only the three synthetic cohort accounts are allowed")
        if not row["id"].replace("-", "").replace("_", "").isalnum():
            raise ValueError("Unsafe scenario ID")
        for turn in row["turns"]:
            if turn["expected"]["route"] == "ready":
                RequestSpec.model_validate(turn["expected"]["spec"])
    return rows


def identities(dataset, factory):
    """Prove synthetic ownership from the frozen import manifest, not an arbitrary UUID."""
    lock = json.loads((dataset / "lock.json").read_text())
    for name, expected in lock["artifacts_sha256"].items():
        path = (dataset / name).resolve()
        if not path.is_relative_to(dataset.resolve()) or digest(path.read_bytes()) != expected:
            raise ValueError("Frozen cohort artifacts changed")
    rows = [json.loads(x) for x in (dataset / "questions.jsonl").read_text().splitlines()]
    result = {}
    with factory() as session:
        for row in rows:
            source = row["source_user_id"]
            if source not in ACCOUNTS:
                raise ValueError("Unexpected source account")
            uid = uuid5(NAMESPACE, f"{lock['data_version']}:{lock['manifest_sha256']}:{source}")
            user = session.get(User, uid)
            if (
                str(uid) != row["api_user_id"]
                or user is None
                or user.display_name != f"Synthetic {row['cohort']} {str(uid)[:8]}"
            ):
                raise ValueError("Synthetic identity mismatch")
            result[source] = uid
            expected = row["expected"]
            if "tool_result" in expected:
                tool = build_registry()[expected["tool"]]
                params = tool.params.model_validate(
                    expected["arguments"] | {"timezone": row["timezone"]}
                )
                current = tool.run(session, uid, params).model_dump(mode="json")
                if current != expected["tool_result"]:
                    raise ValueError("Cohort data/tool output differs from frozen baseline")
    if set(result) != ACCOUNTS:
        raise ValueError("All three imported accounts are required")
    return result


def gold_queries(case, turn):
    """Arguments originate from author-written gold, never from model-selected scope."""
    spec = turn["expected"].get("spec")
    if not spec or spec["intent"] == "reference":
        return []
    tool = TOOLS[spec["intent"]]
    common = {"timezone": case["timezone"]}
    if tool != "availability":
        common["provider"] = case["provider"]
    dates = spec["window"]
    if tool == "period_comparison":
        common |= {
            "baseline_start": spec["baseline"]["start_date"],
            "baseline_end": spec["baseline"]["end_date"],
            "current_start": dates["start_date"],
            "current_end": dates["end_date"],
        }
    else:
        common |= dates
    metrics = spec["metrics"] if tool != "availability" else [None]
    return [(tool, common | ({"metric": m} if m else {})) for m in metrics]


def make_gold(case, turn, factory, uid):
    registry = build_registry()
    results = []
    with factory() as session:
        # The oracle only executes known read tools, scoped to a verified synthetic UUID.
        for name, args in gold_queries(case, turn):
            tool = registry[name]
            result = tool.run(session, uid, tool.params.model_validate(args)).model_dump(
                mode="json"
            )
            results.append({"tool": name, "arguments": args, "result": result})
    return results


def grade_turn(case, turn, response, gold):
    expected = turn["expected"]
    route = expected["route"]
    checks = {"route": response.routing_status == route}
    if route != "ready":
        checks["no_execution"] = not response.tool_calls and not response.evidence
        checks["status"] = response.status == (
            "needs_clarification" if route == "needs_clarification" else "refused"
        )
        if route == "needs_clarification":
            checks["continuation"] = bool(
                response.conversation_id and response.conversation_version
            )
            actual_missing = (response.request_spec or {}).get("unresolved_fields", [])
            checks["missing_fields"] = set(expected["missing_fields"]) <= set(actual_missing)
        else:
            checks["closed"] = response.conversation_id is None
    else:
        actual = response.request_spec or {}
        checks["request_spec"] = all(
            (
                set(actual.get(key, [])) == set(value)
                if key in {"metrics", "unresolved_fields"}
                else actual.get(key) == value
            )
            for key, value in expected["spec"].items()
        )
        spec = RequestSpec.model_validate(expected["spec"])
        calls = response.tool_calls
        checks["primary_tool"] = any(c.name == TOOLS[spec.intent] for c in calls)
        if case["grading_scope"] != "routing_only":
            executed = [c for c in calls if c.status == "ok"]
            checks["executed_scope"] = all(
                scope_error(
                    c.name, json.dumps(c.arguments), spec, case["timezone"], case["provider"]
                )
                is None
                for c in executed
            )
            checks["gold_evidence"] = bool(gold) and all(
                any(
                    e.get("tool") == g["tool"] and e.get("result") == g["result"]
                    for e in response.evidence
                )
                for g in gold
            )
            # Do not trust a response's self-reported complete flag or its narrowed RequestSpec.
            checks["cards_from_evidence"] = response.cards == build_cards(response.evidence)
            checked_claims = [
                verify_claim(c.claim, response.cards)
                for c in response.claims
                if c.claim is not None
            ]
            checks["claims_rechecked"] = (
                bool(checked_claims)
                and len(checked_claims) == len(response.claims)
                and all(c.valid for c in checked_claims)
                and response.answer == " ".join(c.sentence for c in checked_claims if c.sentence)
            )
            targets = coverage(spec, response.cards, calls, response.claims)
            checks["coverage"] = bool(targets) and all(t.reported for t in targets)
            checks["verified"] = (
                response.explanation_status == "verified"
                and bool(response.claims)
                and all(c.valid for c in response.claims)
                and response.completion_status == "complete"
                and response.status in {"answered", "insufficient_data"}
            )
            checks["verification_recorded"] = 1 <= len(response.verification_attempts) <= 2
            checks["final_verification_passed"] = bool(response.verification_attempts) and not (
                response.verification_attempts[-1].invalid_reasons
                or response.verification_attempts[-1].missing_targets
            )
    attempts = response.verification_attempts
    triggered = bool(attempts and (attempts[0].invalid_reasons or attempts[0].missing_targets))
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "first_tool": response.tool_calls[0].name if response.tool_calls else None,
        "first_verification_passed": (
            not attempts[0].invalid_reasons and not attempts[0].missing_targets
        )
        if attempts
        else None,
        "repair_triggered": triggered,
        "repair_recovered": (len(attempts) == 2 and all(checks.values())) if triggered else None,
        "raw_scope_rejections": sum(
            "scope_guard_rejected" in c.warnings for c in response.tool_calls
        ),
    }


def summarize(results, planned):
    reached = {
        "responses": 0,
        "ready": 0,
        "clarification": 0,
        "unsupported": 0,
        "model_error": 0,
        "tool_execution": 0,
        "verification": 0,
    }
    for result in results:
        for turn in result["turns"]:
            response = turn.get("response")
            if response is None:
                continue
            reached["responses"] += 1
            reached["ready"] += response.get("routing_status") == "ready"
            reached["clarification"] += response.get("routing_status") == "needs_clarification"
            reached["unsupported"] += response.get("routing_status") == "unsupported"
            reached["model_error"] += response.get("status") == "model_error"
            reached["tool_execution"] += any(
                call.get("status") == "ok" for call in response.get("tool_calls", [])
            )
            reached["verification"] += bool(response.get("verification_attempts"))
    metrics = {}
    for result in results:
        for turn in result["turns"]:
            for key, passed in turn.get("grade", {}).get("checks", {}).items():
                metric = metrics.setdefault(key, {"passed": 0, "total": 0})
                metric["total"] += 1
                metric["passed"] += int(passed)
    repair = [
        t["grade"]["repair_recovered"]
        for r in results
        for t in r["turns"]
        if t.get("grade", {}).get("repair_triggered")
    ]
    groups = {}
    for key in ("family", "source_user_id", "grading_scope"):
        groups[key] = {}
        for result in results:
            bucket = groups[key].setdefault(result[key], {"passed": 0, "total": 0})
            bucket["total"] += 1
            bucket["passed"] += int(result["passed"])
    first = [
        t["grade"]["first_verification_passed"]
        for r in results
        for t in r["turns"]
        if t.get("grade", {}).get("first_verification_passed") is not None
    ]
    return {
        "stage_reach": reached,
        "scenario_groups": groups,
        "first_verification": {"passed": sum(first), "total": len(first)},
        "planned_scenarios": planned,
        "finished_scenarios": len(results),
        "passed_scenarios": sum(r["passed"] for r in results),
        "blocked_turns": sum(r["blocked_turns"] for r in results),
        "stage_metrics_attempted_turns_only": metrics,
        "repair_recovery": {"passed": sum(repair), "total": len(repair)},
        "note": "Scenario success requires every planned turn. "
        "Stage metrics exclude blocked turns. Consult stage_reach before interpreting checks: "
        "empty cards and zero executed calls can satisfy structural checks without inference. "
        "Reference cases score routing only. No causal or held-out accuracy claim.",
    }


async def evaluate_case(case, uid, assistant, gold_by_turn):
    turns = []
    previous = None
    for index, turn in enumerate(case["turns"]):
        if index and (
            previous is None
            or previous.routing_status != "needs_clarification"
            or not previous.conversation_id
        ):
            break  # Never silently turn a failed continuation into a fresh question.
        request = AskRequest(
            question=turn["question"],
            timezone=case["timezone"],
            today=case["today"],
            provider=case["provider"],
            cancel=turn.get("cancel", False),
            conversation_id=previous.conversation_id if previous else None,
            conversation_version=previous.conversation_version if previous else None,
        )
        started = time.monotonic()
        try:
            response = await assistant.ask(request, uid)
        except Exception as exc:  # noqa: BLE001
            # Do not put URLs, credentials, SQL or arbitrary exception messages in reports.
            turns.append({"error_type": type(exc).__name__, "passed": False})
            break
        grade = grade_turn(case, turn, response, gold_by_turn[index])
        turns.append(
            {
                "grade": grade,
                "response": response.model_dump(mode="json"),
                "latency_s": round(time.monotonic() - started, 3),
            }
        )
        previous = response
    return {
        "id": case["id"],
        "family": case["family"],
        "source_user_id": case["source_user_id"],
        "grading_scope": case["grading_scope"],
        "turns": turns,
        "blocked_turns": len(case["turns"]) - len(turns),
        "passed": len(turns) == len(case["turns"])
        and all(t.get("grade", {}).get("passed", False) for t in turns),
    }


class RecordedModel:
    """Keep router proposals and execution calls in private per-scenario traces."""

    def __init__(self, model):
        self.model = model
        self.turns = []

    @property
    def info(self):
        return self.model.info

    async def complete(self, messages, tools):
        turn = await self.model.complete(messages, tools)
        self.turns.append(
            {
                "messages": copy.deepcopy(messages),
                "tool_names": [t["function"]["name"] for t in tools],
                "text": turn.text,
                "tool_calls": [
                    {"name": c.name, "arguments_json": c.arguments_json} for c in turn.tool_calls
                ],
                "prompt_tokens": turn.prompt_tokens,
                "completion_tokens": turn.completion_tokens,
            }
        )
        return turn


async def run(args):
    cases = load_cases(args.cases)
    if args.validate_only:
        print(f"Validated {len(cases)} scenarios / {sum(len(c['turns']) for c in cases)} turns")
        return
    if args.cohort_dataset is None:
        raise ValueError("--cohort-dataset is required for real-model evaluation")
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if settings.environment != "local" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("This runner requires a local database; no cloud database access")
    output = args.output or ROOT / ".local/eval" / datetime.now(UTC).strftime(
        "routed-%Y%m%dT%H%M%SZ"
    )
    if not output.resolve().is_relative_to((ROOT / ".local/eval").resolve()):
        raise ValueError("Raw output must stay under api-service/.local/eval")
    engine = build_engine(settings.database_url.get_secret_value())
    factory = sessionmaker(engine, expire_on_commit=False)
    users = identities(args.cohort_dataset, factory)
    core = build_assistant(
        settings.model_copy(update={"assistant_allow_today_override": True}), factory, None
    )
    if core is None:
        raise ValueError("Configure HEALTHOPS_ASSISTANT_MODEL_BASE_URL")
    if hasattr(core.model, "describe"):
        await core.model.describe()
    if args.limit:
        cases = cases[: args.limit]
    # Freeze deterministic gold before the model sees any questions. No private DB records
    # outside the three allowlisted identities are queried or written into reports.
    gold = {
        c["id"]: [make_gold(c, t, factory, users[c["source_user_id"]]) for t in c["turns"]]
        for c in cases
    }
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    metadata = {
        "status": "running",
        "execution": "local_in_process_real_model_and_postgresql",
        "cases_sha256": digest(args.cases.read_bytes()),
        "cohort_lock_sha256": digest((args.cohort_dataset / "lock.json").read_bytes()),
        "router_prompt_sha256": digest(ROUTER_PROMPT.encode()),
        "execution_prompt_sha256": digest(SYSTEM_PROMPT.encode()),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "limits": {
            key: getattr(settings, key)
            for key in (
                "assistant_model_name",
                "assistant_model_backend",
                "assistant_temperature",
                "assistant_thinking",
                "assistant_max_tool_calls",
                "assistant_max_model_turns",
                "assistant_deadline_seconds",
                "assistant_max_output_tokens",
            )
        },
        "model": core.model.info.model_dump(mode="json"),
        "reference_grading": "routing_only; retrieved relevance requires separate manual review",
    }
    write(output / "run.json", metadata)
    write(output / "gold.json", gold)
    results = []
    recorder = RecordedModel(core.model)
    core.model = recorder
    assistant = RoutedAssistant(core, ConversationStore(factory))
    try:
        for case in cases:
            recorder.turns = []
            result = await evaluate_case(
                case, users[case["source_user_id"]], assistant, gold[case["id"]]
            )
            result["model_turns"] = recorder.turns
            results.append(result)
            write(output / f"{case['id']}.json", result)
            write(output / "summary.json", summarize(results, len(cases)))
            print(f"{case['id']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
        metadata["status"] = "completed"
    finally:
        write(output / "run.json", metadata)
        engine.dispose()
    print(f"Private reports: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "eval/assistant_routed.synthetic.jsonl"
    )
    parser.add_argument("--cohort-dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--validate-only", action="store_true")
    asyncio.run(run(parser.parse_args()))
