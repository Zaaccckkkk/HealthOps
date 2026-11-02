import hashlib
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from healthops.db import get_session
from healthops.models import ApiToken, User

bearer = HTTPBearer(auto_error=False)
SessionDep = Annotated[Session, Depends(get_session)]


def hash_token(token: str) -> str:
    # For high-entropy random API tokens, not for human passwords.
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if credentials is not None and len(credentials.credentials) <= 512:
        user = session.scalar(
            select(User)
            .join(ApiToken)
            .where(
                ApiToken.token_hash == hash_token(credentials.credentials),
                ApiToken.revoked.is_(False),
                ApiToken.expires_at > datetime.now(UTC),
            )
        )
        if user is not None:
            return user
    raise HTTPException(
        401, "Invalid or expired credentials.", headers={"WWW-Authenticate": "Bearer"}
    )


UserDep = Annotated[User, Depends(current_user)]
