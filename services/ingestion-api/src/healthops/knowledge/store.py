"""Insert and search reference chunks in PostgreSQL with pgvector."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from healthops.models import ReferenceChunk


@dataclass
class Hit:
    chunk: ReferenceChunk
    similarity: float  # cosine similarity in [-1, 1]


def replace_source(session: Session, source_id: str, chunks: list[ReferenceChunk]) -> int:
    """Replace every chunk of one source atomically; returns the number stored."""
    session.execute(delete(ReferenceChunk).where(ReferenceChunk.source_id == source_id))
    session.add_all(chunks)
    session.commit()
    return len(chunks)


def search(
    session: Session,
    query_embedding: list[float],
    top_k: int,
    min_similarity: float,
    embedding_model: str,
) -> list[Hit]:
    """Nearest chunks by cosine distance, restricted to the current embedding model."""
    distance = ReferenceChunk.embedding.cosine_distance(query_embedding)
    rows = session.execute(
        select(ReferenceChunk, distance.label("distance"))
        .where(ReferenceChunk.embedding_model == embedding_model)
        .order_by(distance)
        .limit(top_k)
    ).all()
    hits = [Hit(chunk=row[0], similarity=1.0 - float(row[1])) for row in rows]
    return [hit for hit in hits if hit.similarity >= min_similarity]


def count_by_source(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(ReferenceChunk.source_id, func.count()).group_by(ReferenceChunk.source_id)
    ).all()
    return {source_id: int(count) for source_id, count in rows}
