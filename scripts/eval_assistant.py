"""Evaluate the assistant against a real model endpoint with synthetic data only.

Steps: ensure the synthetic evaluation user and its deterministic history exist in the LOCAL
database, run every question of the chosen set through the orchestrator in-process, write raw
outputs to .local/eval/<timestamp>/ (never committed), print metrics, and write a grading sheet
for the manual rubric in ml/eval/rubric.md.

Engineering tests use a scripted model; this script measures the real model. A poor result is
reported as such, never hidden.
"""

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/ingestion-api/src"))
sys.path.insert(0, str(ROOT / "ml/eval"))

from synthetic_history import observations  # noqa: E402

from healthops.assistant.contracts import AskRequest  # noqa: E402
from healthops.assistant.prompts import PROMPT_VERSION  # noqa: E402
from healthops.config import Settings  # noqa: E402
from healthops.db import build_engine  # noqa: E402
from healthops.main import build_assistant  # noqa: E402
from healthops.models import User  # noqa: E402
from healthops.repository import ingest_batch  # noqa: E402
from healthops.schemas import ObservationIn  # noqa: E402

EVAL_USER_NAME = "assistant-eval-synthetic"
WARNING_KEYWORDS = {
    "sparse_window": ["sparse", "few days", "only", "limited"],
    "no_records_in_window": [
        "no record",
        "no data",
        "not enough",
        "nothing recorded",
        "no ",
        "missing",
    ],
    "insufficient_data": ["not enough", "insufficient", "no data", "missing"],
    "interval_totals_used": ["interval", "recorded intervals", "not full", "only cover"],
    "overlapping_intervals": ["overlap"],
    "provider_missing_in_window": ["only", "missing", "no data"],
    "interval_crosses_day_boundary": ["start", "midnight", "assigned"],
}


def ensure_eval_user(factory: sessionmaker) -> User:
    with factory() as session:
        user = session.scalar(select(User).where(User.display_name == EVAL_USER_NAME))
        if user is None:
            user = User(display_name=EVAL_USER_NAME)
            session.add(user)
            session.commit()
        records = [ObservationIn.model_validate(r) for r in observations()]
        for start in range(0, len(records), 500):
            ingest_batch(session, user.id, records[start : start + 500])
        session.expunge(user)
        return user


def argument_match(expected: dict | None, actual: dict) -> bool | None:
    if not expected:
        return None
    return all(str(actual.get(k)) == str(v) for k, v in expected.items())


def mentions_warnings(answer: str, warnings: list[str]) -> bool | None:
    if not warnings:
        return None
    text = answer.lower()
    return any(
        any(k in text for k in WARNING_KEYWORDS.get(w, [w.replace("_", " ")])) for w in warnings
    )


