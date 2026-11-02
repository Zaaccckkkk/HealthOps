"""Ingest the knowledge base into PostgreSQL (pgvector) using nomic-embed-text via Ollama.

Reads knowledge/sources.yaml (third-party sources must already be downloaded by
fetch_sources.py) and knowledge/own/*.md, chunks them, embeds them and replaces each source's
chunks atomically. Requires HEALTHOPS_DATABASE_URL and HEALTHOPS_ASSISTANT_MODEL_BASE_URL (or
HEALTHOPS_KNOWLEDGE_EMBEDDING_BASE_URL) in the environment, like the API.

    uv run python scripts/knowledge/ingest.py             # everything (from src/api-service/)
    uv run python scripts/knowledge/ingest.py --only own   # project documents only
    make rag                                              # the same two steps in Compose
"""

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy.orm import Session

SERVICE = Path(__file__).resolve().parents[2]  # src/api-service
sys.path.insert(0, str(SERVICE / "scripts/knowledge"))
sys.path.insert(0, str(SERVICE))

from fetch_sources import KNOWLEDGE_DIR, RAW, load_manifest  # noqa: E402

from healthops.config import Settings  # noqa: E402
from healthops.db import build_engine  # noqa: E402
from healthops.knowledge.embeddings import OllamaEmbedder  # noqa: E402
from healthops.knowledge.ingest import SourceSpec, ingest_source, own_document_specs  # noqa: E402
from healthops.knowledge.store import count_by_source  # noqa: E402

# Link shown on reference cards for the project's own notes.
REPO_URL = os.environ.get(
    "HEALTHOPS_KNOWLEDGE_REPO_URL", "https://github.com/HuihanCui/ac215_Megatron/blob/milestone2"
)


def manifest_specs() -> list[SourceSpec]:
    specs = []
    for source in load_manifest():
        topics = [
            t.strip(' "') for t in source.get("topics", "").strip("[]").split(",") if t.strip()
        ]
        specs.append(
            SourceSpec(
                id=source["id"],
                title=source["title"],
                publisher=source["publisher"],
                url=source["url"],
                license=source["license"],
                allowed_use=source["allowed_use"],
                kind=source["kind"],
                path=RAW / source["file"],
                topics=topics,
            )
        )
    return specs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["own", "manifest"], help="restrict to one group")
    args = parser.parse_args()
    settings = Settings()
    if not settings.knowledge_embedding_url:
        sys.exit("Set HEALTHOPS_ASSISTANT_MODEL_BASE_URL (Ollama) for embeddings.")
    embedder = OllamaEmbedder(
        settings.knowledge_embedding_url,
        settings.knowledge_embedding_model,
        settings.knowledge_embedding_dimensions,
    )
    specs: list[SourceSpec] = []
    if args.only != "manifest":
        specs += own_document_specs(KNOWLEDGE_DIR / "own", REPO_URL)
    if args.only != "own":
        specs += manifest_specs()
    engine = build_engine(settings.database_url.get_secret_value())
    with Session(engine) as session:
        for spec in specs:
            if not spec.path.exists():
                print(f"skip     {spec.id}: file missing (run fetch_sources.py)")
                continue
            stored = ingest_source(session, spec, embedder)
            print(
                f"ingested {spec.id}: {stored} chunks "
                f"({spec.license}; quote limit {spec.quote_limit_chars})"
            )
        print("totals:", count_by_source(session))
    return 0


if __name__ == "__main__":
    sys.exit(main())
