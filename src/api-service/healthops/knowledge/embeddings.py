"""Embeddings through Ollama's /api/embed. The same client serves ingestion and queries."""

from __future__ import annotations

import httpx


class EmbeddingError(Exception):
    pass


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str,
        model: str,
        dimensions: int,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self._http = httpx.Client(timeout=timeout_seconds, transport=transport)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._http.post(
            f"{self.base_url}/api/embed",
            # nomic-embed-text supports 8192 tokens but serves 2048 by default; raise it so a
            # long chunk is never truncated silently.
            json={"model": self.model, "input": texts, "options": {"num_ctx": 8192}},
        )
        if response.status_code >= 400:
            raise EmbeddingError(f"embedding request failed: HTTP {response.status_code}")
        vectors = response.json().get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingError("embedding response shape unexpected")
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise EmbeddingError(
                    f"embedding has {len(vector)} dimensions, expected {self.dimensions}"
                )
        return vectors
