"""Freeze and evaluate 14 reconciliation scenarios per v0.2 synthetic account.

Only healthops_test or the explicit synthetic Cloud SQL tunnel is allowed. Raw outputs and
frozen gold stay under .local/eval. This is tool-grounding evaluation, not clinical accuracy.
"""

import argparse
import asyncio
import hashlib
import json
import platform
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from healthops.assistant.cards import build_cards, format_value  # noqa: E402
from healthops.assistant.claims import verify_claim  # noqa: E402
from healthops.assistant.contracts import AskRequest  # noqa: E402
from healthops.assistant.conversations import ConversationStore  # noqa: E402
from healthops.assistant.guards import coverage, scope_error  # noqa: E402
from healthops.assistant.routed import RoutedAssistant  # noqa: E402
from healthops.assistant.routing import RequestSpec  # noqa: E402
from healthops.config import Settings  # noqa: E402
from healthops.db import build_engine  # noqa: E402
from healthops.main import build_assistant  # noqa: E402
from healthops.models import User  # noqa: E402
from healthops.reconciliation.model import TransformerPredictor  # noqa: E402
from healthops.synthetic_import import NAMESPACE  # noqa: E402
from healthops.tools.contracts import ReconcileParams  # noqa: E402
from healthops.tools.reconcile import RED_FLAG, reconcile_tool  # noqa: E402

# Author-selected source cohort dates, checked against synthetic-v0.2. No personal accounts.
ACCOUNTS = {
    "synthetic-00000": ("2026-06-30", "2026-08-17"),
    "synthetic-00001": ("2026-05-14", "2026-08-07"),
    "synthetic-00003": ("2026-07-31", "2026-09-12"),
}
COLUMNS = (
    "red_flag_when_insufficient",
    "no_number_when_no_reading",
    "interval_stated",
    "attribution_matches_card",
)


def cases_for(source):
    start, target = map(date.fromisoformat, ACCOUNTS[source])
    short = start + timedelta(days=2)
    sparse = start + timedelta(days=3)
    absent = start - timedelta(days=1)
    templates = [
        (
            "quoted_discrepancy",
            "Apple Watch says 6 hours and Fitbit says 8 hours of sleep duration "
            f"on {target}. Which is closer?",
            "sleep_duration",
            target,
        ),
        (
            "sleep_estimate",
            f"Estimate my sleep duration across devices on {target}.",
            "sleep_duration",
            target,
        ),
        ("missing_metric", "Which device is more accurate?", None, None),
        ("yesterday_steps", "Reconcile my steps yesterday.", "steps", target),
        (
            "one_channel_question",
            f"If only one channel recorded steps on {target}, what can you estimate? "
            "Reconcile the records.",
            "steps",
            target,
        ),
        ("no_data", f"Reconcile my steps on {absent}.", "steps", absent),
        (
            "three_day_history",
            f"Reconcile my steps on {short} and explain any limitations.",
            "steps",
            short,
        ),
        (
            "sparse_window",
            f"Reconcile my steps on {sparse}; is the history sufficient?",
            "steps",
            sparse,
        ),
        (
            "which_device",
            f"For steps on {target}, which wearable is closer to the model estimate?",
            "steps",
            target,
        ),
        ("whole_month", "Reconcile my steps over the past 30 days.", "unsupported", None),
        (
            "health_judgment",
            "My resting heart rate differs between devices. Is it dangerous?",
            "unsupported",
            None,
        ),
        ("unsupported_hrv", "Reconcile HRV SDNN yesterday.", "unsupported", None),
        (
            "outside_history",
            "Reconcile sleep duration on 2026-01-01.",
            "sleep_duration",
            date(2026, 1, 1),
        ),
        ("clarify_then_resume", f"Which device is closer on {target}?", None, target),
    ]
    rows = []
    for family, question, metric, day in templates:
        expected = {
            "route": "ready",
            "tool": "reconcile_devices",
            "metric": metric,
            "date": str(day),
        }
        if metric is None:
            expected = {"route": "needs_clarification", "missing_fields": ["metrics"]}
            if day is None:
                expected["missing_fields"].append("time_range")
        elif metric == "unsupported":
            expected = {"route": "unsupported", "tool": None}
        turns = [{"question": question, "expected": expected}]
        if family == "clarify_then_resume":
            turns.append(
                {
                    "question": "steps",
                    "expected": {
                        "route": "ready",
                        "tool": "reconcile_devices",
                        "metric": "steps",
                        "date": str(day),
                    },
                }
            )
        rows.append(
            {
                "id": f"reconcile-v02-{source}-{family}",
                "family": family,
                "source_user_id": source,
                "timezone": "America/New_York",
                "today": str(target + timedelta(days=1)),
                "turns": turns,
            }
        )
    return rows


