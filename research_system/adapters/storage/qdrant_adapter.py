from __future__ import annotations

import math
import json
import re
import uuid
from collections import Counter
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.local.persistence import CollectionPersistence

from research_system.runtime.platform.settings import Settings
from research_system.schemas.artifacts import ArtifactRecord
from research_system.schemas.memory import MemoryRecord

_SEARCH_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "for",
    "from",
    "how",
    "into",
    "just",
    "more",
    "previous",
    "recent",
    "review",
    "than",
    "that",
    "the",
    "this",
    "what",
    "which",
    "who",
    "with",
}


class QdrantUnavailableError(RuntimeError):
    pass


class QdrantAdapter:
    def __init__(self, settings: Settings, embedding_provider: Any | None = None) -> None:
        self.settings = settings
        self.embedding_provider = embedding_provider
        self.collection_name = settings.qdrant_collection_name
        self.vector_size = max(int(settings.embedding_dimensions or 1536), 1)
        self.hybrid_candidate_limit = max(int(settings.qdrant_hybrid_candidate_limit or 50), 10)
        self.vector_weight = float(settings.qdrant_vector_weight or 0.0)
        self.lexical_weight = float(settings.qdrant_lexical_weight or 0.0)
        self.client = self._build_client()
        self.available = self.client is not None
        if self.available:
            self._ensure_collection()

    def _build_client(self) -> QdrantClient | None:
        if self.settings.qdrant_url:
            return QdrantClient(url=self.settings.qdrant_url, api_key=self.settings.qdrant_api_key)
        local_path = self.settings.workspace_root / "qdrant_local"
        local_path.mkdir(parents=True, exist_ok=True)
        try:
            # Avoid qdrant-client's transient :memory: SQLite probe, which can leak a connection warning on Windows/Python 3.13.
            if CollectionPersistence.CHECK_SAME_THREAD is None:
                CollectionPersistence.CHECK_SAME_THREAD = True
            return QdrantClient(path=str(local_path))
        except Exception as exc:
            raise QdrantUnavailableError(str(exc)) from exc

    def _ensure_collection(self) -> None:
        if self.client is None:
            return
        existing = [item.name for item in self.client.get_collections().collections]
        if self.collection_name in existing:
            existing_size = self._collection_vector_size()
            if existing_size == self.vector_size:
                return
            if self.settings.qdrant_url:
                raise QdrantUnavailableError(
                    f"collection '{self.collection_name}' uses vector size {existing_size}, expected {self.vector_size}"
                )
            self.client.delete_collection(collection_name=self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(size=self.vector_size, distance=models.Distance.COSINE),
        )

    def _collection_vector_size(self) -> int | None:
        if self.client is None:
            return None
        try:
            collection = self.client.get_collection(self.collection_name)
        except Exception:
            return None
        config = getattr(collection, "config", None)
        params = getattr(config, "params", None)
        vectors = getattr(params, "vectors", None)
        size = getattr(vectors, "size", None)
        if size is not None:
            return int(size)
        if isinstance(vectors, dict):
            for value in vectors.values():
                nested_size = getattr(value, "size", None)
                if nested_size is not None:
                    return int(nested_size)
        return None

    def reset_collection(self) -> None:
        if self.client is None:
            return
        existing = [item.name for item in self.client.get_collections().collections]
        if self.collection_name in existing:
            self.client.delete_collection(collection_name=self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(size=self.vector_size, distance=models.Distance.COSINE),
        )

    def upsert_artifact(self, artifact: ArtifactRecord) -> None:
        if self.client is None:
            return
        payload = self.to_payload(artifact)
        vector = self._vectorize_payload(payload)
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=self._point_id(artifact.artifact_id),
                    vector=vector,
                    payload=payload,
                )
            ],
        )

    def upsert_memory(self, memory: MemoryRecord) -> None:
        """Index a verified or candidate memory in the shared hybrid collection."""
        if self.client is None:
            return
        payload = self.to_memory_payload(memory)
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=self._memory_point_id(memory.memory_id),
                    vector=self._vectorize_text(payload["content"]),
                    payload=payload,
                )
            ],
        )

    def search_memory(
        self,
        *,
        query_text: str,
        top_k: int = 10,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        filters = {"record_type": "memory", **dict(metadata_filters or {})}
        return self.search(
            query_text=query_text,
            top_k=top_k,
            metadata_filters=filters,
        )

    def search(
        self,
        *,
        query_text: str,
        top_k: int = 5,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if self.client is None or top_k <= 0:
            return []
        query_filter = self._build_filter(metadata_filters or {})
        candidate_limit = max(top_k * 8, self.hybrid_candidate_limit)
        vector_hits = self._query_vector_hits(
            query_text=query_text,
            limit=candidate_limit,
            query_filter=query_filter,
        )
        lexical_points = self._scroll_points(
            limit=candidate_limit,
            query_filter=query_filter,
        )
        return self._fuse_hybrid_hits(
            query_text=query_text,
            top_k=top_k,
            vector_hits=vector_hits,
            lexical_points=lexical_points,
        )

    def close(self) -> None:
        if self.client is None:
            return
        inner_client = getattr(self.client, "_client", None)
        collections = getattr(inner_client, "collections", {}) if inner_client is not None else {}
        for collection in list(collections.values()):
            persistence = getattr(collection, "storage", None)
            sqlite_conn = getattr(persistence, "storage", None) if persistence is not None else None
            sqlite_close = getattr(sqlite_conn, "close", None)
            if callable(sqlite_close):
                sqlite_close()
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        inner_close = getattr(inner_client, "close", None)
        if callable(inner_close):
            inner_close()
        self.client = None
        self.available = False

    def to_payload(self, artifact: ArtifactRecord) -> dict[str, Any]:
        content = artifact.content
        return {
            "artifact_id": artifact.artifact_id,
            "record_type": artifact.artifact_type,
            "schema_version": artifact.schema_version,
            "source_type": self._source_type(artifact),
            "symbol": content.get("symbol"),
            "sector": content.get("sector"),
            "theme": content.get("theme"),
            "dimension_tags": content.get("dimension_tags", []),
            "dimension_family": content.get("dimension_family"),
            "scenario_tags": content.get("scenario_tags", []),
            "methodology_tags": content.get("methodology_tags", []),
            "dimension_key": content.get("dimension_key"),
            "methodology_type": content.get("methodology_type"),
            "proposal_type": (content.get("proposal") or {}).get("proposal_type"),
            "proposal_status": (content.get("proposal") or {}).get("status"),
            "peer_group_tags": content.get("peer_group_tags", []),
            "assumption_tags": content.get("assumption_tags", []),
            "sensitivity_tags": content.get("sensitivity_tags", []),
            "valuation_band_label": content.get("valuation_band_label"),
            "publish_time": self._publish_time(artifact),
            "document_title": self._document_title(artifact),
            "document_id": artifact.artifact_id,
            "run_id": artifact.run_id,
            "thread_id": artifact.thread_id,
            "executor": artifact.producer.executor,
            "capability": artifact.producer.capability,
            "node_id": artifact.producer.node_id,
            "content": self._payload_text(content),
            "source_refs": artifact.source_refs,
        }

    def _point_id(self, artifact_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, artifact_id))

    def _payload_text(self, payload: dict[str, Any]) -> str:
        snapshot_type = payload.get("snapshot_type")
        if snapshot_type == "market":
            facts = payload.get("facts", {})
            latest_bar = facts.get("market_hist", {}).get("latest_bar", {})
            overview = facts.get("market_overview", {})
            parts = [
                f"market snapshot for {payload.get('symbol') or 'unknown symbol'}",
                f"query: {payload.get('query', '')}",
                f"snapshot_date: {payload.get('snapshot_date', '')}",
            ]
            if latest_bar:
                parts.append(f"latest_bar: {latest_bar}")
            if overview:
                parts.append(f"market_overview: {overview}")
            if payload.get("field_sources"):
                parts.append(f"field_sources: {payload.get('field_sources')}")
            if payload.get("source_trace"):
                parts.append(f"source_trace: {payload.get('source_trace')}")
            if payload.get("degraded"):
                parts.append(f"degraded: {payload.get('degrade_reason')}")
            return "\n".join(parts)

        if snapshot_type == "fundamental":
            facts = payload.get("facts", {})
            parts = [
                f"fundamental snapshot for {payload.get('symbol') or 'unknown symbol'}",
                f"query: {payload.get('query', '')}",
                f"snapshot_date: {payload.get('snapshot_date', '')}",
            ]
            if facts.get("info"):
                parts.append(f"company_info: {facts['info']}")
            if facts.get("financial_abstract"):
                parts.append(f"financial_abstract: {facts['financial_abstract']}")
            if payload.get("field_sources"):
                parts.append(f"field_sources: {payload.get('field_sources')}")
            if payload.get("source_trace"):
                parts.append(f"source_trace: {payload.get('source_trace')}")
            if payload.get("degraded"):
                parts.append(f"degraded: {payload.get('degrade_reason')}")
            return "\n".join(parts)

        if snapshot_type == "notice":
            facts = payload.get("facts", {})
            parts = [
                f"notice snapshot for {payload.get('symbol') or 'unknown symbol'}",
                f"query: {payload.get('query', '')}",
                f"snapshot_date: {payload.get('snapshot_date', '')}",
            ]
            if facts.get("latest_notice"):
                parts.append(f"latest_notice: {facts['latest_notice']}")
            if facts.get("recent_notices"):
                parts.append(f"recent_notices: {facts['recent_notices']}")
            if payload.get("field_sources"):
                parts.append(f"field_sources: {payload.get('field_sources')}")
            if payload.get("source_trace"):
                parts.append(f"source_trace: {payload.get('source_trace')}")
            if payload.get("degraded"):
                parts.append(f"degraded: {payload.get('degrade_reason')}")
            return "\n".join(parts)

        if snapshot_type in {"lhb", "capital_flow", "sentiment", "events", "policy"}:
            facts = payload.get("facts", {})
            parts = [
                f"{snapshot_type} snapshot for {payload.get('symbol') or payload.get('sector') or payload.get('theme') or 'unknown target'}",
                f"query: {payload.get('query', '')}",
                f"snapshot_date: {payload.get('snapshot_date', '')}",
            ]
            if facts:
                parts.append(str(facts))
            if payload.get("field_sources"):
                parts.append(f"field_sources: {payload.get('field_sources')}")
            if payload.get("source_trace"):
                parts.append(f"source_trace: {payload.get('source_trace')}")
            if payload.get("degraded"):
                parts.append(f"degraded: {payload.get('degrade_reason')}")
            return "\n".join(parts)

        if snapshot_type == "sector":
            facts = payload.get("facts", {})
            parts = [
                f"sector snapshot for {payload.get('sector') or 'unknown sector'}",
                f"query: {payload.get('query', '')}",
                f"snapshot_date: {payload.get('snapshot_date', '')}",
            ]
            if facts.get("sector_type"):
                parts.append(f"sector_type: {facts['sector_type']}")
            if facts.get("latest_hist"):
                parts.append(f"latest_hist: {facts['latest_hist']}")
            if facts.get("spot"):
                parts.append(f"spot: {facts['spot']}")
            if facts.get("benchmark"):
                parts.append(f"benchmark: {facts['benchmark']}")
            if facts.get("relative_strength"):
                parts.append(f"relative_strength: {facts['relative_strength']}")
            if facts.get("breadth"):
                parts.append(f"breadth: {facts['breadth']}")
            if facts.get("top_constituents"):
                parts.append(f"top_constituents: {facts['top_constituents']}")
            if facts.get("catalyst_samples"):
                parts.append(f"catalyst_samples: {facts['catalyst_samples']}")
            if facts.get("theme_news"):
                parts.append(f"theme_news: {facts['theme_news']}")
            if facts.get("policy_signals"):
                parts.append(f"policy_signals: {facts['policy_signals']}")
            if facts.get("regulatory_signals"):
                parts.append(f"regulatory_signals: {facts['regulatory_signals']}")
            if facts.get("signal_scorecard"):
                parts.append(f"signal_scorecard: {facts['signal_scorecard']}")
            if facts.get("catalyst_digest"):
                parts.append(f"catalyst_digest: {facts['catalyst_digest']}")
            if payload.get("field_sources"):
                parts.append(f"field_sources: {payload.get('field_sources')}")
            if payload.get("source_trace"):
                parts.append(f"source_trace: {payload.get('source_trace')}")
            if payload.get("degraded"):
                parts.append(f"degraded: {payload.get('degrade_reason')}")
            return "\n".join(parts)

        if payload.get("block_type"):
            parts = [
                f"logic block for {payload.get('source_record_id') or 'unknown source'}",
                f"block_type: {payload.get('block_type')}",
                f"text: {payload.get('text', '')}",
            ]
            if payload.get("normalized_claim"):
                parts.append(f"normalized_claim: {payload['normalized_claim']}")
            if payload.get("symbols"):
                parts.append(f"symbols: {payload['symbols']}")
            if payload.get("sectors"):
                parts.append(f"sectors: {payload['sectors']}")
            if payload.get("risk_flags"):
                parts.append(f"risk_flags: {payload['risk_flags']}")
            return "\n".join(parts)

        if payload.get("dimension_name"):
            parts = [
                f"distilled dimension: {payload.get('dimension_name')}",
                f"canonical_dimension_name: {payload.get('canonical_dimension_name') or payload.get('dimension_name')}",
                f"dimension_family: {payload.get('dimension_family') or ''}",
                f"dimension_category: {payload.get('dimension_category')}",
            ]
            if payload.get("alias_hits"):
                parts.append(f"alias_hits: {payload['alias_hits']}")
            if payload.get("scenario_tags"):
                parts.append(f"scenario_tags: {payload['scenario_tags']}")
            if payload.get("methodology_tags"):
                parts.append(f"methodology_tags: {payload['methodology_tags']}")
            if payload.get("dimension_tags"):
                parts.append(f"dimension_tags: {payload['dimension_tags']}")
            if payload.get("supported_claim_block_ids"):
                parts.append(f"supported_claim_block_ids: {payload['supported_claim_block_ids']}")
            if payload.get("evidence_block_ids"):
                parts.append(f"evidence_block_ids: {payload['evidence_block_ids']}")
            return "\n".join(parts)

        if payload.get("finding_type"):
            parts = [
                f"review finding for {payload.get('target_kind') or 'unknown_target'}",
                f"finding_type: {payload.get('finding_type')}",
                f"severity: {payload.get('severity')}",
                f"verdict: {payload.get('verdict')}",
                f"reason: {payload.get('reason', '')}",
            ]
            if payload.get("suggested_action"):
                parts.append(f"suggested_action: {payload['suggested_action']}")
            return "\n".join(parts)

        if payload.get("methodology_type"):
            parts = [
                f"methodology result for {payload.get('symbol') or 'unknown_symbol'}",
                f"methodology_type: {payload.get('methodology_type')}",
            ]
            if payload.get("peer_group_tags"):
                parts.append(f"peer_group_tags: {payload.get('peer_group_tags')}")
            if payload.get("assumption_tags"):
                parts.append(f"assumption_tags: {payload.get('assumption_tags')}")
            if payload.get("sensitivity_tags"):
                parts.append(f"sensitivity_tags: {payload.get('sensitivity_tags')}")
            if payload.get("valuation_band"):
                parts.append(f"valuation_band: {payload.get('valuation_band')}")
            if payload.get("methodology_result"):
                parts.append(f"methodology_result: {payload.get('methodology_result')}")
            return "\n".join(parts)

        if payload.get("proposal"):
            proposal = payload.get("proposal") or {}
            parts = [
                f"proposal draft: {proposal.get('proposal_type')}",
                f"status: {proposal.get('status')}",
                f"title: {proposal.get('title')}",
                f"summary: {proposal.get('summary')}",
            ]
            if payload.get("manifest"):
                parts.append(f"manifest: {payload.get('manifest')}")
            if payload.get("memory_record"):
                parts.append(f"memory_record: {payload.get('memory_record')}")
            return "\n".join(parts)

        text_parts = []
        for key in ("title", "content", "summary", "conclusion", "risk"):
            value = payload.get(key)
            if value:
                text_parts.append(str(value))
        if not text_parts:
            text_parts.append(str(payload))
        return "\n".join(text_parts)

    def _build_filter(self, filters: dict[str, Any]) -> models.Filter | None:
        allowed_keys = {
            "record_type",
            "memory_layer",
            "memory_id",
            "memory_type",
            "status",
            "scope",
            "domain_id",
            "tenant_id",
            "user_id",
            "thread_id",
            "source_type",
            "symbol",
            "sector",
            "theme",
            "run_id",
            "dimension_family",
            "dimension_key",
            "scenario_tags",
            "methodology_tags",
            "dimension_tags",
            "methodology_type",
            "valuation_band_label",
        }
        conditions = []
        for key, value in filters.items():
            if key not in allowed_keys or value in (None, "", []):
                continue
            if isinstance(value, list):
                values = [item for item in value if item not in (None, "")]
                if not values:
                    continue
                match = models.MatchAny(any=values)
            else:
                match = models.MatchValue(value=value)
            conditions.append(models.FieldCondition(key=key, match=match))
        if not conditions:
            return None
        return models.Filter(must=conditions)

    def _query_vector_hits(
        self,
        *,
        query_text: str,
        limit: int,
        query_filter: models.Filter | None,
    ) -> list[Any]:
        if self.client is None:
            return []
        vector = self._vectorize_text(query_text)
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
            with_vectors=False,
        )
        return list(getattr(response, "points", []) or [])

    def _scroll_points(
        self,
        *,
        limit: int,
        query_filter: models.Filter | None,
    ) -> list[Any]:
        if self.client is None:
            return []
        scroll = getattr(self.client, "scroll", None)
        if not callable(scroll):
            return []
        kwargs: dict[str, Any] = {
            "collection_name": self.collection_name,
            "limit": limit,
            "with_payload": True,
            "with_vectors": False,
        }
        if query_filter is not None:
            kwargs["scroll_filter"] = query_filter
        try:
            response = scroll(**kwargs)
        except TypeError:
            if "scroll_filter" in kwargs:
                kwargs["query_filter"] = kwargs.pop("scroll_filter")
            response = scroll(**kwargs)
        if isinstance(response, tuple):
            return list(response[0] or [])
        return list(getattr(response, "points", response) or [])

    def _fuse_hybrid_hits(
        self,
        *,
        query_text: str,
        top_k: int,
        vector_hits: list[Any],
        lexical_points: list[Any],
    ) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        vector_rank: dict[str, int] = {}
        vector_score: dict[str, float] = {}
        for rank, point in enumerate(vector_hits, start=1):
            key = self._candidate_key(point)
            vector_rank[key] = rank
            vector_score[key] = float(getattr(point, "score", 0.0) or 0.0)
            candidates[key] = {
                "id": getattr(point, "id", key),
                "payload": dict(getattr(point, "payload", {}) or {}),
            }

        lexical_hits = self._score_lexical_hits(
            query_text=query_text,
            points=lexical_points,
        )
        lexical_rank: dict[str, int] = {}
        lexical_score: dict[str, float] = {}
        lexical_match_terms: dict[str, list[str]] = {}
        max_lexical_score = max((row["lexical_score"] for row in lexical_hits), default=0.0)
        for rank, row in enumerate(lexical_hits, start=1):
            key = row["key"]
            lexical_rank[key] = rank
            lexical_score[key] = float(row["lexical_score"])
            lexical_match_terms[key] = list(row["matched_terms"])
            candidates.setdefault(
                key,
                {
                    "id": getattr(row["point"], "id", key),
                    "payload": dict(getattr(row["point"], "payload", {}) or {}),
                },
            )

        results: list[dict[str, Any]] = []
        for key, candidate in candidates.items():
            fused_score = 0.0
            if key in vector_rank and self.vector_weight > 0:
                fused_score += self.vector_weight * self._rrf_score(vector_rank[key])
            if key in lexical_rank and self.lexical_weight > 0:
                fused_score += self.lexical_weight * self._rrf_score(lexical_rank[key])
            if key in lexical_score and max_lexical_score > 0:
                fused_score += 0.05 * (lexical_score[key] / max_lexical_score)
            results.append(
                {
                    "id": candidate["id"],
                    "score": fused_score,
                    "payload": candidate["payload"],
                    "vector_score": vector_score.get(key),
                    "lexical_score": lexical_score.get(key),
                    "matched_terms": lexical_match_terms.get(key, []),
                }
            )
        results.sort(
            key=lambda item: (
                float(item.get("score") or 0.0),
                float(item.get("lexical_score") or 0.0),
                float(item.get("vector_score") or 0.0),
            ),
            reverse=True,
        )
        return [
            {
                "id": item["id"],
                "score": item["score"],
                "payload": item["payload"],
                "vector_score": item.get("vector_score"),
                "lexical_score": item.get("lexical_score"),
                "matched_terms": item.get("matched_terms", []),
            }
            for item in results[:top_k]
        ]

    def _rrf_score(self, rank: int) -> float:
        return 1.0 / (60.0 + float(rank))

    def _score_lexical_hits(
        self,
        *,
        query_text: str,
        points: list[Any],
    ) -> list[dict[str, Any]]:
        query_terms = sorted(self._extract_search_terms(query_text))
        if not query_terms:
            return []
        rows: list[dict[str, Any]] = []
        document_frequency: Counter[str] = Counter()
        total_docs = 0
        total_length = 0
        for point in points:
            payload = getattr(point, "payload", {}) or {}
            tokens = self._tokenize_text(str(payload.get("content") or ""))
            if not tokens:
                continue
            counts = Counter(tokens)
            total_docs += 1
            total_length += sum(counts.values())
            for term in query_terms:
                if counts.get(term):
                    document_frequency[term] += 1
            rows.append(
                {
                    "key": self._candidate_key(point),
                    "point": point,
                    "counts": counts,
                }
            )
        if not rows or total_docs <= 0:
            return []
        average_doc_length = total_length / total_docs if total_length > 0 else 1.0
        scored: list[dict[str, Any]] = []
        for row in rows:
            counts = row["counts"]
            matched_terms = [term for term in query_terms if counts.get(term)]
            if not matched_terms:
                continue
            score = self._bm25_score(
                query_terms=query_terms,
                counts=counts,
                document_frequency=document_frequency,
                total_docs=total_docs,
                average_doc_length=average_doc_length,
            )
            if score <= 0:
                continue
            scored.append(
                {
                    "key": row["key"],
                    "point": row["point"],
                    "lexical_score": score,
                    "matched_terms": matched_terms,
                }
            )
        scored.sort(key=lambda item: float(item["lexical_score"]), reverse=True)
        return scored

    def _bm25_score(
        self,
        *,
        query_terms: list[str],
        counts: Counter[str],
        document_frequency: Counter[str],
        total_docs: int,
        average_doc_length: float,
    ) -> float:
        k1 = 1.5
        b = 0.75
        doc_length = max(sum(counts.values()), 1)
        score = 0.0
        for term in query_terms:
            tf = float(counts.get(term, 0))
            if tf <= 0:
                continue
            df = float(document_frequency.get(term, 0))
            idf = math.log(1.0 + ((total_docs - df + 0.5) / (df + 0.5)))
            numerator = tf * (k1 + 1.0)
            denominator = tf + k1 * (1.0 - b + b * (doc_length / max(average_doc_length, 1.0)))
            score += idf * (numerator / denominator)
        return score

    def _candidate_key(self, point: Any) -> str:
        payload = getattr(point, "payload", {}) or {}
        record_id = str(
            payload.get("artifact_id") or payload.get("memory_id") or ""
        ).strip()
        if record_id:
            return record_id
        return str(getattr(point, "id", "") or "")

    def to_memory_payload(self, memory: MemoryRecord) -> dict[str, Any]:
        return {
            "artifact_id": memory.memory_id,
            "memory_id": memory.memory_id,
            "record_type": "memory",
            "memory_layer": memory.layer,
            "memory_type": memory.memory_type,
            "domain_id": memory.domain_id,
            "scope": memory.scope,
            "tenant_id": memory.tenant_id,
            "user_id": memory.user_id,
            "thread_id": memory.thread_id,
            "status": memory.status,
            "source_run_ids": list(memory.source_run_ids),
            "source_artifact_ids": list(memory.source_artifact_ids),
            "content": self._memory_payload_text(memory.content),
            "memory_content": memory.content,
            "created_at": memory.created_at.isoformat(),
            "expires_at": memory.expires_at.isoformat() if memory.expires_at else None,
        }

    @staticmethod
    def _memory_payload_text(content: dict[str, Any]) -> str:
        return json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)

    def _memory_point_id(self, memory_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"memory:{memory_id}"))

    def _vectorize_text(self, text: str) -> list[float]:
        normalized = str(text or "").strip()
        if normalized:
            embedded = self._embedding_vector(normalized)
            if embedded is not None:
                return embedded
        return self._hash_vectorize(normalized)

    def _vectorize_payload(self, payload: dict[str, Any]) -> list[float]:
        return self._vectorize_text(payload.get("content", ""))

    def _embedding_vector(self, text: str) -> list[float] | None:
        provider = getattr(self, "embedding_provider", None)
        embed_texts = getattr(provider, "embed_texts", None)
        if not callable(embed_texts):
            return None
        available = getattr(provider, "available", True)
        if available is False:
            return None
        try:
            vectors = embed_texts(
                texts=[text],
                model=self.settings.embedding_model,
            )
        except Exception:
            return None
        if not vectors:
            return None
        return self._coerce_vector_size(vectors[0])

    def _coerce_vector_size(self, vector: list[float]) -> list[float]:
        normalized = [float(item) for item in list(vector or [])]
        if len(normalized) == self.vector_size:
            return normalized
        if len(normalized) > self.vector_size:
            return normalized[: self.vector_size]
        return normalized + [0.0] * (self.vector_size - len(normalized))

    def _hash_vectorize(self, text: str) -> list[float]:
        base = [0.0] * self.vector_size
        encoded = text.encode("utf-8", errors="ignore")
        if not encoded:
            return base
        for index, byte in enumerate(encoded):
            base[index % self.vector_size] += byte / 255.0
        return base

    def _extract_search_terms(self, query: str) -> set[str]:
        return set(self._tokenize_text(query))

    def _tokenize_text(self, text: str) -> list[str]:
        tokens: list[str] = []
        lowered = text.lower()
        for token in re.findall(r"[a-z0-9]{2,}", lowered):
            if token not in _SEARCH_STOPWORDS:
                tokens.append(token)
        for token in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            tokens.append(token)
            if len(token) <= 16:
                for index in range(0, len(token) - 1):
                    tokens.append(token[index:index + 2])
        return [token for token in tokens if token.strip()]

    def _source_type(self, artifact: ArtifactRecord) -> str:
        if artifact.artifact_type == "verified_snapshot":
            return f"{artifact.content.get('snapshot_type', 'snapshot')}_snapshot"
        if artifact.artifact_type == "logic_block":
            return "logic_block"
        if artifact.artifact_type == "distilled_dimension":
            return "distilled_dimension"
        if artifact.artifact_type == "review_finding":
            return "review_finding"
        return str(artifact.content.get("source_type", artifact.artifact_type))

    def _document_title(self, artifact: ArtifactRecord) -> str:
        if artifact.artifact_type == "verified_snapshot":
            snapshot_type = artifact.content.get("snapshot_type", "snapshot")
            target = artifact.content.get("symbol") or artifact.content.get("sector") or "unknown"
            return f"{snapshot_type}_snapshot:{target}"
        if artifact.artifact_type == "logic_block":
            return f"logic_block:{artifact.content.get('block_type', 'unknown')}"
        if artifact.artifact_type == "distilled_dimension":
            return f"distilled_dimension:{artifact.content.get('dimension_name', 'unknown')}"
        if artifact.artifact_type == "review_finding":
            return f"review_finding:{artifact.content.get('finding_type', 'unknown')}"
        return str(
            artifact.content.get("title")
            or artifact.content.get("snapshot_type")
            or artifact.artifact_type
        )

    def _publish_time(self, artifact: ArtifactRecord) -> str:
        if artifact.artifact_type == "verified_snapshot" and artifact.content.get("snapshot_type") == "notice":
            latest_notice = artifact.content.get("facts", {}).get("latest_notice", {})
            for key in ("鍏憡鏃堕棿", "鍏憡鏃ユ湡"):
                value = latest_notice.get(key)
                if value:
                    return str(value)
        return str(artifact.content.get("publish_time") or artifact.created_at.isoformat())
