"""Evaluate frozen synthetic questions over an IAM-protected Cloud Run revision.

Requires the existing API Python environment and scripts directory on PYTHONPATH.
The temporary evaluation revision must explicitly allow frozen dates. Production does not.
"""

import argparse
import hashlib
import json
import statistics
import subprocess
import time
from pathlib import Path

import httpx
from prepare_cohort_baseline import encoded
from run_cohort_baseline import grade

from healthops.assistant.contracts import AskResponse
from healthops.assistant.prompts import SYSTEM_PROMPT
from healthops.assistant.toolbox import Toolbox


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("dataset", "probes", "credentials", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if not args.url.startswith("https://") or not args.url.endswith(".run.app"):
        raise ValueError("Expected Cloud Run HTTPS URL")
    lock = json.loads((args.dataset / "lock.json").read_text())
    for name, expected in lock["artifacts_sha256"].items():
        if digest((args.dataset / name).read_bytes()) != expected:
            raise ValueError("Frozen input checksum mismatch")
    snapshot = lock["snapshot"]
    if digest(SYSTEM_PROMPT.encode()) != snapshot["prompt_sha256"]:
        raise ValueError("Prompt changed")
    if digest(encoded(Toolbox(None).schemas())) != snapshot["tool_schema_sha256"]:
        raise ValueError("Tool schemas changed")
    rows = [json.loads(x) for x in (args.dataset / "questions.jsonl").read_text().splitlines()]
    probes = [json.loads(x) for x in args.probes.read_text().splitlines()]
    if len(rows) != 30 or len(probes) != 6:
        raise ValueError("Expected the original 30+6 cases")
    users = {r["source_user_id"]: r for r in rows}
    report = json.loads((args.credentials / "report.json").read_text())
    credentials = {}
    for u in report["users"]:
        expected = users[u["user_id"]]
        if u["api_user_id"] != expected["api_user_id"]:
            raise ValueError("Synthetic identity mismatch")
        credentials[u["user_id"]] = json.loads(
            (args.credentials / u["credentials_file"]).read_text()
        )
    if any(p["source_user_id"] not in credentials for p in probes):
        raise ValueError("Unknown probe account")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    metadata = {
        "status": "running",
        "revision": args.revision,
        "execution": "cloud_run_http",
        "reference_mode": "cloud_corpus_enabled",
        "dataset_lock_sha256": digest((args.dataset / "lock.json").read_bytes()),
        "probes_sha256": digest(args.probes.read_bytes()),
        "questions_sha256": digest((args.dataset / "questions.jsonl").read_bytes()),
        "comparison_limit": (
            "Different host/server version and enabled RAG; not a causal LangGraph experiment"
        ),
    }
    (args.output / "run.json").write_bytes(encoded(metadata))
    iam = subprocess.check_output(["gcloud", "auth", "print-identity-token"], text=True).strip()
    headers = {
        uid: {
            "X-Serverless-Authorization": "Bearer " + iam,
            "Authorization": "Bearer " + c["token"],
        }
        for uid, c in credentials.items()
    }
    results = []
    with httpx.Client(timeout=185) as client:
        for uid, h in headers.items():
            response = client.get(args.url + "/v1/me", headers=h)
            response.raise_for_status()
            if response.json()["id"] != users[uid]["api_user_id"]:
                raise ValueError("HTTP identity mismatch")
        for item in rows + probes:
            source = users[item["source_user_id"]]
            body = {
                "question": item["question"],
                "timezone": source["timezone"],
                "today": source["today"],
                "provider": item.get("request_provider"),
            }
            started = time.monotonic()
            response = client.post(
                args.url + "/v1/assistant/ask",
                headers=headers[item["source_user_id"]],
                json=body,
            )
            if response.status_code == 429:
                time.sleep(min(61, max(1, int(response.headers.get("retry-after", "60")))))
                response = client.post(
                    args.url + "/v1/assistant/ask",
                    headers=headers[item["source_user_id"]],
                    json=body,
                )
            response.raise_for_status()
            parsed = AskResponse.model_validate(response.json())
            result = (
                grade(item, parsed)
                if "expected" in item
                else {
                    "id": item["id"],
                    "exploratory": True,
                    "status": parsed.status,
                    "explanation_status": parsed.explanation_status,
                    "tools": [c.name for c in parsed.tool_calls],
                    "answer": parsed.answer,
                }
            )
            result["latency_s"] = round(time.monotonic() - started, 3)
            results.append(result)
            (args.output / (item["id"] + ".json")).write_bytes(
                encoded(
                    {
                        "question": item["question"],
                        "today": source["today"],
                        "grade": result,
                        "response": parsed.model_dump(mode="json"),
                    }
                )
            )
            (args.output / "results.json").write_bytes(encoded(results))
            print(
                f"{item['id']}: {parsed.status}, {parsed.explanation_status}",
                flush=True,
            )
    scored = results[:30]
    summary = {
        "questions": 30,
        "probes": 6,
        "verified_explanations": sum(r["explanation_status"] == "verified" for r in scored),
        "latency_p50_s": statistics.median(r["latency_s"] for r in scored),
    }
    for field in (
        "tool_selected_ok",
        "raw_arguments_ok",
        "executed_arguments_ok",
        "gold_evidence_ok",
        "refusal_ok",
    ):
        values = [r[field] for r in scored if r[field] is not None]
        summary[field] = {"passed": sum(bool(v) for v in values), "total": len(values)}
    (args.output / "summary.json").write_bytes(encoded(summary))
    metadata["status"] = "completed"
    (args.output / "run.json").write_bytes(encoded(metadata))
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"Cloud evaluation stopped: {type(exc).__name__}; inspect private artifacts."
        ) from None
