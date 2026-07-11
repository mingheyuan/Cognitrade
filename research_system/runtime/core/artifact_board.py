from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any

from research_system.runtime.storage.jsonl import read_json, read_jsonl
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord


_LOCKS_GUARD = RLock()
_LOCKS: dict[str, RLock] = {}
_UNSET = object()


def _path_lock(path: Path) -> RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, RLock())


class ArtifactBoard:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.default_thread_id: str | None = None
        self.invalidated_artifact_ids: set[str] = set()
        self._lock = _path_lock(path)

    def bind_thread(self, thread_id: str) -> None:
        self.default_thread_id = str(thread_id or "").strip() or None

    def bind_invalidated_artifacts(self, artifact_ids: list[str] | set[str]) -> None:
        """Hide stale outputs from active reads while keeping them replayable."""
        self.invalidated_artifact_ids = {
            str(artifact_id).strip()
            for artifact_id in artifact_ids
            if str(artifact_id).strip()
        }

    def _refresh_invalidated_artifacts(self) -> None:
        snapshot = read_json(self.path.parent / "execution_snapshot.json")
        if not isinstance(snapshot, dict):
            return
        self.invalidated_artifact_ids.update(
            str(artifact_id).strip()
            for artifact_id in list(snapshot.get("invalidated_artifact_ids") or [])
            if str(artifact_id).strip()
        )

    def add(self, artifact: ArtifactRecord) -> ArtifactRecord:
        return self.add_many([artifact])[0]

    def add_many(self, artifacts: list[ArtifactRecord]) -> list[ArtifactRecord]:
        """Validate and append one node's output batch under one Board lock."""
        if not artifacts:
            return []
        if self.default_thread_id:
            artifacts = [
                item.model_copy(update={"thread_id": self.default_thread_id})
                if not str(item.thread_id or "").strip()
                else item
                for item in artifacts
            ]
        with self._lock:
            prepared = [self._prepare(item) for item in artifacts]
            batch_ids = {item.artifact_id for item in prepared}
            if len(batch_ids) != len(prepared):
                raise ValueError("artifact batch contains duplicate artifact IDs")
            existing = {item.artifact_id: item for item in self._read_all()}
            known_ids = set(existing) | batch_ids
            normalized: list[ArtifactRecord] = []
            for artifact in prepared:
                if artifact.supersedes_artifact_id:
                    previous = existing.get(artifact.supersedes_artifact_id)
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
                    if parent_id not in known_ids:
                        raise ValueError(f"unknown parent artifact: {parent_id}")
                normalized.append(artifact)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for artifact in normalized:
                    handle.write(json.dumps(artifact.model_dump(mode="json"), ensure_ascii=True))
                    handle.write("\n")
            return normalized

    def _read_all(self) -> list[ArtifactRecord]:
        return [ArtifactRecord.model_validate(row) for row in read_jsonl(self.path)]

    @staticmethod
    def _prepare(artifact: ArtifactRecord) -> ArtifactRecord:
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
                    "responsibility_node_id": producer.node_id
                    or producer.executor
                    or "task_intake"
                }
            )
        risk_flags = list(artifact.risk_flags)
        confidence = artifact.confidence
        if confidence is None:
            confidence = 0.5
            if "confidence_defaulted" not in risk_flags:
                risk_flags.append("confidence_defaulted")
        return artifact.model_copy(
            update={
                "content_hash": content_hash,
                "producer": producer,
                "confidence": confidence,
                "risk_flags": risk_flags,
            }
        )

    def list_artifacts(
        self,
        *,
        thread_id: str | None | object = _UNSET,
        artifact_type: str | None = None,
        executor: str | None = None,
        capability: str | None = None,
        status: str | None = None,
        run_id: str | None = None,
        node_id: str | None = None,
        schema_version: str | None = None,
        include_invalidated: bool = False,
    ) -> list[ArtifactRecord]:
        self._refresh_invalidated_artifacts()
        with self._lock:
            rows = self._read_all()
        superseded_ids = {
            row.supersedes_artifact_id
            for row in rows
            if row.supersedes_artifact_id
        }
        effective_thread_id = (
            self.default_thread_id
            if thread_id is _UNSET
            else str(thread_id or "").strip() or None
        )
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
            if (
                status == "active"
                and row.artifact_id in self.invalidated_artifact_ids
                and not include_invalidated
            ):
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
