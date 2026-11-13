"""Writer-only real-model probe on the committed synthetic set; no DB or real accounts.

Basic-check acceptance is reported separately from the unscored manual quality rubric.
Raw outputs stay in the service's ignored .local/eval directory.
"""

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from healthops.assistant.cards import Card
from healthops.assistant.model import ModelError, OllamaChatModel
from healthops.assistant.narrative import PROMPT_VERSION, messages, packet, qualifications, validate

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "original": "answer_quality.synthetic.jsonl",
    "additional": "answer_quality.additional.synthetic.jsonl",
    "development": "writer-corpus/development.synthetic.jsonl",
    "reserved": "writer-corpus/reserved_test.synthetic.jsonl",
}


def load_cases(suite, *, include_reserved=False):
    if suite == "reserved" and not include_reserved:
        raise ValueError(
            "Reserved evaluation requires --include-reserved; never use it for tuning."
        )
    filename = SUITES[suite]
    cases = [json.loads(line) for line in (ROOT / "eval" / filename).read_text().splitlines()]
    if not cases or any(c["data_kind"] != "synthetic" for c in cases):
        raise ValueError("Only the committed synthetic suites are accepted")
    expected_split = {"development": "development", "reserved": "reserved_test"}.get(suite)
    if expected_split and any(c.get("split") != expected_split for c in cases):
        raise ValueError("Suite contains a record from the wrong split")
    return cases


def writer_input(case):
    """Allowlist input fields so labels/rubrics never become model input."""
    cards = [Card.model_validate(c) for c in case["cards"]]
    facts = packet(cards, case["today"])
    return cards, facts, messages(case["question"], facts, case["language"])


async def run(base_url, suite="original", *, include_reserved=False):
    cases = load_cases(suite, include_reserved=include_reserved)
    model = OllamaChatModel(
        base_url,
        "qwen3:8b",
        prompt_version=PROMPT_VERSION,
        thinking=False,
        timeout_seconds=30,
        max_output_tokens=800,
    )
    await model.describe()
    folder = (
        ROOT / ".local/eval" / ("answer-quality-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    )
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "cases.snapshot.json").write_text(json.dumps(cases, indent=2))
    corpus_hash = hashlib.sha256((ROOT / "eval" / SUITES[suite]).read_bytes()).hexdigest()
    results = []
    for case in cases:
        cards, facts, payload = writer_input(case)
        started = time.monotonic()
        raw = None
        prompt_tokens = completion_tokens = None
        try:
            turn = await model.complete(payload, [])
            raw = turn.text
            prompt_tokens, completion_tokens = turn.prompt_tokens, turn.completion_tokens
            answer, reason = validate(turn, facts)
        except ModelError as exc:
            answer, reason = None, type(exc).__name__
        if answer:
            answer = " ".join([answer, *qualifications(cards, case["language"], case["today"])])
        item = {
            "id": case["id"],
            "scenario_group": case.get("scenario_group"),
            "input_messages": payload,
            "evidence_packet": facts,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "seconds": round(time.monotonic() - started, 2),
            "basic_checks_passed": answer is not None,
            "reason": reason,
            "answer": answer,
            "raw_model_output": raw,
            "manual_rubric": case["manual_rubric"],
            "manual_quality_result": "not_reviewed",
        }
        results.append(item)
        print(
            json.dumps({k: item[k] for k in ("id", "seconds", "basic_checks_passed", "reason")}),
            flush=True,
        )
        (folder / "results.json").write_text(
            json.dumps(
                {
                    "model": model.info.model_dump(),
                    "suite": suite,
                    "corpus_sha256": corpus_hash,
                    "scope": "writer_only_not_router_or_tool_calling",
                    "results": results,
                },
                indent=2,
            )
        )
    print(f"Saved private results: {folder}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-url", required=True)
    parser.add_argument("--suite", choices=sorted(SUITES), default="original")
    parser.add_argument(
        "--include-reserved",
        action="store_true",
        help="Explicitly evaluate the frozen reserved set; not for prompt tuning",
    )
    args = parser.parse_args()
    asyncio.run(run(args.model_url, args.suite, include_reserved=args.include_reserved))
