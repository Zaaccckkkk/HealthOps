"""Ingest the knowledge base: manifest sources (downloaded locally) and knowledge/own/*.md."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from healthops.knowledge.embeddings import OllamaEmbedder
from healthops.knowledge.store import replace_source
from healthops.knowledge.text import chunk_passages, extract
from healthops.models import ReferenceChunk

# Sources that only allow short quotation are stored in full for retrieval but quoted up to
# this many characters; the rest of the chunk is never shown.
SHORT_QUOTE_CHARS = 300
FULL_QUOTE_CHARS = 1800


@dataclass
class SourceSpec:
    id: str
    title: str
    publisher: str
    url: str
    license: str
    allowed_use: str
    kind: str
    path: Path
    topics: list[str]

    @property
    def quote_limit_chars(self) -> int:
        return SHORT_QUOTE_CHARS if "short" in self.allowed_use.lower() else FULL_QUOTE_CHARS


def own_document_specs(own_dir: Path, repo_url: str) -> list[SourceSpec]:
    specs = []
    for path in sorted(own_dir.glob("*.md")):
        title = path.read_text().splitlines()[0].lstrip("#").strip()
        specs.append(
            SourceSpec(
                id=f"own-{path.stem}",
                title=title,
                publisher="HealthOps project",
                url=f"{repo_url}/knowledge/own/{path.name}",
                license="Project licence",
                allowed_use="verbatim quotation",
                kind="markdown",
                path=path,
                topics=["healthops"],
            )
        )
    return specs


def ingest_source(
    session: Session, spec: SourceSpec, embedder: OllamaEmbedder, batch_size: int = 16
) -> int:
    passages = chunk_passages(extract(spec.path, spec.kind))
    chunks: list[ReferenceChunk] = []
    for start in range(0, len(passages), batch_size):
        batch = passages[start : start + batch_size]
        vectors = embedder.embed([p.text for p in batch])
        for offset, (passage, vector) in enumerate(zip(batch, vectors, strict=True)):
            chunks.append(
                ReferenceChunk(
                    source_id=spec.id,
                    source_title=spec.title[:300],
                    publisher=spec.publisher[:200],
                    url=spec.url[:500],
                    license=spec.license[:200],
                    allowed_use=spec.allowed_use,
                    quote_limit_chars=spec.quote_limit_chars,
                    section=passage.section[:300],
                    page=passage.page,
                    chunk_index=start + offset,
                    text=passage.text,
                    topics=",".join(spec.topics)[:300],
                    embedding_model=embedder.model,
                    embedding=vector,
                )
            )
    return replace_source(session, spec.id, chunks)
