"""Review-bound preference exports; never regenerate prompts or evidence."""

import argparse
import hashlib
import json
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
    path.chmod(0o600)


def pair(row):
    """NeMo BinaryPreferenceDataset preserves the conversation list verbatim."""
    for key in ("chosen", "rejected"):
        messages = row[key]
        if len(messages) != 1 or messages[0].get("role") != "assistant":
            raise ValueError("Exactly one assistant completion is required")
        if not isinstance(messages[0].get("content"), str) or not messages[0]["content"]:
            raise ValueError("Empty completion")
    return {
        "prompt": row["prompt"],
        "chosen": row["chosen"][0]["content"],
        "rejected": row["rejected"][0]["content"],
    }


def validate_row(row, split, system_prompt):
    if row.get("proposed_split") != split or split not in {"train", "validation"}:
        raise ValueError("Only assigned development train/validation candidates are accepted")
    if "holdout" in row["id"].lower() or "test" in row.get("split", "").lower():
        raise ValueError("Held-out tests must never become preferences")
    if row.get("contract_blocked") is not False or row.get("basic_validator_error"):
        raise ValueError("Unresolved output-contract failure")
    if not row.get("split_group"):
        raise ValueError("Missing leakage group")
    result = pair(row)
    prompt = result["prompt"]
    if (
        len(prompt) != 2
        or prompt[0] != {"role": "system", "content": system_prompt}
        or prompt[1].get("role") != "user"
    ):
        raise ValueError("Writer prompt contract changed; recapture and review, do not rewrite")
    evidence = json.loads(prompt[1]["content"])["evidence"]
    if evidence != row["evidence_packet"] or not evidence.get("sources"):
        raise ValueError("Evidence differs from the exact model input")
    chosen, rejected = json.loads(result["chosen"]), json.loads(result["rejected"])
    if chosen == rejected:
        raise ValueError("Identical completions do not define a preference")
    if (
        set(chosen) != {"answer", "evidence_ids"}
        or not isinstance(chosen["answer"], str)
        or not 1 <= len(chosen["answer"]) <= 2200
        or set(chosen["evidence_ids"]) != {s["evidence_id"] for s in evidence["sources"]}
    ):
        raise ValueError("Chosen completion violates the writer output contract")
    return result


def verify_capture(row, trace_roots=()):
    """Check the observed negative and prompt against their original local trace."""
    if row.get("source"):
        path = Path(row["source"])
        trace = json.loads(path.read_text())
        if trace.get("provenance") not in {
            "reconstructed_synthetic_fixture_uploaded_via_API_not_cohort",
            "synthetic_v0.2_cohort_real_predictor_old_conditions_not_guaranteed",
        }:
            raise ValueError("Unrecognized synthetic replay provenance")
        messages, raw = trace["input_messages"], trace["raw_model_output"]
    else:
        if row.get("trace_source"):
            path = Path(row["trace_source"]).parent / (row["id"] + ".json")
        else:
            matches = [root / (row["id"] + ".json") for root in trace_roots]
            matches = [p for p in matches if p.is_file()]
            if len(matches) != 1:
                raise ValueError("Missing original trace; supply one explicit --trace-root")
            path = matches[0]
        trace = json.loads(path.read_text())
        if file_hash(path) != row["trace_sha256"]:
            raise ValueError("Original trace hash changed")
        if trace["case"]["split"] != "development" or trace["case"]["data_kind"] != "synthetic":
            raise ValueError("Only synthetic development captures are accepted")
        matches = [c for c in trace["turns"][-1]["model_calls"] if c["messages"] == row["prompt"]]
        if len(matches) != 1 or matches[0].get("tools"):
            raise ValueError("Expected one tool-free writer call")
        messages, raw = matches[0]["messages"], matches[0].get("raw_text")
    if messages != row["prompt"] or raw != row["rejected"][0]["content"]:
        raise ValueError("Prompt or rejected output differs from the captured model call")
    return file_hash(path)


def inspect(train, validation, system_prompt, capture_check=verify_capture):
    report, seen_ids, seen_groups, seen_evidence, seen_prompts = [], set(), {}, {}, {}
    for split, rows in (("train", train), ("validation", validation)):
        if not rows:
            raise ValueError(f"Empty {split} split")
        for row in rows:
            if row["id"] in seen_ids:
                raise ValueError("Duplicate case id")
            seen_ids.add(row["id"])
            for value, seen in (
                (row["split_group"], seen_groups),
                (fingerprint(row["evidence_packet"]), seen_evidence),
                (fingerprint(row["prompt"]), seen_prompts),
            ):
                if value in seen and seen[value] != split:
                    raise ValueError("Train/validation leakage")
                seen[value] = split
            result = validate_row(row, split, system_prompt)
            report.append(
                {
                    "id": row["id"],
                    "split": split,
                    "group": row["split_group"],
                    "pair_sha256": fingerprint(result),
                    "trace_sha256": capture_check(row),
                    "review_status": row.get("review_status"),
                }
            )
    return report


def export_bundle(train, validation, report, approvals, output):
    """A saved prose approval is not silently treated as approval of changed inputs."""
    by_id = {r["id"]: r for r in approvals}
    if len(by_id) != len(approvals) or set(by_id) != {r["id"] for r in report}:
        raise ValueError("One exact approval per candidate is required")
    for row in report:
        approval = by_id[row["id"]]
        if (
            approval.get("decision") != "approve"
            or not approval.get("reviewer")
            or approval.get("pair_sha256") != row["pair_sha256"]
            or approval.get("split") != row["split"]
        ):
            raise ValueError("Pending, stale or mismatched pair approval")
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for split, rows in (("train", train), ("validation", validation)):
        path = output / f"{split}.jsonl"
        path.write_text("".join(json.dumps(pair(r), ensure_ascii=True) + "\n" for r in rows))
        path.chmod(0o600)
        hashes[path.name] = file_hash(path)
    manifest = {
        "schema": "healthops-writer-dpo-v1",
        "approved": True,
        "data_kind": "synthetic_development",
        "files": hashes,
        "counts": {"train": len(train), "validation": len(validation)},
        "system_prompt_sha256": fingerprint(train[0]["prompt"][0]["content"]),
        "pairs": report,
        "approvals_sha256": fingerprint(approvals),
    }
    write_json(output / "manifest.json", manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--system-prompt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approvals", type=Path)
    parser.add_argument("--trace-root", action="append", type=Path, default=[])
    args = parser.parse_args()
    if ".local" not in args.output.resolve().parts:
        raise ValueError("Write review artifacts only under an ignored .local directory")
    train = read_rows(args.candidates / "train.candidates.jsonl")
    validation = read_rows(args.candidates / "validation.candidates.jsonl")
    pinned = json.loads(Path(__file__).with_name("config.json").read_text())
    if fingerprint(args.system_prompt.read_text()) != pinned["writer_system_sha256"]:
        raise ValueError("System prompt differs from the pinned application writer contract")
    report = inspect(
        train,
        validation,
        args.system_prompt.read_text(),
        capture_check=lambda row: verify_capture(row, args.trace_root),
    )
    if args.approvals:
        export_bundle(
            train, validation, report, json.loads(args.approvals.read_text()), args.output
        )
        print("Approved development export written; no model training was started")
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        write_json(args.output / "audit.json", report)
        write_json(
            args.output / "approvals.draft.json",
            [{**r, "decision": "pending", "reviewer": ""} for r in report],
        )
        print(f"Audited {len(report)} candidates; draft approvals are NOT a training export")


if __name__ == "__main__":
    main()
