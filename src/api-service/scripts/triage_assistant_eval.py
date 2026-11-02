"""Summarize synthetic diagnostic failures for review, never auto-create training labels."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local/eval"
ACCOUNTS = {"synthetic-00000", "synthetic-00003", "synthetic-00001"}


def categories(case):
    """Signals for human diagnosis, not proven causal attribution."""
    found = set()
    for turn in case["turns"]:
        if turn.get("error_type"):
            found.add("transport_or_state")
            continue
        response = turn.get("response", {})
        checks = turn.get("grade", {}).get("checks", {})
        empty = any(
            e.get("tool") == "lookup_reference" and e.get("result", {}).get("references") == []
            for e in response.get("evidence", [])
        )
        if empty:
            found.add("retrieval_empty")
        if any(checks.get(k) is False for k in ("route", "request_spec", "missing_fields")):
            found.add("routing_or_scope")
        if checks.get("primary_tool") is False:
            found.add("tool_choice")
        calls = response.get("tool_calls", [])
        if any(c.get("status") in {"invalid_arguments", "rejected"} for c in calls):
            found.add("execution_contract")
        if any(c.get("status") == "error" for c in calls):
            found.add("tool_service")
        if any(checks.get(k) is False for k in ("coverage", "claims_rechecked", "verified")):
            found.add("verification_or_coverage")
        if response.get("status") in {"model_error", "budget_exceeded"}:
            found.add("model_service_or_budget")
        if response.get("status") == "tool_error" and not empty and not found:
            found.add("unreviewed_failure")
    if not case.get("passed") and not found:
        found.add("unreviewed_failure")
    return sorted(found) or ["no_flagged_signals"]


def summarize(cases):
    counts = Counter()
    rubric_passes = 0
    reviews = []
    seen = set()
    for case in cases:
        cid = case.get("id", "")
        if case.get("source_user_id") not in ACCOUNTS:
            raise ValueError("Only frozen synthetic account reports are accepted")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", cid) or cid in seen:
            raise ValueError("Invalid or duplicate scenario ID")
        seen.add(cid)
        rubric_passes += bool(case.get("passed"))
        labels = categories(case)
        counts.update(labels)
        if labels != ["no_flagged_signals"]:
            reviews.append(
                {
                    "id": cid,
                    "signals": labels,
                    "passed_current_rubric": bool(case.get("passed")),
                    "eligible_for_training": False,
                    "required_review": (
                        "Confirm upstream fixes, inspect exact context, "
                        "author and validate a target, and separate evaluation from training."
                    ),
                }
            )
    return {
        "scenario_count": len(seen),
        "rubric_passed_scenarios": rubric_passes,
        "signal_counts": dict(sorted(counts.items())),
        "training_examples_created": 0,
        "reviews": reviews,
        "note": (
            "Signals can overlap. Routing-only passes are not full-answer success. "
            "No automatic preference labels or causal claims."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.run.resolve().is_relative_to(
        LOCAL.resolve()
    ) or not args.output.resolve().is_relative_to(LOCAL.resolve()):
        raise ValueError("Input and output must remain under api-service/.local/eval")
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing report")
    cases = []
    for path in sorted(args.run.glob("*.json")):
        value = json.loads(path.read_text())
        if isinstance(value, dict) and "source_user_id" in value and "turns" in value:
            cases.append(value)
    if not cases:
        raise ValueError("No completed scenario reports found")
    report = summarize(cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    args.output.chmod(0o600)
    print(json.dumps({k: v for k, v in report.items() if k != "reviews"}, indent=2))


if __name__ == "__main__":
    main()
