"""Replay frozen writer inputs; keep mechanical checks separate from semantic review."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from healthops.assistant.model import ModelError, ModelTurn, OpenAICompatibleChatModel
from healthops.assistant.narrative import PROMPT_VERSION, is_writer_system, validate

ROOT = Path(__file__).resolve().parents[1]
FROZEN_INPUTS_SHA256 = "74123deb9030526e799dfcec4923544a37bf0fd0af117fcdd6d4257fd5582710"
DURATION_INPUTS_SHA256 = "da41bbeeb206ff0a60f3a3f69011953874c4e9a97b169efc0d047aadfaf6273a"


def messages_hash(messages):
    content = json.dumps(messages, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


class WriterDispatchModel:
    """Evaluation-only adapter: route exactly the final writer call to another model."""

    def __init__(self, base, writer):
        self.base, self.writer = base, writer

    @property
    def info(self):
        return self.base.info

    async def complete(self, messages, tools):
        is_writer = bool(messages) and is_writer_system(messages[0])
        if is_writer and tools:
            raise ValueError("Writer call unexpectedly exposes tools")
        return await (self.writer if is_writer else self.base).complete(messages, tools)


def writer_model(url, model):
    return OpenAICompatibleChatModel(
        url,
        model,
        prompt_version=PROMPT_VERSION,
        max_output_tokens=800,
        temperature=0.0,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )


def load_inputs(folder, *, duration=False):
    path = folder / "writer-baseline-inputs.jsonl"
    expected_hash = DURATION_INPUTS_SHA256 if duration else FROZEN_INPUTS_SHA256
    expected_count = 2 if duration else 11
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("Frozen writer test inputs changed")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if len(rows) != expected_count or len({r["id"] for r in rows}) != expected_count:
        raise ValueError(f"Expected {expected_count} valid captured writer inputs")
    for row in rows:
        if (
            row["split"] != "heldout_test"
            or row["training_eligible"] is not False
            or row["tools"]
            or messages_hash(row["messages"]) != row["messages_sha256"]
            or not is_writer_system(row["messages"][0])
        ):
            raise ValueError("Test provenance or writer contract changed")
    return rows


async def evaluate(rows, model):
    results = []
    for row in rows:
        facts = json.loads(row["messages"][1]["content"])["evidence"]
        raw, error, tool_calls = None, None, []
        try:
            turn = await model.complete(row["messages"], [])
            raw = turn.text
            tool_calls = [vars(t) for t in turn.tool_calls]
            _, error = validate(turn, facts)
        except ModelError as exc:
            error = type(exc).__name__
        _, old_error = validate(ModelTurn(text=row["baseline_raw_output"]), facts)
        results.append(
            {
                "id": row["id"],
                "messages_sha256": row["messages_sha256"],
                "raw_output": raw,
                "tool_calls": tool_calls,
                "mechanical_error": error,
                "historical_baseline_mechanical_error": old_error,
                "semantic_verdict": "pending",
                "review_reason": "",
                "training_eligible": False,
            }
        )
    return results


def compare_reviews(before, after):
    """Require every label and identical prompts; never count JSON validity as quality."""
    left, right = ({r["id"]: r for r in rows} for rows in (before, after))
    if len(left) != len(before) or len(right) != len(after) or left.keys() != right.keys():
        raise ValueError("Comparison requires matching, unique case ids")
    if not left:
        raise ValueError("Empty comparison")
    for ident in left:
        if left[ident]["messages_sha256"] != right[ident]["messages_sha256"]:
            raise ValueError("Compare identical evidence and prompts")
        for row in (left[ident], right[ident]):
            if row.get("semantic_verdict") not in {"pass", "partial", "fail"} or not row.get(
                "review_reason"
            ):
                raise ValueError("Complete explicit semantic reviews before reporting pass rate")
    return {
        "paired_writer_inputs": len(left),
        "before_pass": sum(r["semantic_verdict"] == "pass" for r in before),
        "after_pass": sum(r["semantic_verdict"] == "pass" for r in after),
        "not_a_full_chain_score": True,
    }


async def run(args):
    rows = load_inputs(args.baseline)
    supplement = getattr(args, "duration_supplement", None)
    if supplement:
        rows += load_inputs(supplement, duration=True)
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError("Duplicate test ids across suites")
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / ".local/eval").resolve()):
        raise ValueError("Test responses must stay under api-service/.local/eval")
    output.mkdir(parents=True, exist_ok=False)
    meta = {
        "status": "running",
        "checkpoint_identity": args.checkpoint_identity,
        "serving_precision": args.serving_precision,
        "model": args.model,
        "inputs_sha256": FROZEN_INPUTS_SHA256,
        "duration_inputs_sha256": DURATION_INPUTS_SHA256 if supplement else None,
        "full_chain_scenarios": 26 if supplement else 20,
        "writer_replay_inputs": len(rows),
        "upstream_not_reached_in_original_baseline": 13 if supplement else 9,
        "temperature": 0.0,
        "max_tokens": 800,
        "enable_thinking": False,
        "semantic_review": "pending",
        "training_eligible": False,
    }
    (output / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    results = await evaluate(rows, writer_model(args.model_url, args.model))
    path = output / "review.json"
    path.write_text(json.dumps(results, ensure_ascii=True, indent=2) + "\n")
    path.chmod(0o600)
    meta["status"] = "completed_pending_review"
    meta["mechanical_pass"] = sum(r["mechanical_error"] is None for r in results)
    (output / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    print("Replay complete; semantic review is required before reporting answer quality")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=ROOT / ".local/eval/writer-holdout-v1")
    parser.add_argument(
        "--duration-supplement",
        type=Path,
        help="Append the two frozen writer inputs from six test-only duration scenarios",
    )
    parser.add_argument("--model-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--checkpoint-identity", required=True, help="Immutable HF/adapter or artifact hash"
    )
    parser.add_argument("--serving-precision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(run(parser.parse_args()))
