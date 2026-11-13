"""Assemble unapproved preferences from reviewed, production-derived development traces.

Preserve exact observed writer prompts/completions. Never replace evidence, invent a
rejected answer, or promote a coding-assistant review to human-approved training data.
"""

import argparse
import hashlib
import json
from pathlib import Path

from healthops.assistant.contracts import AskResponse
from healthops.assistant.model import ModelTurn
from healthops.assistant.narrative import validate
from scripts.eval_writer_chain import ROOT, audit, write, writer_calls


def candidate(trace, review):
    if trace["case"]["split"] != "development" or trace["case"]["data_kind"] != "synthetic":
        raise ValueError("Only synthetic development traces may be used")
    if review.get("disposition") != "candidate":
        return None
    if (
        review.get("quality") not in {"partial", "fail"}
        or review.get("evidence_review") != "approved_silver"
    ):
        raise ValueError("A candidate needs a reviewed writing defect and supported evidence")
    if not review.get("reason") or not review.get("evidence_support"):
        raise ValueError("Review rationale and evidence pointers are required")
    if len(trace["turns"]) != len(trace["case"]["questions"]):
        raise ValueError("Blocked dialogue is not a writing negative")
    last = trace["turns"][-1]
    if last["http_status"] != 200:
        raise ValueError("HTTP failure is not a writing negative")
    response = AskResponse.model_validate(last["response"])
    checks = audit(response, last["model_calls"], trace["gold_tool_query"])
    if not all(checks.values()):
        raise ValueError("Evidence/trace checks failed; fix upstream before training")
    writers = writer_calls(last["model_calls"])
    if len(writers) != 1:
        raise ValueError("Writer retries require separate review; do not export as a first draft")
    call = writers[0]
    if call.get("tools"):
        raise ValueError("The final writer must be tool-free")
    payload = json.loads(call["messages"][1]["content"])
    context = json.loads(payload["user_request"])
    if [m["content"] for m in context["user_dialogue"] if m["role"] == "user"] != trace["case"][
        "questions"
    ]:
        raise ValueError("Writer dialogue differs from the actual scenario")
    facts = payload["evidence"]
    selected = last["request"].get("provider")
    if selected and selected not in {s["provider"] for s in facts["sources"]}:
        raise ValueError("Selected provider is absent from the evidence; repair context first")
    raw = call.get("raw_text")
    if not raw or not isinstance(json.loads(raw).get("answer"), str):
        raise ValueError("An observed prose completion is required")
    chosen = json.dumps(
        {
            "answer": review["chosen_answer"],
            "evidence_ids": [s["evidence_id"] for s in facts["sources"]],
        }
    )
    _, error = validate(ModelTurn(text=chosen), facts)
    if error:
        raise ValueError(f"Chosen output fails basic checks: {error}")
    if json.loads(chosen) == json.loads(raw):
        raise ValueError("Identical outputs are not a preference")
    return {
        "id": trace["id"],
        "split": "development_candidate",
        "prompt": call["messages"],
        "chosen": [{"role": "assistant", "content": chosen}],
        "rejected": [{"role": "assistant", "content": raw}],
        "evidence_provenance": "captured_authenticated_api_sql_tools_and_real_models",
        "evidence_packet": facts,
        "reason": review["reason"],
        "evidence_support": review["evidence_support"],
        "label_provenance": "coding_assistant_reviewed_silver",
        "review_status": "pending_independent_human_review",
        "training_eligible": False,
    }


def assemble(folder):
    folder = folder.resolve()
    if not folder.is_relative_to((ROOT / ".local/eval").resolve()):
        raise ValueError("Only local evaluation artifacts are accepted")
    meta = json.loads((folder / "run.json").read_text())
    if meta["status"] != "completed":
        raise ValueError("Complete the capture before preference review")
    cases = json.loads((folder / "cases.json").read_text())
    if hashlib.sha256((folder / "cases.json").read_bytes()).hexdigest() != meta["cases_sha256"]:
        raise ValueError("Captured cases changed")
    reviews = json.loads((folder / "review.json").read_text())
    for review in reviews:
        if not review.get("reason") or review.get("disposition") not in {
            "candidate",
            "retain_for_review",
            "input_gap",
            "input_review",
            "ambiguous_review",
            "upstream_blocked",
        }:
            raise ValueError("Every scenario needs an explicit disposition and rationale")
    by_id = {r["id"]: r for r in reviews}
    if len(by_id) != len(reviews) or set(by_id) != {c["id"] for c in cases}:
        raise ValueError("Exactly one explicit review per scenario is required")
    candidates = []
    for case in cases:
        path = folder / f"{case['id']}.json"
        trace = json.loads(path.read_text())
        if trace["case"] != case:
            raise ValueError("Trace differs from frozen scenario")
        row = candidate(trace, by_id[case["id"]])
        if row:
            row["trace_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            row["model"] = meta["model"]
            row["predictor"] = meta["predictor"]
            candidates.append(row)
    target = folder / "preference-candidates.jsonl"
    target.write_text("".join(json.dumps(r) + "\n" for r in candidates))
    target.chmod(0o600)
    write(
        folder / "preference-summary.json",
        {
            "reviewed": len(reviews),
            "candidates": len(candidates),
            "training_eligible": 0,
            "independent_human_review": False,
        },
    )
    render_review(folder, reviews, candidates)
    return candidates


def render_review(folder, reviews, candidates):
    """Readable review alongside exact machine-readable prompts; no approval implied."""
    lines = [
        "# Captured writer preference review",
        "",
        "These are coding-assistant silver candidates, not ground truth. No training is authorized "
        "by this file. Review the actual evidence, source attribution and statistical meaning "
        "before approving or editing either answer. A basic validator pass is not semantic proof.",
        "",
        "The rejected column is the raw model output before server fallback/qualifications. "
        "The exact system prompt and user payload are preserved in `preference-candidates.jsonl`.",
        "",
        "## Candidates",
        "",
    ]
    for row in candidates:
        context = json.loads(json.loads(row["prompt"][1]["content"])["user_request"])
        questions = [m["content"] for m in context["user_dialogue"] if m["role"] == "user"]
        lines.extend(
            [
                f"### {row['id']}",
                "",
                "**User turns:** " + " -> ".join(questions),
                "",
                "**Observed rejected answer:**",
                "",
                json.loads(row["rejected"][0]["content"])["answer"],
                "",
                "**Proposed chosen answer (unapproved):**",
                "",
                json.loads(row["chosen"][0]["content"])["answer"],
                "",
                "**Why review it:** " + row["reason"],
                "",
                "**Evidence pointers:** " + ", ".join(row["evidence_support"]),
                "",
                f"[Full authenticated API trace]({row['id']}.json)",
                "",
                "<details><summary>Exact evidence packet supplied to the writer</summary>",
                "",
                "```json",
                json.dumps(row["evidence_packet"], indent=2),
                "```",
                "",
                "</details>",
                "",
                "- [ ] Evidence and chosen meaning checked by a human",
                "- [ ] Approve / edit / reject (record decision and reviewer separately)",
                "",
            ]
        )
    lines.extend(
        [
            "## All scenario dispositions",
            "",
            "| Scenario | Disposition | Review |",
            "| --- | --- | --- |",
        ]
    )
    for row in reviews:
        reason = row["reason"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {row['id']} | {row['disposition']} | {reason} |")
    target = folder / "preference-review.md"
    target.write_text("\n".join(lines) + "\n")
    target.chmod(0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    rows = assemble(parser.parse_args().folder)
    print(f"Prepared {len(rows)} unapproved candidates; no training performed.")
