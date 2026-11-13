"""Replay audited development-stage inputs across models; never claim end-to-end accuracy."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from healthops.assistant.model import OllamaChatModel
from healthops.assistant.narrative import (
    check_draft,
    editor_messages,
    is_writer_system,
    parse_draft,
)

ROOT = Path(__file__).resolve().parents[1]
DEV_ACCOUNTS = {"synthetic-00000", "synthetic-00001", "synthetic-00003"}


def preference_rows(folder):
    """Replay reviewed training prompts only; reference answers are never model inputs."""
    manifest = json.loads((folder / "manifest.json").read_text())
    path = folder / "train.jsonl"
    if (
        manifest.get("approved") is not True
        or manifest.get("data_kind") != "synthetic_development"
        or hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"]["train.jsonl"]
    ):
        raise ValueError("Expected an immutable approved synthetic development bundle")
    data = [json.loads(line) for line in path.read_text().splitlines()]
    approvals = [r for r in manifest["pairs"] if r["split"] == "train"]
    if len(data) != len(approvals) or len(data) != manifest["counts"]["train"]:
        raise ValueError("Training preference count changed")
    rows = []
    for row, approval in zip(data, approvals, strict=True):
        fingerprint = hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest()
        expected = approval.get("export_sha256", approval["pair_sha256"])
        if fingerprint != expected or not is_writer_system(row["prompt"][0]):
            raise ValueError("Preference pair or prompt changed after review")
        payload = json.dumps(
            {"messages": row["prompt"], "tools": []}, sort_keys=True, ensure_ascii=True
        )
        rows.append(
            {
                "id": approval["id"],
                "stage": "writer",
                "messages": row["prompt"],
                "tools": [],
                "input_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "reference_chosen": row["chosen"],
                "historical_rejected": row["rejected"],
                "split": "development_training_diagnostic",
                "training_eligible": False,
            }
        )
    return rows


def load_rows(folder, stage):
    rows = []
    for path in sorted(folder.glob("*.json")):
        doc = json.loads(path.read_text())
        if not isinstance(doc, dict) or "case" not in doc or "turns" not in doc:
            continue
        case = doc["case"]
        if (
            case.get("data_kind") != "synthetic"
            or case.get("split") != "development"
            or case.get("source_user_id") not in DEV_ACCOUNTS
        ):
            raise ValueError("Only synthetic development captures are allowed")
        for turn_index, turn in enumerate(doc["turns"]):
            for call_index, call in enumerate(turn.get("model_calls", [])):
                messages = call["messages"]
                names = {tool["function"]["name"] for tool in call["tools"]}
                kind = (
                    "writer"
                    if is_writer_system(messages[0])
                    else "router"
                    if names == {"route_request"}
                    else "execution"
                )
                # First writer draft only; editor replays are generated afresh from diagnostics.
                if kind == "writer" and len(messages) != 2:
                    continue
                if kind == "writer" and not turn.get("evidence_checks", {}).get("ready"):
                    raise ValueError("Writer evidence must have a successful capture audit")
                if stage != "all" and kind != stage:
                    continue
                payload = json.dumps(
                    {"messages": messages, "tools": call["tools"]},
                    sort_keys=True,
                    ensure_ascii=True,
                )
                rows.append(
                    {
                        "id": f"{doc['id']}-{turn_index}-{call_index}",
                        "stage": kind,
                        "messages": messages,
                        "tools": call["tools"],
                        "input_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                        "source_trace_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "reference_tool_calls": call["tool_calls"],
                        "split": "development",
                        "training_eligible": False,
                    }
                )
    if not rows:
        raise ValueError("No matching audited development-stage inputs")
    return rows


async def evaluate(rows, model, output):
    results = []
    for row in rows:
        started = time.monotonic()
        result = {
            "id": row["id"],
            "stage": row["stage"],
            "input_sha256": row["input_sha256"],
            "semantic_review": "pending",
            "error": None,
            "edited": False,
        }
        try:
            turn = await model.complete(row["messages"], row["tools"])
            result.update(
                raw_output=turn.text,
                tool_calls=[vars(c) for c in turn.tool_calls],
                prompt_tokens=turn.prompt_tokens,
                completion_tokens=turn.completion_tokens,
            )
            if row["stage"] == "writer":
                facts = json.loads(row["messages"][1]["content"])["evidence"]
                answer, issues = check_draft(turn, facts)
                result["first_check_issues"] = issues
                if issues:
                    result["edited"] = True
                    edit = await model.complete(editor_messages(row["messages"], turn, issues), [])
                    result["editor_raw_output"] = edit.text
                    draft, error = parse_draft(edit)
                    result["error"] = error
                    answer = draft.answer if draft else None
                result["final_answer"] = answer
            else:
                # Reference-call agreement is descriptive, not an independent gold score.
                actual = [(c.name, json.loads(c.arguments_json)) for c in turn.tool_calls]
                reference = [
                    (c["name"], json.loads(c["arguments_json"]))
                    for c in row["reference_tool_calls"]
                ]
                result["reference_call_agreement"] = actual == reference
        except Exception as exc:
            result["error"] = type(exc).__name__
        result["seconds"] = round(time.monotonic() - started, 3)
        results.append(result)
        (output / "results.json").write_text(
            json.dumps(results, ensure_ascii=True, indent=2) + "\n"
        )
        print(row["id"], result["error"] or "captured", flush=True)
    return results


async def run(args):
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / ".local/eval").resolve()):
        raise ValueError("Raw results must remain under api-service/.local/eval")
    rows = (
        preference_rows(args.preference_bundle)
        if args.preference_bundle
        else load_rows(args.captures, args.stage)
    )
    output.mkdir(parents=True, exist_ok=False)
    inputs = json.dumps(rows, ensure_ascii=True, sort_keys=True).encode()
    (output / "inputs.json").write_bytes(inputs)
    model = OllamaChatModel(
        args.model_url,
        args.model,
        prompt_version="stage-sensitivity-v1",
        thinking=False,
        temperature=args.temperature,
        max_output_tokens=800,
        timeout_seconds=180,
    )
    metadata = {
        "execution": "frozen_stage_replay_not_full_chain",
        "stage": args.stage,
        "model": (await model.describe()).model_dump(mode="json"),
        "input_sha256": hashlib.sha256(inputs).hexdigest(),
        "count": len(rows),
        "temperature": args.temperature,
        "thinking": False,
        "max_output_tokens": 800,
        "status": "running",
        "semantic_review": "pending",
        "training_eligible": False,
    }
    (output / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
    results = await evaluate(rows, model, output)
    metadata.update(
        status="completed",
        transport_or_parse_errors=sum(bool(r["error"]) for r in results),
        model=(await model.describe()).model_dump(mode="json"),
    )
    (output / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--captures", type=Path)
    source.add_argument("--preference-bundle", type=Path)
    parser.add_argument(
        "--stage", choices=["writer", "router", "execution", "all"], default="writer"
    )
    parser.add_argument("--model-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
