"""Print a Markdown table of every evaluation run from its summary.json, so RESULTS.md rows are
copied from the raw summaries rather than typed by hand."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def row(summary: dict, name: str) -> str:
    status = ", ".join(f"{k} {v}" for k, v in sorted(summary["status_counts"].items()))
    explanation = summary.get("explanation_status")
    explanation_text = (
        f"{explanation['verified']} / {explanation['unverified']} / {explanation['not_applicable']}"
        if explanation
        else "n/a (pre-cards run)"
    )
    claims = summary.get("claims")
    claims_text = f"{claims['valid']} / {claims['total']}" if claims else "n/a"
    selection = summary["tool_selection_accuracy"]
    return (
        f"| {name} | {summary['set']} | {summary['prompt_version']} | "
        f"{round(selection[0] * selection[1])}/{selection[1]} | {status} | {explanation_text} | "
        f"{claims_text} | {summary.get('latency_warm_p50_s')} / "
        f"{summary.get('latency_warm_p95_s')} |"
    )


def main() -> int:
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".local/eval"
    print(
        "| Run | Set | Prompt | Tool selection | Data statuses | "
        "Explanation verified / unverified / n.a. | Claims valid / total | Warm p50 / p95 s |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        summary_file = directory / "summary.json"
        if not summary_file.exists():
            continue
        print(row(json.loads(summary_file.read_text()), directory.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
