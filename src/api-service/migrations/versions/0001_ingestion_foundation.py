"""Create users, hashed development tokens, and canonical observations.

This migration is a fixed schema snapshot; never import live model metadata here.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "health_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("source_record_id", sa.String(300), nullable=False),
        sa.Column("source_device", sa.String(150)),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("value", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit", sa.String(12), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_timezone", sa.String(80), nullable=False),
        sa.Column("aggregation", sa.String(30), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "user_id", "provider", "source_record_id", "metric", name="uq_source_record"
        ),
        sa.CheckConstraint("ended_at >= started_at", name="ck_observation_time_order"),
        sa.CheckConstraint(
            "value >= 0 AND value <> 'NaN'::numeric", name="ck_observation_nonnegative"
        ),
    )
    op.create_index(
        "ix_observations_user_time", "health_observations", ["user_id", "started_at", "id"]
    )
    op.create_index(
        "ix_observations_user_metric_time",
        "health_observations",
        ["user_id", "metric", "started_at"],
    )


def downgrade():
    # Explicit operator action only; this drops all data in these tables.
    op.drop_table("health_observations")
    op.drop_table("api_tokens")
    op.drop_table("users")
