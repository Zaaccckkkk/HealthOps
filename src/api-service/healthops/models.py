import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(sa.String(100))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(server_default=sa.false())


class Observation(Base):
    __tablename__ = "health_observations"
    __table_args__ = (
        sa.UniqueConstraint(
            "user_id", "provider", "source_record_id", "metric", name="uq_source_record"
        ),
        sa.CheckConstraint("ended_at >= started_at", name="ck_observation_time_order"),
        sa.CheckConstraint(
            "value >= 0 AND value <> 'NaN'::numeric", name="ck_observation_nonnegative"
        ),
        sa.Index("ix_observations_user_time", "user_id", "started_at", "id"),
        sa.Index("ix_observations_user_metric_time", "user_id", "metric", "started_at"),
        # Bounds the overlap-context scan of the daily-summary tool (intervals ending after a
        # window start) without reading the whole history of a metric.
        sa.Index("ix_observations_user_metric_end", "user_id", "metric", "ended_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(sa.String(30))
    source_record_id: Mapped[str] = mapped_column(sa.String(300))
    source_device: Mapped[str | None] = mapped_column(sa.String(150))
    metric: Mapped[str] = mapped_column(sa.String(40))
    value: Mapped[Decimal] = mapped_column(sa.Numeric(20, 6))
    unit: Mapped[str] = mapped_column(sa.String(12))
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    source_timezone: Mapped[str] = mapped_column(sa.String(80))
    aggregation: Mapped[str] = mapped_column(sa.String(30))
    payload_hash: Mapped[str] = mapped_column(sa.String(64))
    ingested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class ProviderConnection(Base):
    """A user's authorisation for a server-side connector (today: Google Health).

    The refresh token is stored encrypted with a key that lives only in the server
    environment. Deleting the row is "disconnect"; the connector revokes the token at the
    provider first. Synced observations are separate rows and are deleted by a separate,
    explicit action.
    """

    __tablename__ = "provider_connections"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "provider", name="uq_connection_user_provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(sa.String(30))
    encrypted_refresh_token: Mapped[str] = mapped_column(sa.Text)
    scopes: Mapped[str] = mapped_column(sa.Text)
    external_user_id: Mapped[str | None] = mapped_column(sa.String(100))
    legacy_user_id: Mapped[str | None] = mapped_column(sa.String(100))
    connected_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # JSON text: per data type, the end of the last fully synced window (ISO instant).
    cursors: Mapped[str] = mapped_column(sa.Text, server_default="{}")
    # JSON text: the last sync report (counts and record ids only, never values).
    last_sync_report: Mapped[str | None] = mapped_column(sa.Text)


class ConnectionSession(Base):
    """One-time OAuth state bound to a user; expires quickly and is consumed on callback."""

    __tablename__ = "connection_sessions"

    state: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(sa.String(30))
    timezone: Mapped[str] = mapped_column(sa.String(80))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # Outcome written by the callback so the app can read it with its own bearer token.
    outcome: Mapped[str | None] = mapped_column(sa.String(40))


class ReferenceChunk(Base):
    """One retrievable passage of a reference source (knowledge base).

    Source metadata is copied onto every chunk so a retrieved passage carries its own licence
    and quotation rule; the answer templates read `quote_limit_chars` to decide how much text
    may be quoted verbatim. Embeddings are nomic-embed-text (768 dimensions); a different model
    requires re-ingesting everything, which is why the model name is stored per row.
    """

    __tablename__ = "reference_chunks"
    __table_args__ = (
        sa.UniqueConstraint("source_id", "chunk_index", name="uq_reference_chunk"),
        sa.Index("ix_reference_chunks_source", "source_id"),
        # Declared here as well so the migration/model consistency test sees the HNSW index.
        sa.Index(
            "ix_reference_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(sa.String(80))
    source_title: Mapped[str] = mapped_column(sa.String(300))
    publisher: Mapped[str] = mapped_column(sa.String(200))
    url: Mapped[str] = mapped_column(sa.String(500))
    license: Mapped[str] = mapped_column(sa.String(200))
    allowed_use: Mapped[str] = mapped_column(sa.Text)
    quote_limit_chars: Mapped[int] = mapped_column(sa.Integer)
    section: Mapped[str] = mapped_column(sa.String(300))
    page: Mapped[int | None] = mapped_column(sa.Integer)
    chunk_index: Mapped[int] = mapped_column(sa.Integer)
    text: Mapped[str] = mapped_column(sa.Text)
    topics: Mapped[str] = mapped_column(sa.String(300), server_default="")
    embedding_model: Mapped[str] = mapped_column(sa.String(80))
    embedding = mapped_column(Vector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class AssistantConversationState(Base):
    """Short-lived clarification state, never tool results or model reasoning."""

    __tablename__ = "assistant_conversations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(sa.Integer())
    pending: Mapped[bool] = mapped_column(sa.Boolean())
    payload: Mapped[dict] = mapped_column(sa.JSON())
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
