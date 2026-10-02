"""Index observations by (user_id, metric, ended_at) for interval overlap context queries.

Additive only: no table or data changes. Downgrade drops the index.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_observations_user_metric_end",
        "health_observations",
        ["user_id", "metric", "ended_at"],
    )


def downgrade():
    op.drop_index("ix_observations_user_metric_end", table_name="health_observations")
