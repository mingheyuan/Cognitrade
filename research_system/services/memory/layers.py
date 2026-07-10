from __future__ import annotations

from pathlib import Path

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl
from research_system.schemas.memory import MemoryEvaluation, MemoryLayer, MemoryRecord, utc_now


class MemoryPromotionError(RuntimeError):
    pass


class LayeredMemoryStore:
    """Audited episodic/domain/policy memory with one-way promotion gates."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.root / "promotion_audit.jsonl"

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
        if domain_id is not None:
            rows = [row for row in rows if row.domain_id == domain_id]
        if status is not None:
            rows = [row for row in rows if row.status == status]
        return rows

    def get(self, memory_id: str) -> MemoryRecord | None:
        for record in reversed(self.list()):
            if record.memory_id == memory_id:
                return record
        return None

    def _append(self, record: MemoryRecord, *, action: str) -> MemoryRecord:
        append_jsonl(self._path(record.layer), record.model_dump(mode="json"))
        self._record_audit(
            action=action,
            memory_id=record.memory_id,
            layer=record.layer,
            status=record.status,
        )
        return record

    def _path(self, layer: MemoryLayer) -> Path:
        return self.root / f"{layer}.jsonl"

    def _record_audit(self, *, action: str, **payload) -> None:
        append_jsonl(
            self.audit_path,
            {"action": action, "created_at": utc_now().isoformat(), **payload},
        )


__all__ = ["LayeredMemoryStore", "MemoryPromotionError"]