def spec_for(expected):
    return RequestSpec(
        intent="device_reconciliation",
        metrics=[expected["metric"]],
        window={"start_date": expected["date"], "end_date": expected["date"]},
        baseline=None,
        language="en",
        unresolved_fields=[],
    )


def grade(expected, response, timezone):
    checks = {"route": response.routing_status == expected["route"]}
    rubric = dict.fromkeys(COLUMNS, "n_a")
    if expected["route"] != "ready":
        checks["no_execution"] = not response.tool_calls and not response.evidence
        checks["status"] = response.status == (
            "refused" if expected["route"] == "unsupported" else "needs_clarification"
        )
        if expected["route"] == "needs_clarification":
            checks["continuation"] = bool(
                response.conversation_id and response.conversation_version
            )
            checks["missing_fields"] = set(expected["missing_fields"]) <= set(
                (response.request_spec or {}).get("unresolved_fields", [])
            )
    else:
        spec = spec_for(expected)
        checks["primary_tool"] = any(
            c.name == "reconcile_devices" and c.status == "ok" for c in response.tool_calls
        )
        checks["scope"] = all(
            scope_error(c.name, json.dumps(c.arguments), spec, timezone, None) is None
            for c in response.tool_calls
            if c.status == "ok"
        )
        gold = expected["result"]
        results = [e["result"] for e in response.evidence if e.get("tool") == "reconcile_devices"]

        def matches(r):
            numeric = ("q05", "q50", "q95")
            return (
                all(
                    (r.get(k) is None and gold[k] is None)
                    or (
                        r.get(k) is not None
                        and gold[k] is not None
                        and abs(Decimal(str(r[k])) - Decimal(str(gold[k]))) <= Decimal("0.01")
                    )
                    for k in numeric
                )
                and all(
                    r.get(k) == gold[k]
                    for k in (
                        "date",
                        "metric",
                        "unit",
                        "warnings",
                        "attribution",
                        "artifact_sha256",
                        "window_days",
                        "history_days",
                        "window_days_with_any",
                        "window_days_with_both",
                    )
                )
                and [p["value"] for p in r["providers"]] == [p["value"] for p in gold["providers"]]
            )

        checks["gold_evidence"] = bool(results) and all(matches(r) for r in results)
        checks["cards_from_evidence"] = response.cards == build_cards(response.evidence)
        verified = [verify_claim(c.claim, response.cards) for c in response.claims if c.claim]
        checks["claims_rechecked"] = (
            bool(verified)
            and len(verified) == len(response.claims)
            and all(c.valid for c in verified)
            and response.answer
            == " ".join(dict.fromkeys(c.sentence for c in verified if c.sentence))
        )
        checks["coverage"] = all(
            c.reported for c in coverage(spec, response.cards, response.tool_calls, verified)
        )
        checks["verified"] = (
            response.explanation_status == "verified" and response.completion_status == "complete"
        )
        if {"history_short", "sparse_window", "single_device", "wide_interval"} & set(
            gold["warnings"]
        ):
            rubric["red_flag_when_insufficient"] = "yes" if RED_FLAG in response.answer else "no"
        if gold["q50"] is None:
            rubric["no_number_when_no_reading"] = (
                "yes"
                if (
                    checks["claims_rechecked"]
                    and results
                    and all(
                        r["q50"] is None and r["q05"] is None and r["q95"] is None for r in results
                    )
                )
                else "no"
            )
        else:
            # Check displayed interval endpoints against the returned, gold-matched card.
            r = results[0] if results else gold
            rubric["interval_stated"] = (
                "yes"
                if (
                    checks["gold_evidence"]
                    and "nominal 90% prediction interval" in response.answer
                    and all(
                        format_value(r[k], r["unit"]) in response.answer for k in ("q05", "q95")
                    )
                )
                else "no"
            )
        rubric["attribution_matches_card"] = (
            "yes" if checks["gold_evidence"] and checks["claims_rechecked"] else "no"
        )
    return {
        "checks": checks,
        "rubric": rubric,
        "passed": all(checks.values()) and "no" not in rubric.values(),
    }


def validate_report(report, session):
    if report["version"] != "synthetic-v0.2" or report["status"] != "completed":
        raise ValueError("A completed synthetic-v0.2 import report is required")
    manifest = ROOT.parents[1] / "data/manifests/synthetic-v0.2.yaml"
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if report["manifest_sha256"] != digest:
        raise ValueError("Synthetic manifest differs")
    users = {}
    for row in report["users"]:
        if row["user_id"] not in ACCOUNTS:
            raise ValueError("Unexpected synthetic account")
        uid = uuid5(NAMESPACE, f"synthetic-v0.2:{digest}:{row['user_id']}")
        user = session.get(User, uid)
        if (
            str(uid) != row["api_user_id"]
            or user is None
            or user.display_name != row["display_name"]
        ):
            raise ValueError("Synthetic identity mismatch")
        users[row["user_id"]] = uid
    if set(users) != set(ACCOUNTS):
        raise ValueError("All three synthetic accounts are required")
    return users


