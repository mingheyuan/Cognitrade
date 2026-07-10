from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord


_LOCKS_GUARD = RLock()
_LOCKS: dict[str, RLock] = {}


def _path_lock(path: Path) -> RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, RLock())


class ArtifactBoard:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.default_thread_id: str | None = None
        self._lock = _path_lock(path)

    def bind_thread(self, thread_id: str) -> None:
        self.default_thread_id = str(thread_id or "").strip() or None

    def add(self, artifact: ArtifactRecord) -> ArtifactRecord:
        if self.default_thread_id and not str(artifact.thread_id or "").strip():
            artifact = artifact.model_copy(update={"thread_id": self.default_thread_id})
        content_hash = hashlib.sha256(
            json.dumps(
                artifact.content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        producer = artifact.producer
        if not producer.responsibility_node_id:
            producer = producer.model_copy(
                update={
                    "responsibility_node_id": producer.node_id or producer.executor or "task_intake"
                }
            )
        risk_flags = list(artifact.risk_flags)
        confidence = artifact.confidence
        if confidence is None:
            confidence = 0.5
            if "confidence_defaulted" not in risk_flags:
                risk_flags.append("confidence_defaulted")
        artifact = artifact.model_copy(
            update={
                "content_hash": content_hash,
                "producer": producer,
                "confidence": confidence,
                "risk_flags": risk_flags,
            }
        )
        with self._lock:
            if artifact.supersedes_artifact_id:
                previous = self.get(artifact.supersedes_artifact_id)
                if previous is None:
                    raise ValueError(
                        f"unknown superseded artifact: {artifact.supersedes_artifact_id}"
                    )
                if previous.artifact_type != artifact.artifact_type:
                    raise ValueError("an artifact can only supersede the same artifact type")
                if artifact.artifact_version <= previous.artifact_version:
                    artifact = artifact.model_copy(
                        update={"artifact_version": previous.artifact_version + 1}
                    )
            for parent_id in artifact.parent_artifact_ids:
                if self.get(parent_id) is None:
                    raise ValueError(f"unknown parent artifact: {parent_id}")
            append_jsonl(self.path, artifact.model_dump(mode="json"))
        return artifact

    def add_legacy_entry(
        self,
        entry: Any,
        *,
        responsibility_node_id: str | None = None,
    ) -> ArtifactRecord:
        parent_artifact_ids = [
            str(source_ref.get("artifact_id") or "").strip()
            for source_ref in list(entry.source_refs or [])
            if str(source_ref.get("artifact_id") or "").strip()
        ]
        artifact = ArtifactRecord(
            run_id=entry.run_id,
            thread_id=entry.thread_id,
            request_id=entry.request_id,
            artifact_type=entry.kind,
            schema_version="legacy.blackboard.v1",
            producer=ArtifactProducer(
                executor=entry.agent,
                capability=f"legacy.blackboard.{entry.kind}",
                node_id=responsibility_node_id,
                subagent_type=None,
                domain_id="finance_ashare",
                responsibility_node_id=(
                    responsibility_node_id or f"legacy:{entry.phase}:{entry.agent}"
                ),
            ),
            content=dict(entry.payload or {}),
            source_refs=list(entry.source_refs or []),
            parent_artifact_ids=parent_artifact_ids,
            confidence=entry.confidence,
            risk_flags=list(entry.risk_flags or []),
            status=entry.status,
            metadata={
                "legacy_entry_id": entry.entry_id,
                "legacy_phase": entry.phase,
                "responsibility_node_id": responsibility_node_id,
            },
            created_at=entry.created_at,
        )
        return self.add(artifact)

    def list_artifacts(
        self,
        *,
        thread_id: str | None = None,
        artifact_type: str | None = None,
        executor: str | None = None,
        capability: str | None = None,
        status: str | None = None,
        run_id: str | None = None,
        node_id: str | None = None,
        schema_version: str | None = None,
    ) -> list[ArtifactRecord]:
        with self._lock:
            rows = [ArtifactRecord.model_validate(row) for row in read_jsonl(self.path)]
        superseded_ids = {
            row.supersedes_artifact_id
            for row in rows
            if row.supersedes_artifact_id
        }
        effective_thread_id = str(thread_id or self.default_thread_id or "").strip() or None
        filtered: list[ArtifactRecord] = []
        for row in rows:
            if effective_thread_id is not None and row.thread_id != effective_thread_id:
                continue
            if artifact_type is not None and row.artifact_type != artifact_type:
                continue
            if executor is not None and row.producer.executor != executor:
                continue
            if capability is not None and row.producer.capability != capability:
                continue
            if status is not None and row.status != status:
                continue
            if status == "active" and row.artifact_id in superseded_ids:
                continue
            if run_id is not None and row.run_id != run_id:
                continue
            if node_id is not None and row.producer.node_id != node_id:
                continue
            if schema_version is not None and row.schema_version != schema_version:
                continue
            filtered.append(row)
        return filtered

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        for artifact in reversed(self.list_artifacts(thread_id=None)):
            if artifact.artifact_id == artifact_id:
                return artifact
        return None

    def resolve(self, artifact_ids: list[str]) -> list[ArtifactRecord]:
        artifacts: list[ArtifactRecord] = []
        for artifact_id in artifact_ids:
            artifact = self.get(artifact_id)
            if artifact is None:
                raise KeyError(f"unknown artifact: {artifact_id}")
            artifacts.append(artifact)
        return artifacts

    def lineage(self, artifact_id: str) -> list[ArtifactRecord]:
        root = self.get(artifact_id)
        if root is None:
            raise KeyError(f"unknown artifact: {artifact_id}")
        resolved: list[ArtifactRecord] = []
        seen: set[str] = set()
        stack = list(root.parent_artifact_ids)
        while stack:
            parent_id = stack.pop()
            if parent_id in seen:
                continue
            seen.add(parent_id)
            parent = self.get(parent_id)
            if parent is None:
                raise ValueError(f"broken artifact lineage: {artifact_id}->{parent_id}")
            resolved.append(parent)
            stack.extend(parent.parent_artifact_ids)
        return resolved


__all__ = ["ArtifactBoard"]
