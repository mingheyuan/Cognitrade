from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.adapters.storage.qdrant_adapter import QdrantAdapter


class _FakeEmbeddingProvider:
    available = True

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self.mapping = mapping

    def embed_texts(self, *, texts: list[str], model: str | None = None) -> list[list[float]]:
        return [list(self.mapping.get(text, [0.0, 0.0, 0.0, 0.0])) for text in texts]


class _FakeQdrantClient:
    def __init__(self, points: list[SimpleNamespace]) -> None:
        self.points = points

    def query_points(self, **kwargs):
        filtered = [point for point in self.points if self._matches(point.payload, kwargs.get("query_filter"))]
        ranked = sorted(filtered, key=lambda item: float(getattr(item, "score", 0.0)), reverse=True)
        return SimpleNamespace(
            points=[
                SimpleNamespace(id=item.id, score=item.score, payload=dict(item.payload))
                for item in ranked[: kwargs.get("limit", len(ranked))]
            ]
        )

    def scroll(self, **kwargs):
        query_filter = kwargs.get("scroll_filter") or kwargs.get("query_filter")
        filtered = [point for point in self.points if self._matches(point.payload, query_filter)]
        return (
            [SimpleNamespace(id=item.id, payload=dict(item.payload)) for item in filtered[: kwargs.get("limit", len(filtered))]],
            None,
        )

    def _matches(self, payload: dict, query_filter) -> bool:
        if query_filter is None:
            return True
        for condition in list(getattr(query_filter, "must", []) or []):
            match = getattr(condition, "match", None)
            actual = payload.get(condition.key)
            expected_any = getattr(match, "any", None)
            if expected_any is not None:
                actual_values = actual if isinstance(actual, list) else [actual]
                if not any(value in expected_any for value in actual_values):
                    return False
                continue
            expected_value = getattr(match, "value", None)
            if actual != expected_value:
                return False
        return True


class QdrantHybridSearchTests(unittest.TestCase):
    def _build_adapter(self, *, points: list[SimpleNamespace], embeddings: dict[str, list[float]] | None = None) -> QdrantAdapter:
        adapter = object.__new__(QdrantAdapter)
        adapter.collection_name = "test_collection"
        adapter.vector_size = 4
        adapter.hybrid_candidate_limit = 10
        adapter.vector_weight = 0.55
        adapter.lexical_weight = 0.45
        adapter.settings = SimpleNamespace(
            embedding_model="test-embedding-model",
            embedding_dimensions=4,
            qdrant_hybrid_candidate_limit=10,
            qdrant_vector_weight=0.55,
            qdrant_lexical_weight=0.45,
        )
        adapter.embedding_provider = _FakeEmbeddingProvider(embeddings or {})
        adapter.client = _FakeQdrantClient(points)
        adapter.available = True
        return adapter

    def test_vectorize_text_prefers_embedding_provider(self) -> None:
        adapter = self._build_adapter(
            points=[],
            embeddings={"cash flow resilience": [0.1, 0.2, 0.3, 0.4]},
        )

        vector = adapter._vectorize_text("cash flow resilience")

        self.assertEqual(vector, [0.1, 0.2, 0.3, 0.4])

    def test_search_fuses_vector_and_lexical_rankings(self) -> None:
        adapter = self._build_adapter(
            points=[
                SimpleNamespace(
                    id="doc-a",
                    score=0.95,
                    payload={
                        "artifact_id": "doc-a",
                        "content": "liquidity overview and trading tone",
                        "document_title": "liquidity_note",
                        "symbol": "000001",
                    },
                ),
                SimpleNamespace(
                    id="doc-b",
                    score=0.80,
                    payload={
                        "artifact_id": "doc-b",
                        "content": "cash flow resilience framework with resilient cash flow assumptions",
                        "document_title": "cashflow_note",
                        "symbol": "000001",
                    },
                ),
            ],
            embeddings={"cash flow resilience": [0.1, 0.2, 0.3, 0.4]},
        )

        hits = adapter.search(
            query_text="cash flow resilience",
            top_k=2,
            metadata_filters={"symbol": "000001"},
        )

        self.assertEqual([item["id"] for item in hits], ["doc-b", "doc-a"])
        self.assertGreater(float(hits[0]["lexical_score"] or 0.0), 0.0)
        self.assertIn("cash", hits[0]["matched_terms"])

    def test_search_respects_metadata_filters_before_hybrid_fusion(self) -> None:
        adapter = self._build_adapter(
            points=[
                SimpleNamespace(
                    id="doc-a",
                    score=0.95,
                    payload={
                        "artifact_id": "doc-a",
                        "content": "cash flow resilience for symbol 000001",
                        "document_title": "doc_a",
                        "symbol": "000001",
                    },
                ),
                SimpleNamespace(
                    id="doc-b",
                    score=0.90,
                    payload={
                        "artifact_id": "doc-b",
                        "content": "cash flow resilience for symbol 600519",
                        "document_title": "doc_b",
                        "symbol": "600519",
                    },
                ),
            ],
            embeddings={"cash flow resilience": [0.1, 0.2, 0.3, 0.4]},
        )

        hits = adapter.search(
            query_text="cash flow resilience",
            top_k=2,
            metadata_filters={"symbol": "600519"},
        )

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "doc-b")


if __name__ == "__main__":
    unittest.main()