async def run(args):
    settings = Settings()
    settings.require_local()
    if not settings.assistant_model_base_url:
        sys.exit("Set HEALTHOPS_ASSISTANT_MODEL_BASE_URL (for example http://127.0.0.1:11434).")
    engine = build_engine(settings.database_url.get_secret_value())
    factory = sessionmaker(engine, expire_on_commit=False)
    user = ensure_eval_user(factory)
    # Exactly the API's assistant and limits; only the evaluation-time "today" override differs.
    assistant = build_assistant(
        settings.model_copy(update={"assistant_allow_today_override": True}), factory, None
    )
    model = assistant.model
    if hasattr(model, "describe"):
        await model.describe()  # digest / context window recorded with the run
    questions = [
        json.loads(line)
        for line in (ROOT / f"ml/eval/assistant_{args.set}.synthetic.jsonl")
        .read_text()
        .splitlines()
        if line
    ]
    if args.limit:
        questions = questions[: args.limit]
    out_dir = ROOT / ".local/eval" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, latencies = [], []
    for item in questions:
        started = time.monotonic()
        response = await assistant.ask(
            AskRequest(
                question=item["question"],
                timezone=item["timezone"],
                today=date.fromisoformat(item["today"]),
            ),
            user.id,
        )
        latency = time.monotonic() - started
        latencies.append(latency)
        expected = item["expected"]
        executed = [c for c in response.tool_calls if c.status != "not_executed"]
        first = executed[0] if executed else None
        expected_tool = {"decline": "decline_request", "clarify": None}.get(
            expected["tool"], expected["tool"]
        )
        row = {
            "id": item["id"],
            "question": item["question"],
            "status": response.status,
            "expected_tool": expected_tool,
            "first_tool": first.name if first else None,
            "tool_selected_ok": (
                (first.name == expected_tool)
                if first
                else (expected_tool is None and response.status == "needs_clarification")
            ),
            "explanation_status": response.explanation_status,
            "claims_valid": sum(1 for c in response.claims if c.valid),
            "claims_total": len(response.claims),
            "arguments_semantic_ok": argument_match(expected.get("arguments"), first.arguments)
            if first
            else None,
            # The same check on what the model wrote, before server adjustments.
            "model_arguments_semantic_ok": argument_match(
                expected.get("arguments"), first.model_arguments
            )
            if first
            else None,
            "server_adjustments": [
                w
                for w in (first.warnings if first else [])
                if w.endswith("_ignored") or "reordered" in w
            ],
            "arguments_valid": (first.status == "ok") if first else None,
            "numeric_consistency": response.numeric_consistency.status,
            "unmatched_numbers": response.numeric_consistency.unmatched,
            "tool_warnings": sorted({w for c in response.tool_calls for w in c.warnings}),
            "warnings_mentioned": mentions_warnings(
                response.answer, sorted({w for c in response.tool_calls for w in c.warnings})
            ),
            "refusal_ok": (response.status == "refused") if expected["tool"] == "decline" else None,
            # Which fixed refusal the user saw; the model's free-text detail is not copied here.
            "decline_category": next(
                (c.arguments.get("category") for c in executed if c.name == "decline_request"),
                None,
            ),
            "clarification_ok": (response.status == "needs_clarification")
            if expected["tool"] == "clarify"
            else None,
            "declined_in_text_only": response.status == "no_tool_answer",
            "latency_s": round(latency, 2),
            "answer": response.answer,
            "notes": item["notes"],
        }
        rows.append(row)
        (out_dir / f"{item['id']}.json").write_text(response.model_dump_json(indent=2))
        print(
            f"{item['id']:8} {response.status:18} tool={row['first_tool']!s:20} "
            f"args_ok={row['arguments_semantic_ok']!s:5} {latency:6.1f}s"
        )

    def rate(key):
        values = [r[key] for r in rows if r[key] is not None]
        return (sum(1 for v in values if v) / len(values), len(values)) if values else (None, 0)

    warm = latencies[1:] or latencies
    summary = {
        "set": args.set,
        "questions": len(rows),
        "model": model.info.model_dump(),
        "limits": assistant.limits().model_dump(),
        "prompt_version": PROMPT_VERSION,
        "tool_selection_accuracy": rate("tool_selected_ok"),
        "argument_semantic_correctness_executed": rate("arguments_semantic_ok"),
        "argument_semantic_correctness_model_original": rate("model_arguments_semantic_ok"),
        "runs_with_server_adjustments": sum(1 for r in rows if r["server_adjustments"]),
        "argument_validity": rate("arguments_valid"),
        "refusal_correctness": rate("refusal_ok"),
        "no_tool_answers": sum(1 for r in rows if r["declined_in_text_only"]),
        "clarification_correctness": rate("clarification_ok"),
        "explanation_status": {
            k: sum(1 for r in rows if r["explanation_status"] == k)
            for k in ("verified", "unverified", "not_applicable")
        },
        "claims": {
            "valid": sum(r["claims_valid"] for r in rows),
            "total": sum(r["claims_total"] for r in rows),
        },
        "warning_propagation": rate("warnings_mentioned"),
        "numeric_consistency": {
            s: sum(1 for r in rows if r["numeric_consistency"] == s)
            for s in ("consistent", "unmatched", "not_applicable")
        },
        "status_counts": {
            s: sum(1 for r in rows if r["status"] == s) for s in sorted({r["status"] for r in rows})
        },
        "latency_cold_first_s": round(latencies[0], 2) if latencies else None,
        "latency_warm_p50_s": round(statistics.median(warm), 2) if warm else None,
        "latency_warm_p95_s": round(sorted(warm)[max(0, int(len(warm) * 0.95) - 1)], 2)
        if warm
        else None,
        "note": "Small-sample descriptive numbers from a local quantized model; not a deployment "
        "measurement. Answer correctness requires the manual rubric (grading_sheet.csv).",
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    with (out_dir / "grading_sheet.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "id",
                "question",
                "status",
                "first_tool",
                "answer",
                "notes",
                "metric_correct",
                "unit_correct",
                "window_correct",
                "provider_stated",
                "direction_correct",
                "caveats_kept",
                "no_invention",
                "verdict",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r["id"],
                    r["question"],
                    r["status"],
                    r["first_tool"],
                    r["answer"],
                    r["notes"],
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )
    print(json.dumps(summary, indent=2))
    print(f"Raw outputs and grading sheet: {out_dir}")
    engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--set", choices=["dev", "test", "heldout2", "protocol", "reference"], default="dev"
    )
    parser.add_argument("--limit", type=int, default=0)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
