"""Build local, unapproved preference candidates from an explicitly reviewed development run.

This is not a training exporter: neither teacher prose nor reviewer approval is automatic gold.
Raw inputs, outputs and preference data must remain in ignored .local/eval/.
"""

import argparse
import hashlib
import json
from pathlib import Path

from healthops.assistant.model import ModelTurn
from healthops.assistant.narrative import validate
from scripts.eval_answer_quality import ROOT, writer_input


def build_candidates(run, cases, reviews):
    if run.get("suite") != "development" or any(
        c.get("split") != "development" or c.get("data_kind") != "synthetic" for c in cases
    ):
        raise ValueError("Only synthetic development cases may become preference candidates")
    content = "".join(json.dumps(c, ensure_ascii=True, sort_keys=True) + "\n" for c in cases)
    if hashlib.sha256(content.encode()).hexdigest() != run.get("corpus_sha256"):
        raise ValueError("Case snapshot does not match the evaluated corpus hash")
    case_map = {c["id"]: c for c in cases}
    result_map = {r["id"]: r for r in run["results"]}
    review_map = {r["id"]: r for r in reviews}
    if (
        len(case_map) != len(cases)
        or len(result_map) != len(run["results"])
        or len(review_map) != len(reviews)
        or case_map.keys() != result_map.keys()
        or case_map.keys() != review_map.keys()
    ):
        raise ValueError("One explicit review is required for every evaluated case")
    candidates = []
    for ident, case in case_map.items():
        result, review = result_map[ident], review_map[ident]
        if review.get("quality") not in {"pass", "partial", "fail"}:
            raise ValueError("Review needs pass/partial/fail")
        if review.get("disposition") not in {"candidate", "retain_success", "exclude"}:
            raise ValueError("Review needs an explicit disposition")
        if not review.get("reason") or not review.get("evidence_support"):
            raise ValueError("Review needs a rationale and evidence support")
        _, facts, expected_messages = writer_input(case)
        if (
            result.get("input_messages") != expected_messages
            or result.get("evidence_packet") != facts
        ):
            raise ValueError(
                "Recorded input differs from the frozen evidence/prompt implementation"
            )
        if review["disposition"] != "candidate":
            continue
        if review["quality"] == "pass":
            raise ValueError("Do not manufacture a negative from an acceptable answer")
        raw = result.get("raw_model_output")
        if not raw:
            raise ValueError("Infrastructure failures are not rejected writing answers")
        # Use the actual model completion, not the server's subsequently qualified answer.
        rejected = json.loads(raw)
        if not isinstance(rejected.get("answer"), str):
            raise ValueError("Candidate requires an inspectable writing completion")
        chosen = json.dumps(
            {
                "answer": review["chosen_answer"],
                "evidence_ids": [s["evidence_id"] for s in facts["sources"]],
            },
            ensure_ascii=False,
        )
        _, error = validate(ModelTurn(text=chosen), facts)
        if error:
            raise ValueError(f"Chosen answer fails basic consistency: {ident}: {error}")
        if json.loads(chosen) == rejected:
            raise ValueError("Identical answers do not define a preference")
        candidates.append(
            {
                "id": ident,
                "scenario_group": case["scenario_group"],
                "split": "development_candidate",
                "prompt": result["input_messages"],
                "chosen": [{"role": "assistant", "content": chosen}],
                "rejected": [{"role": "assistant", "content": raw}],
                "preference_reason": review["reason"],
                "evidence_support": review["evidence_support"],
                "label_provenance": "coding_assistant_reviewed_silver",
                "review_status": "pending_independent_human_review",
                "training_eligible": False,
                "model": run["model"],
                "corpus_sha256": run["corpus_sha256"],
            }
        )
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_folder", type=Path)
    args = parser.parse_args()
    folder = args.run_folder.resolve()
    if not folder.is_relative_to((ROOT / ".local/eval").resolve()):
        raise ValueError("Artifacts must remain inside the ignored .local/eval directory")
    run = json.loads((folder / "results.json").read_text())
    cases = json.loads((folder / "cases.snapshot.json").read_text())
    reviews = json.loads((folder / "review.json").read_text())
    candidates = build_candidates(run, cases, reviews)
    (folder / "preference-candidates.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in candidates)
    )
    lines = [
        "# Development writing review",
        "",
        "Assistant review; independent human approval pending.",
        "",
    ]
    for case in cases:
        # Look up by ID: reviewers need not preserve file ordering.
        result = next(r for r in run["results"] if r["id"] == case["id"])
        review = next(r for r in reviews if r["id"] == case["id"])
        lines += [
            f"## {case['id']}",
            "",
            case["question"],
            "",
            f"Quality: {review['quality']}; disposition: {review['disposition']}",
            "",
            "Observed raw completion:",
            "```json",
            result["raw_model_output"] or "null",
            "```",
            "",
            "Review: " + review["reason"],
            "",
            "Evidence: " + review["evidence_support"],
            "",
        ]
        if review.get("chosen_answer"):
            lines += ["Proposed preferred answer: " + review["chosen_answer"], ""]
    (folder / "review.md").write_text("\n".join(lines))
    print(f"Prepared {len(candidates)} unapproved preference candidates from {len(cases)} reviews")


if __name__ == "__main__":
    main()
