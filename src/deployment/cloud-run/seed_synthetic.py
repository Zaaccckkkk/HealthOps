"""Provision synthetic identities through an operator tunnel, then ingest via Cloud Run.

Run with the API service's Python environment (including the synthetic extra).
No cloud writes occur unless --apply is supplied. Never reads the personal database.
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from healthops.db import build_engine
from healthops.models import User
from healthops.synthetic_import import ImportFailure, atomic_report, prepare, provision


def validate_target(database_url, api_url):
    url = make_url(database_url)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.port != 15433
        or url.database != "healthops_synthetic"
        or dict(url.query) != {"sslmode": "require"}
    ):
        raise ImportFailure("Expected the explicit synthetic Cloud SQL operator tunnel.")
    from urllib.parse import urlparse

    api = urlparse(api_url)
    if (
        api.scheme != "https"
        or not (api.hostname or "").endswith(".run.app")
        or api.username
        or api.password
        or api.query
        or api.fragment
        or api.path not in ("", "/")
        or api.port not in (None, 443)
    ):
        raise ImportFailure("Expected an authenticated Cloud Run HTTPS service URL.")


def upload(report, spool, output, api_url):
    identity = subprocess.check_output(
        ["gcloud", "auth", "print-identity-token"], text=True
    ).strip()
    users = {u["user_id"]: u for u in report["users"]}
    pending = {uid: [] for uid in users}
    tokens = {
        uid: json.loads((output / user["credentials_file"]).read_text())["token"]
        for uid, user in users.items()
    }
    with httpx.Client(timeout=120) as client:

        def flush(uid):
            if not pending[uid]:
                return
            response = client.post(
                api_url.rstrip("/") + "/v1/observations/batch",
                headers={
                    "X-Serverless-Authorization": f"Bearer {identity}",
                    "Authorization": f"Bearer {tokens[uid]}",
                },
                json={"observations": pending[uid]},
            )
            response.raise_for_status()
            result = response.json()
            report["inserted"] += result["inserted"]
            report["duplicates"] += result["duplicates"]
            pending[uid].clear()
            atomic_report(output / "report.json", report)

        spool.seek(0)
        for line in spool:
            row = json.loads(line)
            pending[row["user_id"]].append(row["observation"])
            if len(pending[row["user_id"]]) == 500:
                flush(row["user_id"])
        for uid in users:
            flush(uid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database-url-file", type=Path, required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database_url = args.database_url_file.read_text().strip()
    validate_target(database_url, args.api_url)
    with tempfile.TemporaryFile(mode="w+") as spool:
        report = prepare(
            args.dataset,
            args.manifest,
            spool,
            user_ids=[],
            per_cohort=1,
            max_records=100_000,
        )
        if not args.apply:
            atomic_report(args.output_dir / "dry-run.json", report)
            print(json.dumps({"status": "validated", "records": report["records"]}))
            return
        engine = build_engine(database_url)
        try:
            with Session(engine) as session:
                expected = {UUID(u["api_user_id"]) for u in report["users"]}
                if not set(session.scalars(select(User.id))).issubset(expected):
                    raise ImportFailure(
                        "Database contains identities outside this synthetic cohort."
                    )
                for user in report["users"]:
                    provision(session, user, args.output_dir / f"{user['api_user_id']}.json")
            report["status"] = "running"
            atomic_report(args.output_dir / "report.json", report)
            try:
                upload(report, spool, args.output_dir, args.api_url)
                report["status"] = "completed"
            except Exception:
                report["status"] = "failed"
                raise
            finally:
                atomic_report(args.output_dir / "report.json", report)
        finally:
            engine.dispose()
        print(json.dumps({k: report[k] for k in ("status", "records", "inserted", "duplicates")}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        # Driver errors can contain credentials; never print exception text or a traceback.
        print(f"Synthetic cloud import stopped: {type(exc).__name__}.")
        raise SystemExit(1) from None
