"""Run routed multi-turn diagnostics through an IAM-protected evaluation revision.

Use the API environment and PYTHONPATH=src/api-service:src/api-service/scripts.
Gold is read only from the explicitly named synthetic Cloud SQL tunnel. No seeding.
"""

import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path
from uuid import UUID

import httpx
from eval_routed import (
    ROOT,
    digest,
    evaluate_case,
    identities,
    load_cases,
    make_gold,
    summarize,
    write,
)
from seed_synthetic import validate_target
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from healthops.assistant.contracts import AskResponse
from healthops.assistant.prompts import SYSTEM_PROMPT
from healthops.assistant.routing import ROUTER_PROMPT
from healthops.db import build_engine
from healthops.models import User


def command_output(*args, **kwargs):
    """Synchronous preflight only; called before HTTP evaluation begins."""
    return subprocess.check_output(*args, **kwargs)


class HTTPAssistant:
    """Use the real HTTP continuation protocol; never retry an ambiguous consumed reply."""

    def __init__(self, client, url, headers):
        self.client, self.url, self.headers = client, url.rstrip("/"), headers

    async def ask(self, request, user_id):
        response = await self.client.post(
            self.url + "/v1/assistant/ask",
            headers=self.headers[user_id],
            json=request.model_dump(mode="json", exclude_none=True),
        )
        # A 429 is returned before execution. Network timeouts and 5xx are NOT replayed:
        # the server may already have consumed the conversation version.
        if response.status_code == 429:
            delay = min(60, max(1, int(response.headers.get("retry-after", "60"))))
            await asyncio.sleep(delay)
            response = await self.client.post(
                self.url + "/v1/assistant/ask",
                headers=self.headers[user_id],
                json=request.model_dump(mode="json", exclude_none=True),
            )
        response.raise_for_status()
        return AskResponse.model_validate(response.json())


def credential_headers(directory, users, iam):
    report = json.loads((directory / "report.json").read_text())
    result = {}
    for user in report["users"]:
        uid = users[user["user_id"]]
        if UUID(user["api_user_id"]) != uid:
            raise ValueError("Credential identity differs from frozen synthetic account")
        path = (directory / user["credentials_file"]).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Credential path is outside the private directory")
        token = json.loads(path.read_text())["token"]
        result[uid] = {
            "Authorization": "Bearer " + token,
            "X-Serverless-Authorization": "Bearer " + iam,
        }
    if set(result) != set(users.values()):
        raise ValueError("Missing synthetic account credentials")
    return result


def describe_cloud_run(args, resource: str, name: str):
    """Inspect only the operator-selected project; never an embedded team account."""
    return json.loads(
        command_output(
            [
                "gcloud",
                "run",
                resource,
                "describe",
                name,
                f"--region={args.region}",
                f"--project={args.project}",
                "--format=json",
            ],
            text=True,
        )
    )


async def run(args):
    url = args.database_url_file.read_text().strip()
    validate_target(url, args.url)
    if not args.output.resolve().is_relative_to((ROOT / ".local/eval").resolve()):
        raise ValueError("Reports must stay under api-service/.local/eval")
    cases = load_cases(args.cases)
    if args.ids:
        selected = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in selected]
        if {c["id"] for c in cases} != selected:
            raise ValueError("Unknown scenario ID")
    engine = build_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False)
    users = identities(args.dataset, factory)
    with factory() as session:
        if set(session.scalars(select(User.id))) != set(users.values()):
            raise ValueError("Database must contain exactly the three synthetic accounts")
    gold = {
        c["id"]: [make_gold(c, t, factory, users[c["source_user_id"]]) for t in c["turns"]]
        for c in cases
    }
    engine.dispose()
    iam = command_output(["gcloud", "auth", "print-identity-token"], text=True).strip()
    headers = credential_headers(args.credentials, users, iam)
    revision = describe_cloud_run(args, "revisions", args.revision)
    container = revision["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container.get("env", [])}
    if env.get("HEALTHOPS_ASSISTANT_ALLOW_TODAY_OVERRIDE") != "true":
        raise ValueError("Expected an evaluation-only revision with frozen dates")
    service = describe_cloud_run(args, "services", args.service)
    traffic = service["status"]["traffic"]
    if not any(
        t.get("revisionName") == args.revision
        and t.get("url") == args.url
        and not t.get("percent", 0)
        for t in traffic
    ):
        raise ValueError("URL must point to the tagged, zero-default-traffic evaluation revision")
    if any(t.get("revisionName") == args.revision and t.get("percent", 0) for t in traffic):
        raise ValueError("Evaluation revision must not receive normal traffic")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    metadata = {
        "status": "running",
        "execution": "cloud_run_http",
        "revision": args.revision,
        "image": container["image"],
        "cases_sha256": digest(args.cases.read_bytes()),
        "cohort_lock_sha256": digest((args.dataset / "lock.json").read_bytes()),
        "local_router_prompt_sha256": digest(ROUTER_PROMPT.encode()),
        "local_execution_prompt_sha256": digest(SYSTEM_PROMPT.encode()),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "git_head": command_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "grading_scope": "reference cases routing only; remaining cases end-to-end",
        "trace_limit": "HTTP responses contain tool/verification traces, not raw router proposals",
    }
    write(args.output / "run.json", metadata)
    write(args.output / "gold.json", gold)
    results = []
    try:
        async with httpx.AsyncClient(timeout=190, trust_env=False) as client:
            for uid, h in headers.items():
                response = await client.get(args.url + "/v1/me", headers=h)
                response.raise_for_status()
                if response.json()["id"] != str(uid):
                    raise ValueError("Authenticated HTTP identity mismatch")
            assistant = HTTPAssistant(client, args.url, headers)
            refreshed_at = time.monotonic()
            for case in cases:
                if time.monotonic() - refreshed_at > 1800:
                    iam = (
                        await asyncio.to_thread(
                            command_output,
                            ["gcloud", "auth", "print-identity-token"],
                            text=True,
                        )
                    ).strip()
                    assistant.headers = credential_headers(args.credentials, users, iam)
                    refreshed_at = time.monotonic()
                result = await evaluate_case(
                    case, users[case["source_user_id"]], assistant, gold[case["id"]]
                )
                results.append(result)
                write(args.output / f"{case['id']}.json", result)
                write(args.output / "summary.json", summarize(results, len(cases)))
                print(
                    f"{case['id']}: {'PASS' if result['passed'] else 'FAIL'}",
                    flush=True,
                )
                if any("error_type" in t for t in result["turns"]):
                    raise RuntimeError("HTTP transport failure; run stopped for diagnosis")
        metadata["status"] = "completed"
    except BaseException:
        metadata["status"] = "interrupted"
        raise
    finally:
        write(args.output / "run.json", metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("dataset", "credentials", "database-url-file", "output"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "eval/assistant_routed.synthetic.jsonl"
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-east4")
    parser.add_argument("--service", default="healthops-ms3-api")
    parser.add_argument(
        "--ids", help="Comma-separated smoke-test scenario IDs; omit for full suite"
    )
    asyncio.run(run(parser.parse_args()))
