import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
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
