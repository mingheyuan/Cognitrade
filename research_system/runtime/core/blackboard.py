from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl
from research_system.schemas.runtime import BlackboardEntry

if TYPE_CHECKING:
    from research_system.runtime.core.artifact_board import ArtifactBoard


class Blackboard:
    def __init__(
        self,
        path: Path,
        *,
        artifact_board: "ArtifactBoard | None" = None,
        persist_legacy_projection: bool = True,
    ) -> None:
        self.path = path
        self.artifact_board = artifact_board
        self.persist_legacy_projection = persist_legacy_projection
        self.default_thread_id: str | None = None
        self.current_node_id: str | None = None

    def bind_thread(self, thread_id: str) -> None:
        self.default_thread_id = str(thread_id or "").strip() or None
        if self.artifact_board is not None:
            self.artifact_board.bind_thread(thread_id)

    def bind_node(self, node_id: str | None) -> None:
        self.current_node_id = str(node_id or "").strip() or None

    def add(self, entry: BlackboardEntry) -> BlackboardEntry:
        if self.default_thread_id and not str(entry.thread_id or "").strip():
            entry = entry.model_copy(update={"thread_id": self.default_thread_id})
        if self.artifact_board is not None:
            self.artifact_board.add_legacy_entry(
                entry,
                responsibility_node_id=self.current_node_id,
            )
        if self.persist_legacy_projection:
            append_jsonl(self.path, entry.model_dump(mode="json"))
        return entry

    def disable_legacy_projection(self) -> None:
        """Keep legacy reads backed by the Board without writing a JSONL mirror."""
        self.persist_legacy_projection = False

    def list_entries(
        self,
        *,
        thread_id: str | None = None,
        kind: str | None = None,
        agent: str | None = None,
        status: str | None = None,
    ) -> list[BlackboardEntry]:
        if self.artifact_board is not None:
            artifacts = self.artifact_board.list_artifacts(
                thread_id=thread_id,
                schema_version="legacy.blackboard.v1",
            )
            rows = [
                BlackboardEntry(
                    entry_id=str(artifact.metadata.get("legacy_entry_id") or artifact.artifact_id),
                    run_id=artifact.run_id,
                    thread_id=artifact.thread_id,
                    request_id=artifact.request_id,
                    kind=artifact.artifact_type,
                    agent=artifact.producer.executor,
                    phase=str(artifact.metadata.get("legacy_phase") or "artifact"),
                    status=artifact.status,
                    payload=artifact.content,
                    source_refs=artifact.source_refs,
                    confidence=artifact.confidence,
                    risk_flags=artifact.risk_flags,
                    created_at=artifact.created_at,
                )
                for artifact in artifacts
            ]
        else:
            rows = [BlackboardEntry.model_validate(row) for row in read_jsonl(self.path)]
        effective_thread_id = str(thread_id or self.default_thread_id or "").strip() or None
        filtered: list[BlackboardEntry] = []
        for row in rows:
            if effective_thread_id is not None and row.thread_id != effective_thread_id:
                continue
            if kind is not None and row.kind != kind:
                continue
            if agent is not None and row.agent != agent:
                continue
            if status is not None and row.status != status:
                continue
            filtered.append(row)
        return filtered