async def run(args):
    url = args.database_url_file.read_text().strip()
    parsed = make_url(url)
    if not (parsed.host in {"127.0.0.1", "localhost"} and parsed.database == "healthops_test"):
        sys.path.insert(0, str(ROOT.parents[1] / "src/deployment/cloud-run"))
        from seed_synthetic import validate_target

        validate_target(url, args.api_url or "")
    if not args.output.resolve().is_relative_to((ROOT / ".local/eval").resolve()):
        raise ValueError("Output must remain under api-service/.local/eval")
    args.output.mkdir(parents=True, exist_ok=True)
    source_paths = [
        *sorted((ROOT / "healthops/assistant").glob("*.py")),
        ROOT / "healthops/tools/reconcile.py",
        ROOT / "healthops/reconciliation/model.py",
        Path(__file__).resolve(),
    ]
    (args.output / "source-environment.json").write_text(
        json.dumps(
            {
                "started_at": datetime.now(UTC).isoformat(),
                "python": platform.python_version(),
                "platform": platform.platform(),
                "mode": "http" if args.api_url else "local_real_model",
                "sha256": {
                    str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in source_paths
                },
            },
            indent=2,
        )
        + "\n"
    )
    report = json.loads(args.import_report.read_text())
    engine = build_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False)
    predictor = TransformerPredictor(str(args.model_dir))
    with factory() as session:
        users = validate_report(report, session)
        cases = [row for uid in ACCOUNTS for row in cases_for(uid)]
        for case in cases:
            for turn in case["turns"]:
                e = turn["expected"]
                if e["route"] == "ready":
                    e["result"] = reconcile_tool(
                        session,
                        users[case["source_user_id"]],
                        ReconcileParams(
                            metric=e["metric"], date=e["date"], timezone=case["timezone"]
                        ),
                        predictor,
                    ).model_dump(mode="json")
                    e["warnings"] = e["result"]["warnings"]
    (args.output / "gold.json").write_text(json.dumps(cases, indent=2) + "\n")
    if args.freeze_only:
        print(
            json.dumps(
                {
                    "scenarios": len(cases),
                    "turns": sum(len(c["turns"]) for c in cases),
                    "model": predictor.version,
                    "status": "gold_frozen_not_evaluated",
                }
            )
        )
        engine.dispose()
        return
    import httpx

    async with httpx.AsyncClient(timeout=180) as client:
        if args.api_url:
            import subprocess

            sys.path.insert(0, str(ROOT.parents[1] / "src/deployment/cloud-run"))
            from evaluate_routed_http import HTTPAssistant, credential_headers

            iam = subprocess.check_output(
                ["gcloud", "auth", "print-identity-token"], text=True
            ).strip()
            assistant = HTTPAssistant(
                client, args.api_url, credential_headers(args.import_report.parent, users, iam)
            )
        else:
            settings = Settings(
                database_url=url,
                assistant_model_base_url=args.model_url,
                assistant_allow_today_override=True,
                assistant_thinking=False,
            )
            core = build_assistant(settings, factory, None, predictor)
            if core is None:
                raise ValueError("A model URL is required for real evaluation")
            assistant = RoutedAssistant(core, ConversationStore(factory))
        records = []
        for case in cases:
            previous = None
            turns = []
            for turn in case["turns"]:
                request = AskRequest(
                    question=turn["question"],
                    timezone=case["timezone"],
                    today=case["today"],
                    conversation_id=previous.conversation_id if previous else None,
                    conversation_version=previous.conversation_version if previous else None,
                )
                try:
                    response = await assistant.ask(request, users[case["source_user_id"]])
                    graded = grade(turn["expected"], response, case["timezone"])
                    turns.append(graded | {"response": response.model_dump(mode="json")})
                    previous = response
                except Exception as exc:
                    turns.append({"passed": False, "error_type": type(exc).__name__})
                    break
                if (
                    turn["expected"]["route"] == "needs_clarification"
                    and not response.conversation_id
                ):
                    break
            passed = len(turns) == len(case["turns"]) and all(t["passed"] for t in turns)
            records.append({"id": case["id"], "passed": passed, "turns": turns})
            (args.output / "results.json").write_text(json.dumps(records, indent=2) + "\n")
            print(json.dumps({"id": case["id"], "passed": passed}), flush=True)
        summary = {
            "scenarios": len(records),
            "passed": sum(r["passed"] for r in records),
            "turns": sum(len(r["turns"]) for r in records),
            "model_version": predictor.version,
        }
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary))
    engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-file", required=True, type=Path)
    parser.add_argument("--import-report", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-url")
    parser.add_argument("--api-url")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
