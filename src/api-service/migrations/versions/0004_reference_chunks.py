"""Knowledge base: pgvector extension and the reference_chunks table.

Additive only. Requires a PostgreSQL image with the pgvector extension available
(pgvector/pgvector:pg17 in Compose). The HNSW index uses cosine distance, matching the
retrieval query.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "reference_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("source_title", sa.String(300), nullable=False),
        sa.Column("publisher", sa.String(200), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("license", sa.String(200), nullable=False),
        sa.Column("allowed_use", sa.Text(), nullable=False),
        sa.Column("quote_limit_chars", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(300), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("topics", sa.String(300), server_default="", nullable=False),
        sa.Column("embedding_model", sa.String(80), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("source_id", "chunk_index", name="uq_reference_chunk"),
    )
    op.create_index("ix_reference_chunks_source", "reference_chunks", ["source_id"])
    op.execute(
        "CREATE INDEX ix_reference_chunks_embedding ON reference_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade():
    op.drop_index("ix_reference_chunks_embedding", table_name="reference_chunks")
    op.drop_index("ix_reference_chunks_source", table_name="reference_chunks")
    op.drop_table("reference_chunks")
