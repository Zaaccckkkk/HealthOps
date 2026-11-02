"""pgvector store and the lookup_reference tool against PostgreSQL with a fake embedder."""

import httpx
import pytest
from sqlalchemy.orm import Session

from healthops.assistant.toolbox import (
    KnowledgeContext,
    ReferenceLookupParams,
    lookup_reference_tool,
)
from healthops.knowledge.embeddings import OllamaEmbedder
from healthops.knowledge.ingest import SourceSpec, ingest_source
from healthops.knowledge.store import count_by_source, search
from healthops.models import ReferenceChunk

pytestmark = pytest.mark.integration

DIMS = 768


def fake_vector(text: str) -> list[float]:
    """Deterministic 768-d vector: one coordinate per topic word, so cosine similarity is high
    for texts sharing a topic and near zero otherwise."""
    topics = ["sleep", "heart", "steps", "sdnn", "rmssd", "stocks"]
    vector = [0.0] * DIMS
    lowered = text.lower()
    for index, topic in enumerate(topics):
        if topic in lowered:
            vector[index] = 1.0
    if not any(vector):
        vector[DIMS - 1] = 1.0
    return vector


class FakeEmbedderTransport(httpx.BaseTransport):
    def handle_request(self, request):
        import json

        body = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [fake_vector(t) for t in body["input"]]})


@pytest.fixture
def embedder():
    return OllamaEmbedder(
        "http://fake-embed.test", "nomic-embed-text", DIMS, transport=FakeEmbedderTransport()
    )


def spec(tmp_path, name, text, allowed="verbatim quotation with link"):
    path = tmp_path / f"{name}.md"
    path.write_text(f"# {name}\n\n{text}\n")
    return SourceSpec(
        name,
        name.title(),
        "Test publisher",
        f"https://example.test/{name}",
        "Public domain",
        allowed,
        "markdown",
        path,
        ["test"],
    )


def test_ingest_search_and_tool(engine, tmp_path, embedder):
    with Session(engine) as session:
        stored = ingest_source(
            session,
            spec(tmp_path, "sleep-doc", "Adults should sleep seven or more hours. " * 5),
            embedder,
        )
        ingest_source(
            session,
            spec(
                tmp_path,
                "heart-doc",
                "Resting heart rate is measured at rest. " * 5,
                "short verbatim quotation only",
            ),
            embedder,
        )
        assert stored == 1
        counts = count_by_source(session)
        assert counts["sleep-doc"] == 1 and counts["heart-doc"] == 1

        hits = search(session, fake_vector("how much sleep"), 3, 0.35, "nomic-embed-text")
        assert [h.chunk.source_id for h in hits] == ["sleep-doc"] and hits[0].similarity > 0.99

        # The tool returns quotable passages and respects the per-source quotation limit.
        context = KnowledgeContext(embedder=embedder, top_k=3, min_similarity=0.35)
        result = lookup_reference_tool(
            session, None, ReferenceLookupParams(query="heart rate"), context
        )
        assert [r.source_id for r in result.references] == ["heart-doc"]
        assert (
            len(result.references[0].text) <= 300 and result.references[0].quote_limit_chars == 300
        )

        # Unrelated query: nothing above the threshold, explicit warning, no invented passage.
        empty = lookup_reference_tool(
            session, None, ReferenceLookupParams(query="stocks to buy"), context
        )
        assert empty.references == [] and empty.warnings == ["no_reference_found"]

        # Re-ingesting replaces, never duplicates.
        ingest_source(
            session,
            spec(tmp_path, "sleep-doc", "Adults should sleep seven or more hours. " * 5),
            embedder,
        )
        assert count_by_source(session)["sleep-doc"] == 1
        assert (
            session.query(ReferenceChunk).filter_by(source_id="sleep-doc").one().embedding_model
            == "nomic-embed-text"
        )


def test_tool_without_a_knowledge_context_reports_unavailable(engine):
    with Session(engine) as session:
        result = lookup_reference_tool(session, None, ReferenceLookupParams(query="anything"), None)
        assert result.references == [] and result.warnings == ["knowledge_base_unavailable"]
