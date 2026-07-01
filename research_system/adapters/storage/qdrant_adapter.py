from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.local.persistence import CollectionPersistence

from research_system.runtime.settings import Settings
from research_system.schemas.runtime import BlackboardEntry


class QdrantUnavailableError(RuntimeError):
    pass


class QdrantAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.collection_name = settings.qdrant_collection_name
        self.client = self._build_client()
        self.vector_size = 8
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
            return
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(size=self.vector_size, distance=models.Distance.COSINE),
        )

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

    def upsert_entry(self, entry: BlackboardEntry) -> None:
        if self.client is None:
            return
        payload = self.to_payload(entry)
        vector = self._vectorize_payload(payload)
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=self._point_id(entry.entry_id),
                    vector=vector,
                    payload=payload,
                )
            ],
        )

    def search(
        self,
        *,
        query_text: str,
        top_k: int = 5,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if self.client is None:
            return []
        vector = self._vectorize_text(query_text)
        query_filter = self._build_filter(metadata_filters or {})
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
            with_vectors=False,
        )
        return [
            {
                "id": item.id,
                "score": item.score,
                "payload": item.payload,
            }
            for item in response.points
        ]

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

    def to_payload(self, entry: BlackboardEntry) -> dict[str, Any]:
        return {
            "entry_id": entry.entry_id,
            "record_type": entry.kind,
            "source_type": self._source_type(entry),
            "symbol": entry.payload.get("symbol"),
            "sector": entry.payload.get("sector"),
            "theme": entry.payload.get("theme"),
            "dimension_tags": entry.payload.get("dimension_tags", []),
            "dimension_family": entry.payload.get("dimension_family"),
            "scenario_tags": entry.payload.get("scenario_tags", []),
            "methodology_tags": entry.payload.get("methodology_tags", []),
            "dimension_key": entry.payload.get("dimension_key"),
            "methodology_type": entry.payload.get("methodology_type"),
            "proposal_type": (entry.payload.get("proposal") or {}).get("proposal_type"),
            "proposal_status": (entry.payload.get("proposal") or {}).get("status"),
            "peer_group_tags": entry.payload.get("peer_group_tags", []),
            "assumption_tags": entry.payload.get("assumption_tags", []),
            "sensitivity_tags": entry.payload.get("sensitivity_tags", []),
            "valuation_band_label": entry.payload.get("valuation_band_label"),
            "publish_time": self._publish_time(entry),
            "document_title": self._document_title(entry),
            "document_id": entry.entry_id,
            "run_id": entry.run_id,
            "agent": entry.agent,
            "phase": entry.phase,
            "content": self._payload_text(entry.payload),
            "source_refs": entry.source_refs,
        }

    def _point_id(self, entry_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, entry_id))

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

    def _vectorize_text(self, text: str) -> list[float]:
        base = [0.0] * self.vector_size
        encoded = text.encode("utf-8", errors="ignore")
        if not encoded:
            return base
        for index, byte in enumerate(encoded):
            base[index % self.vector_size] += byte / 255.0
        return base

    def _vectorize_payload(self, payload: dict[str, Any]) -> list[float]:
        return self._vectorize_text(payload.get("content", ""))

    def _source_type(self, entry: BlackboardEntry) -> str:
        if entry.kind == "verified_snapshot":
            return f"{entry.payload.get('snapshot_type', 'snapshot')}_snapshot"
        if entry.kind == "logic_block":
            return "logic_block"
        if entry.kind == "distilled_dimension":
            return "distilled_dimension"
        if entry.kind == "review_finding":
            return "review_finding"
        return str(entry.payload.get("source_type", entry.kind))

    def _document_title(self, entry: BlackboardEntry) -> str:
        if entry.kind == "verified_snapshot":
            snapshot_type = entry.payload.get("snapshot_type", "snapshot")
            target = entry.payload.get("symbol") or entry.payload.get("sector") or "unknown"
            return f"{snapshot_type}_snapshot:{target}"
        if entry.kind == "logic_block":
            return f"logic_block:{entry.payload.get('block_type', 'unknown')}"
        if entry.kind == "distilled_dimension":
            return f"distilled_dimension:{entry.payload.get('dimension_name', 'unknown')}"
        if entry.kind == "review_finding":
            return f"review_finding:{entry.payload.get('finding_type', 'unknown')}"
        return str(entry.payload.get("title") or entry.payload.get("snapshot_type") or entry.kind)

    def _publish_time(self, entry: BlackboardEntry) -> str:
        if entry.kind == "verified_snapshot" and entry.payload.get("snapshot_type") == "notice":
            latest_notice = entry.payload.get("facts", {}).get("latest_notice", {})
            for key in ("公告时间", "公告日期"):
                value = latest_notice.get(key)
                if value:
                    return str(value)
        return str(entry.payload.get("publish_time") or entry.created_at.isoformat())
