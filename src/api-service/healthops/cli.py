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


def create_user(session: Session, name: str, output: Path) -> UUID:
    if not name.strip() or len(name) > 100:
        raise ValueError("Name must contain 1-100 characters.")
    return _write_token(session, User(display_name=name.strip()), output)


def issue_token(session: Session, user_id: UUID, output: Path) -> UUID:
    user = session.get(User, user_id)
    if user is None:
        raise ValueError("User does not exist.")
    return _write_token(session, user, output)


def _write_token(session: Session, user: User, output: Path) -> UUID:
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
                expires_at=datetime.now(UTC) + timedelta(days=7),
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user")
    create.add_argument("--name", required=True)
    create.add_argument(
        "--output", type=Path, required=True, help="New local credential file (0600)"
    )
    issue = commands.add_parser("issue-token")
    issue.add_argument("--user-id", type=UUID, required=True)
    issue.add_argument("--output", type=Path, required=True)
    revoke = commands.add_parser("revoke-token")
    revoke.add_argument("--token-id", type=UUID, required=True)
    args = parser.parse_args()
    settings = Settings()
    settings.require_local()
    engine = build_engine(settings.database_url.get_secret_value())
    try:
        with Session(engine) as session:
            if args.command in {"create-user", "issue-token"}:
                if args.command == "create-user":
                    create_user(session, args.name, args.output)
                else:
                    issue_token(session, args.user_id, args.output)
                print(
                    f"Credentials saved to {args.output}; "
                    "token expires in 7 days. Do not commit it."
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
