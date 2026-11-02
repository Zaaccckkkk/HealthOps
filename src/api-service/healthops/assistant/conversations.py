"""Durable, owner-scoped optimistic updates. Connections never span model calls."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update

from healthops.models import AssistantConversationState as Row


class ConversationConflict(Exception):
    """Missing, expired, closed, foreign, or stale continuation (same public error)."""


class ConversationStore:
    def __init__(self, session_factory):
        self.factory = session_factory

    def load(self, conversation_id: UUID, user_id: UUID, version: int) -> dict:
        with self.factory() as session:
            row = session.scalar(
                select(Row).where(
                    Row.id == conversation_id,
                    Row.user_id == user_id,
                    Row.version == version,
                    Row.pending.is_(True),
                    Row.expires_at > datetime.now(UTC),
                )
            )
            if row is None:
                raise ConversationConflict()
            return dict(row.payload)

    def save(self, conversation_id, user_id, version, payload, pending):
        now = datetime.now(UTC)
        with self.factory.begin() as session:
            session.execute(delete(Row).where(Row.expires_at <= now))
            if conversation_id is None:
                if not pending:
                    return None, None
                conversation_id = uuid4()
                session.add(
                    Row(
                        id=conversation_id,
                        user_id=user_id,
                        version=1,
                        pending=True,
                        payload=payload,
                        expires_at=now + timedelta(hours=1),
                    )
                )
                return conversation_id, 1
            result = session.execute(
                update(Row)
                .where(
                    Row.id == conversation_id,
                    Row.user_id == user_id,
                    Row.version == version,
                    Row.pending.is_(True),
                    Row.expires_at > now,
                )
                .values(version=version + 1, pending=pending, payload=payload if pending else {})
            )
            if result.rowcount != 1:
                raise ConversationConflict()
            return (conversation_id, version + 1) if pending else (None, None)
