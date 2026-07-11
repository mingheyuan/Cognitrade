from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Protocol

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl
from research_system.schemas.memory import MemoryEvaluation, MemoryLayer, MemoryRecord, MemoryScope, utc_now


class MemoryPromotionError(RuntimeError):
    pass


class MemoryRetrievalBackend(Protocol):
    def upsert_memory(self, memory: MemoryRecord) -> None: ...

    def search_memory(
        self,
        *,
        query_text: str,
        top_k: int,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...


class LayeredMemoryStore:
    """Audited episodic/domain/policy memory with one-way promotion gates."""

    def __init__(
        self,
        root: Path,
        *,
        retrieval_backend: MemoryRetrievalBackend | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.root / "promotion_audit.jsonl"
        self.retrieval_backend = retrieval_backend

    def add_episode(
        self,
        *,
        domain_id: str,
        memory_type: str,
        content: dict,
        source_run_id: str,
        source_artifact_ids: list[str],
        gated: bool,
        gate_evaluations: list[dict] | None = None,
        scope: MemoryScope = "domain",
        tenant_id: str | None = None,
        user_id: str | None = None,
        thread_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryRecord:
        if not gated:
            raise MemoryPromotionError("ungated run output cannot enter episodic memory")
        evaluations = [
            MemoryEvaluation(
                evaluation_id=str(row.get("gate_id") or "gate"),
                evaluator="professional_gate",
                verdict=str(row.get("verdict") or "fail"),
                evidence_refs=[str(row.get("artifact_id") or "")],
                notes=[str(row.get("reason") or "")],
            )
            for row in list(gate_evaluations or [])
        ]
        record = MemoryRecord(
            layer="episodic",
            domain_id=domain_id,
            memory_type=memory_type,
            content=dict(content),
            scope=scope,
            tenant_id=tenant_id,
            user_id=user_id,
            thread_id=thread_id,
            expires_at=expires_at,
            source_run_ids=[source_run_id],
            source_artifact_ids=list(source_artifact_ids),
            evaluations=evaluations,
            status="verified",
        )
        return self._append(record, action="episode_recorded")

    def promote(
        self,
        *,
        memory_id: str,
        target_layer: MemoryLayer,
        evaluation: MemoryEvaluation,
        content: dict | None = None,
    ) -> MemoryRecord:
        source = self.get(memory_id)
        if source is None:
            raise KeyError(f"unknown memory record: {memory_id}")
        expected = {"episodic": "domain", "domain": "policy"}.get(source.layer)
        if target_layer != expected:
            raise MemoryPromotionError(
                f"invalid memory promotion: {source.layer}->{target_layer}"
            )
        if source.status != "verified":
            raise MemoryPromotionError("unverified memory cannot be promoted")
        if evaluation.verdict != "pass" or evaluation.score is None:
            raise MemoryPromotionError("promotion requires a scored passing evaluation")
        if not evaluation.evidence_refs:
            raise MemoryPromotionError("promotion evaluation requires evidence references")
        minimum_score = 0.7 if target_layer == "domain" else 0.85
        if evaluation.score < minimum_score:
            raise MemoryPromotionError(
                f"promotion score is below {target_layer} threshold: {evaluation.score}"
            )
        if target_layer == "policy":
            passing_evaluations = [
                item for item in source.evaluations if item.verdict == "pass" and item.score is not None
            ]
            if not passing_evaluations:
                raise MemoryPromotionError(
                    "policy promotion requires prior evaluated domain evidence"
                )
        promoted = MemoryRecord(
            layer=target_layer,
            domain_id=source.domain_id,
            memory_type=source.memory_type,
            content=dict(content or source.content),
            scope=source.scope,
            tenant_id=source.tenant_id,
            user_id=source.user_id,
            thread_id=source.thread_id,
            expires_at=source.expires_at,
            source_run_ids=list(source.source_run_ids),
            source_artifact_ids=list(source.source_artifact_ids),
            parent_memory_ids=[source.memory_id],
            evaluations=[*source.evaluations, evaluation],
            status="verified",
            version=source.version + 1,
            promoted_at=utc_now(),
            metadata={"promoted_from_layer": source.layer},
        )
        source_update = source.model_copy(update={"status": "promoted", "promoted_at": utc_now()})
        append_jsonl(self._path(source.layer), source_update.model_dump(mode="json"))
        self._index(source_update)
        self._record_audit(
            action="memory_promoted",
            source_memory_id=source.memory_id,
            target_memory_id=promoted.memory_id,
            source_layer=source.layer,
            target_layer=target_layer,
            evaluation_id=evaluation.evaluation_id,
        )
        return self._append(promoted, action="promotion_recorded")

    def list(
        self,
        *,
        layer: MemoryLayer | None = None,
        domain_id: str | None = None,
        status: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        thread_id: str | None = None,
        query: str | None = None,
        include_global: bool = True,
        include_scoped: bool = False,
    ) -> list[MemoryRecord]:
        layers: list[MemoryLayer] = [layer] if layer else ["episodic", "domain", "policy"]
        rows: list[MemoryRecord] = []
        for current_layer in layers:
            rows.extend(
                MemoryRecord.model_validate(row)
                for row in read_jsonl(self._path(current_layer))
            )
        latest_by_id: dict[str, MemoryRecord] = {}
        for row in rows:
            latest_by_id[row.memory_id] = row
        rows = list(latest_by_id.values())
        now = datetime.now(timezone.utc)
        visible: list[tuple[MemoryRecord, float]] = []
        query_tokens = self._query_tokens(query) if query else set()
        vector_rank = (
            self._vector_rank(
                query=query,
                layer=layer,
                domain_id=domain_id,
                status=status,
                tenant_id=tenant_id,
                user_id=user_id,
                thread_id=thread_id,
                top_k=max(len(rows), 20),
            )
            if query
            else {}
        )
        for row in rows:
            if row.expires_at is not None and row.expires_at <= now:
                if row.status not in {"revoked", "expired"}:
                    row = row.model_copy(update={"status": "expired"})
                if status not in {None, "expired"}:
                    continue
            if not include_scoped:
                if row.tenant_id is not None and (tenant_id is None or row.tenant_id != tenant_id):
                    continue
                if row.user_id is not None and (user_id is None or row.user_id != user_id):
                    continue
                if row.thread_id is not None and (thread_id is None or row.thread_id != thread_id):
                    continue
            if not include_global and row.scope == "global":
                continue
            relevance = 0.0
            if query:
                searchable = str(row.content).casefold()
                overlap = sum(1 for token in query_tokens if token in searchable)
                if overlap == 0 and row.memory_id not in vector_rank:
                    continue
                relevance = overlap / max(len(query_tokens), 1)
                if query.casefold() in searchable:
                    relevance += 1.0
            visible.append((row, relevance))
        if query:
            if vector_rank:
                visible.sort(
                    key=lambda item: (
                        item[0].memory_id in vector_rank,
                        -vector_rank.get(item[0].memory_id, len(vector_rank) + 1),
                        item[1],
                        item[0].created_at,
                    ),
                    reverse=True,
                )
            else:
                visible.sort(
                    key=lambda item: (item[1], item[0].created_at),
                    reverse=True,
                )
        rows = [item[0] for item in visible]
        if domain_id is not None:
            rows = [row for row in rows if row.domain_id == domain_id]
        if status is not None:
            rows = [row for row in rows if row.status == status]
        return rows

    @staticmethod
    def _query_tokens(query: str | None) -> set[str]:
        if not query:
            return set()
        return {
            token
            for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", query.casefold())
            if token
        }

    def get(self, memory_id: str) -> MemoryRecord | None:
        for record in reversed(self.list(include_scoped=True)):
            if record.memory_id == memory_id:
                return record
        return None

    def revoke(self, *, memory_id: str, revoked_by: str, reason: str) -> MemoryRecord:
        record = self.get(memory_id)
        if record is None:
            raise KeyError(f"unknown memory record: {memory_id}")
        if record.status in {"revoked", "expired"}:
            return record
        revoked = record.model_copy(update={"status": "revoked", "revoked_at": utc_now()})
        self._append(revoked, action="memory_revoked")
        self._record_audit(
            action="memory_revoked_by_operator",
            memory_id=memory_id,
            revoked_by=revoked_by,
            reason=reason,
        )
        return revoked

    def expire_records(self, *, now: datetime | None = None) -> list[MemoryRecord]:
        """Persist expiration transitions so retention is replayable and auditable."""
        current_time = now or utc_now()
        latest_by_id: dict[str, MemoryRecord] = {}
        for layer in ("episodic", "domain", "policy"):
            for row in read_jsonl(self._path(layer)):
                record = MemoryRecord.model_validate(row)
                latest_by_id[record.memory_id] = record

        expired: list[MemoryRecord] = []
        for record in latest_by_id.values():
            if record.expires_at is None or record.expires_at > current_time:
                continue
            if record.status in {"revoked", "expired"}:
                continue
            expired_record = record.model_copy(
                update={"status": "expired", "revoked_at": None}
            )
            self._append(expired_record, action="memory_expired")
            expired.append(expired_record)
        return expired

    def _append(self, record: MemoryRecord, *, action: str) -> MemoryRecord:
        append_jsonl(self._path(record.layer), record.model_dump(mode="json"))
        self._record_audit(
            action=action,
            memory_id=record.memory_id,
            layer=record.layer,
            status=record.status,
        )
        self._index(record)
        return record

    def _index(self, record: MemoryRecord) -> None:
        if self.retrieval_backend is None:
            return
        try:
            self.retrieval_backend.upsert_memory(record)
        except Exception as exc:
            # The JSONL store remains the source of truth; retrieval failure is
            # observable and falls back to lexical ranking on the next query.
            self._record_audit(
                action="memory_index_failed",
                memory_id=record.memory_id,
                layer=record.layer,
                error=f"{exc.__class__.__name__}: {exc}",
            )

    def _vector_rank(
        self,
        *,
        query: str,
        layer: MemoryLayer | None,
        domain_id: str | None,
        status: str | None,
        tenant_id: str | None,
        user_id: str | None,
        thread_id: str | None,
        top_k: int,
    ) -> dict[str, int]:
        if self.retrieval_backend is None:
            return {}
        filters: dict[str, Any] = {}
        if layer:
            filters["memory_layer"] = layer
        if domain_id:
            filters["domain_id"] = domain_id
        if status:
            filters["status"] = status
        if tenant_id:
            filters["tenant_id"] = tenant_id
        if user_id:
            filters["user_id"] = user_id
        if thread_id:
            filters["thread_id"] = thread_id
        try:
            hits = self.retrieval_backend.search_memory(
                query_text=query,
                top_k=max(1, top_k),
                metadata_filters=filters,
            )
        except Exception as exc:
            self._record_audit(
                action="memory_search_failed",
                query=query[:500],
                error=f"{exc.__class__.__name__}: {exc}",
            )
            return {}
        rank: dict[str, int] = {}
        for index, hit in enumerate(hits, start=1):
            payload = dict(hit.get("payload") or {})
            memory_id = str(
                payload.get("memory_id")
                or payload.get("artifact_id")
                or hit.get("id")
                or ""
            ).strip()
            if memory_id and memory_id not in rank:
                rank[memory_id] = index
        return rank

    def _path(self, layer: MemoryLayer) -> Path:
        return self.root / f"{layer}.jsonl"

    def _record_audit(self, *, action: str, **payload) -> None:
        append_jsonl(
            self.audit_path,
            {"action": action, "created_at": utc_now().isoformat(), **payload},
        )


__all__ = ["LayeredMemoryStore", "MemoryPromotionError", "MemoryRetrievalBackend"]
