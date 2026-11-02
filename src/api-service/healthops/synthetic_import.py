"""Local operator import of a versioned synthetic cohort; dry-run unless --apply.

Uses the same ObservationIn and ingest_batch as HTTP ingestion. Account provisioning is
operator-only; no public registration or caller-supplied user ID is added to the API.
Install the service's optional 'synthetic' extra to read parquet and YAML manifests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from healthops.auth import hash_token
from healthops.cli import issue_token
from healthops.db import build_engine
from healthops.models import ApiToken, User
from healthops.repository import SourceConflict, ingest_batch
from healthops.schemas import ObservationIn

NAMESPACE = UUID("933fdc20-3304-50e7-8367-c7a8f5368c0b")
COHORTS = ("apple_watch_only", "fitbit_only", "paired")
FILES = ("users.parquet", "observations_healthkit.parquet", "observations_google_health.parquet")
FIELDS = tuple(ObservationIn.model_fields)


class ImportFailure(ValueError):
    """Safe, value-free diagnostic suitable for operator output."""


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def local_database(url: str) -> None:
    parsed = make_url(url)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.host not in {"127.0.0.1", "localhost", "::1", "db", "test-db"}
        or parsed.database not in {"healthops", "healthops_test"}
        or parsed.query
    ):
        raise ImportFailure("Import is limited to the local healthops or healthops_test database.")


def atomic_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".import-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def verify_source(dataset: Path, manifest_path: Path) -> tuple[str, str]:
    import yaml

    manifest = yaml.safe_load(manifest_path.read_text())
    version = manifest.get("version", "")
    if (
        not isinstance(version, str)
        or not re.fullmatch(r"synthetic-[A-Za-z0-9_.-]+", version)
        or manifest.get("stage") != "data-synthesizer"
        or dataset.name != version
    ):
        raise ImportFailure("Expected a synthetic-* dataset and data-synthesizer manifest.")
    entries = manifest.get("files", [])
    for name in FILES:
        matching = [entry for entry in entries if entry.get("path") == name]
        path = dataset / name
        if len(matching) != 1 or not path.is_file():
            raise ImportFailure(f"Missing or ambiguous manifest entry/file: {name}.")
        entry = matching[0]
        if path.stat().st_size != entry.get("bytes") or digest(path) != entry.get("sha256"):
            raise ImportFailure(f"Manifest checksum mismatch: {name}.")
    return version, digest(manifest_path)


def select_users(dataset: Path, user_ids: list[str], per_cohort: int) -> list[dict]:
    import pyarrow.parquet as pq

    users = pq.read_table(dataset / "users.parquet", columns=["user_id", "cohort"]).to_pylist()
    ids = [user["user_id"] for user in users]
    if any(not isinstance(uid, str) or not uid for uid in ids) or len(set(ids)) != len(ids):
        raise ImportFailure("Cohort user IDs must be nonempty, unique strings.")
    if any(user["cohort"] not in COHORTS for user in users):
        raise ImportFailure("Unknown cohort label in users.parquet.")
    if user_ids:
        if len(set(user_ids)) != len(user_ids) or not set(user_ids).issubset(ids):
            raise ImportFailure("Requested user IDs must be unique and present in users.parquet.")
        return sorted([u for u in users if u["user_id"] in user_ids], key=lambda u: u["user_id"])
    selected = []
    for cohort in COHORTS:
        members = sorted([u for u in users if u["cohort"] == cohort], key=lambda u: u["user_id"])
        selected.extend(members[:per_cohort])
    if not selected:
        raise ImportFailure("No synthetic users selected.")
    return selected


def prepare(
    dataset: Path,
    manifest: Path,
    spool,
    *,
    user_ids: list[str],
    per_cohort: int,
    max_records: int,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    """Validate all selected rows before any DB writes; spool in bounded memory."""
    import pyarrow.dataset as ds

    if per_cohort < 1 or max_records < 1 or (start and end and start > end):
        raise ImportFailure("Counts must be positive and start-date must not follow end-date.")
    version, manifest_hash = verify_source(dataset, manifest)
    users = select_users(dataset, user_ids, per_cohort)
    by_id = {}
    for user in users:
        api_id = str(uuid5(NAMESPACE, f"{version}:{manifest_hash}:{user['user_id']}"))
        by_id[user["user_id"]] = {
            **user,
            "api_user_id": api_id,
            "display_name": f"Synthetic {user['cohort']} {api_id[:8]}",
            "records": 0,
            "series": Counter(),
            "first_date": None,
            "last_date": None,
            "timezones": set(),
        }
    count = 0
    for name, provider in zip(FILES[1:], ("healthkit", "google_health"), strict=True):
        source = ds.dataset(dataset / name, format="parquet")
        columns = ["user_id", "provenance_dataset", "provenance_version", *FIELDS]
        scanner = source.scanner(
            columns=columns, filter=ds.field("user_id").isin(list(by_id)), batch_size=4096
        )
        for batch in scanner.to_batches():
            for row in batch.to_pylist():
                user = by_id[row["user_id"]]
                if (
                    row["provenance_dataset"] != "synthetic"
                    or row["provenance_version"] != version
                    or row["provider"] != provider
                    or (user["cohort"] == "apple_watch_only" and provider != "healthkit")
                    or (user["cohort"] == "fitbit_only" and provider != "google_health")
                ):
                    raise ImportFailure(
                        "Selected row has inconsistent synthetic provenance/provider."
                    )
                try:
                    observation = ObservationIn.model_validate({key: row[key] for key in FIELDS})
                except ValueError:
                    raise ImportFailure(
                        "Selected row violates ObservationIn; no import started."
                    ) from None
                day = observation.started_at.astimezone(
                    ZoneInfo(observation.source_timezone)
                ).date()
                if (start and day < start) or (end and day > end):
                    continue
                count += 1
                if count > max_records:
                    raise ImportFailure("Selected data exceeds max-records; narrow users or dates.")
                user["records"] += 1
                user["series"][f"{provider}/{observation.metric}/{observation.aggregation}"] += 1
                user["timezones"].add(observation.source_timezone)
                iso_day = day.isoformat()
                user["first_date"] = min(user["first_date"] or iso_day, iso_day)
                user["last_date"] = max(user["last_date"] or iso_day, iso_day)
                spool.write(
                    json.dumps(
                        {
                            "user_id": row["user_id"],
                            "observation": observation.model_dump(mode="json"),
                        }
                    )
                    + "\n"
                )
    if not count:
        raise ImportFailure("The selected users/date window contains no observations.")
    for user in users:
        item = by_id[user["user_id"]]
        item["series"] = dict(item["series"])
        item["timezones"] = sorted(item["timezones"])
    spool.flush()
    spool.seek(0)
    return {
        "version": version,
        "manifest_sha256": manifest_hash,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "validated",
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "records": count,
        "inserted": 0,
        "duplicates": 0,
        "users": list(by_id.values()),
    }


def provision(session: Session, user: dict, output: Path) -> None:
    """Only use importer-owned IDs; never attach the cohort to an existing personal user."""
    uid = UUID(user["api_user_id"])
    existing = session.get(User, uid)
    if existing is not None and existing.display_name != user["display_name"]:
        raise ImportFailure("Synthetic account identity collision; refusing to reuse the account.")
    if existing is None:
        if output.exists():
            raise ImportFailure("Credentials exist but the matching database account is absent.")
        session.add(User(id=uid, display_name=user["display_name"]))
        session.commit()
    if output.exists():
        credentials = json.loads(output.read_text())
        token = session.scalar(
            select(ApiToken).where(
                ApiToken.user_id == uid,
                ApiToken.token_hash == hash_token(credentials.get("token", "")),
            )
        )
        if credentials.get("user_id") != str(uid) or token is None:
            raise ImportFailure("Credential file does not match its synthetic database account.")
        user["credential_status"] = (
            "valid"
            if not token.revoked and token.expires_at > datetime.now(UTC)
            else "expired_or_revoked"
        )
    else:
        issue_token(session, uid, output)
        user["credential_status"] = "created"
    user["credentials_file"] = output.name


def apply_import(engine: Engine, report: dict, spool, output_dir: Path, batch_size: int = 500):
    if not 1 <= batch_size <= 500:
        raise ImportFailure("Batch size must be between 1 and 500.")
    local_database(str(engine.url.render_as_string(hide_password=False)))
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.json"
    report["status"] = "running"
    atomic_report(report_path, report)
    users = {u["user_id"]: u for u in report["users"]}
    pending: dict[str, list[ObservationIn]] = {}
    try:
        with Session(engine) as session:
            for user in users.values():
                if user["records"]:
                    provision(session, user, output_dir / f"{user['api_user_id']}.json")
            atomic_report(report_path, report)

            def flush(source_id: str):
                rows = pending[source_id]
                if not rows:
                    return
                result = ingest_batch(session, UUID(users[source_id]["api_user_id"]), rows)
                report["inserted"] += result.inserted
                report["duplicates"] += result.duplicates
                rows.clear()
                atomic_report(report_path, report)

            spool.seek(0)
            for line in spool:
                row = json.loads(line)
                uid = row["user_id"]
                pending.setdefault(uid, []).append(ObservationIn.model_validate(row["observation"]))
                if len(pending[uid]) >= batch_size:
                    flush(uid)
            for uid in pending:
                flush(uid)
        report["status"] = "completed"
    except Exception:
        report["status"] = "failed"
        atomic_report(report_path, report)
        raise
    atomic_report(report_path, report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(".local/synthetic-import"))
    parser.add_argument("--user-id", action="append", default=[])
    parser.add_argument("--users-per-cohort", type=int, default=1)
    parser.add_argument("--max-records", type=int, default=100_000)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--apply", action="store_true", help="Write to the local DB after validation"
    )
    args = parser.parse_args(argv)
    engine = None
    try:
        if not 1 <= args.batch_size <= 500:
            raise ImportFailure("Batch size must be between 1 and 500.")
        with tempfile.TemporaryFile(mode="w+") as spool:
            report = prepare(
                args.dataset,
                args.manifest,
                spool,
                user_ids=args.user_id,
                per_cohort=args.users_per_cohort,
                max_records=args.max_records,
                start=args.start_date,
                end=args.end_date,
            )
            if args.apply:
                # Deliberately ignore .env and HEALTHOPS_DATABASE_URL: neither may silently
                # redirect an import into the cloud database holding personal records.
                url = os.environ.get(
                    "HEALTHOPS_SYNTHETIC_DATABASE_URL",
                    "postgresql+psycopg://healthops:local-only-password@127.0.0.1:55432/healthops",
                )
                local_database(url)
                engine = build_engine(url)
                apply_import(engine, report, spool, args.output_dir, args.batch_size)
            else:
                atomic_report(args.output_dir / "dry-run.json", report)
            print(
                json.dumps(
                    {key: report[key] for key in ("status", "records", "inserted", "duplicates")}
                )
            )
            print(f"Local report directory: {args.output_dir}")
        return 0
    except ImportFailure as exc:
        print(f"Import stopped: {exc}")
    except SourceConflict:
        print("Import stopped: source identity conflict. Earlier batches remain; no overwrite.")
    except Exception as exc:
        # DB/validation exceptions can include credentials or values: do not print their text.
        print(f"Import stopped: {type(exc).__name__}. Check inputs, dependencies and local DB.")
    finally:
        if engine is not None:
            engine.dispose()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
