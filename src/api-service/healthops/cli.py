"""Operator-only local bootstrap. There is deliberately no public user creation API."""

import argparse
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from healthops.auth import hash_token
from healthops.config import Settings
from healthops.db import build_engine
from healthops.models import ApiToken, User

DEFAULT_TOKEN_DAYS = 7
MAX_TOKEN_DAYS = 90  # long enough for a reviewer token; never an unbounded credential


def token_days(value: str) -> int:
    """argparse type for --days: an integer from 1 to MAX_TOKEN_DAYS."""
    try:
        days = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--days must be an integer") from exc
    if not 1 <= days <= MAX_TOKEN_DAYS:
        raise argparse.ArgumentTypeError(f"--days must be between 1 and {MAX_TOKEN_DAYS}")
    return days


def create_user(session: Session, name: str, output: Path, days: int = DEFAULT_TOKEN_DAYS) -> UUID:
    if not name.strip() or len(name) > 100:
        raise ValueError("Name must contain 1-100 characters.")
    return _write_token(session, User(display_name=name.strip()), output, days)


def issue_token(
    session: Session, user_id: UUID, output: Path, days: int = DEFAULT_TOKEN_DAYS
) -> UUID:
    user = session.get(User, user_id)
    if user is None:
        raise ValueError("User does not exist.")
    return _write_token(session, user, output, days)


def _write_token(
    session: Session, user: User, output: Path, days: int = DEFAULT_TOKEN_DAYS
) -> UUID:
    if not 1 <= days <= MAX_TOKEN_DAYS:
        raise ValueError(f"Token lifetime must be between 1 and {MAX_TOKEN_DAYS} days.")
    # Reserve a new 0600 file; never overwrite existing credentials.
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            session.add(user)
            session.flush()
            raw_token = secrets.token_urlsafe(32)
            token = ApiToken(
                user_id=user.id,
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(days=days),
            )
            session.add(token)
            session.flush()
            json.dump(
                {
                    "user_id": str(user.id),
                    "token_id": str(token.id),
                    "token": raw_token,
                    "expires_at": token.expires_at.isoformat(),
                },
                stream,
                indent=2,
            )
            stream.flush()
            os.fsync(stream.fileno())
            session.commit()
            return token.id
    except Exception:
        session.rollback()
        output.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    days_help = (
        f"token lifetime in days, 1 to {MAX_TOKEN_DAYS} (default {DEFAULT_TOKEN_DAYS}); "
        "use a longer value for a reviewer token"
    )
    create = commands.add_parser("create-user")
    create.add_argument("--name", required=True)
    create.add_argument(
        "--output", type=Path, required=True, help="New local credential file (0600)"
    )
    create.add_argument("--days", type=token_days, default=DEFAULT_TOKEN_DAYS, help=days_help)
    issue = commands.add_parser("issue-token")
    issue.add_argument("--user-id", type=UUID, required=True)
    issue.add_argument("--output", type=Path, required=True)
    issue.add_argument("--days", type=token_days, default=DEFAULT_TOKEN_DAYS, help=days_help)
    revoke = commands.add_parser("revoke-token")
    revoke.add_argument("--token-id", type=UUID, required=True)
    return parser


def main():
    args = build_parser().parse_args()
    settings = Settings()
    settings.require_local()
    engine = build_engine(settings.database_url.get_secret_value())
    try:
        with Session(engine) as session:
            if args.command in {"create-user", "issue-token"}:
                if args.command == "create-user":
                    create_user(session, args.name, args.output, args.days)
                else:
                    issue_token(session, args.user_id, args.output, args.days)
                print(
                    f"Credentials saved to {args.output}; "
                    f"token expires in {args.days} days. Do not commit it."
                )
            else:
                result = session.execute(
                    update(ApiToken).where(ApiToken.id == args.token_id).values(revoked=True)
                )
                session.commit()
                print(f"Revoked {result.rowcount} token(s).")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
