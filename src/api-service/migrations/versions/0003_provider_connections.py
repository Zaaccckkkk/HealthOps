"""Server-side connector state: provider connections and one-time OAuth sessions.

Additive only: two new tables, no change to existing tables or data.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "provider_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("external_user_id", sa.String(100)),
        sa.Column("legacy_user_id", sa.String(100)),
        sa.Column(
            "connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("cursors", sa.Text(), server_default="{}", nullable=False),
        sa.Column("last_sync_report", sa.Text()),
        sa.UniqueConstraint("user_id", "provider", name="uq_connection_user_provider"),
    )
    op.create_table(
        "connection_sessions",
        sa.Column("state", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(40)),
    )


def downgrade():
    op.drop_table("connection_sessions")
    op.drop_table("provider_connections")
