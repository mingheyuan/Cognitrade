from __future__ import annotations

from typing import Any

from research_system.cli.chat_thread_store_index_support import ChatThreadStoreIndexSupport, generate_thread_id
from research_system.cli.chat_thread_store_record_support import ChatThreadStoreRecordSupport
from research_system.cli.chat_thread_store_turn_support import ChatThreadStoreTurnSupport
from research_system.runtime.settings import Settings


class ChatThreadStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.memory_root / "chat_threads"
        self.index_path = self.root / "index.json"
        self.metadata_root = self.root / "metadata"
        self.turns_root = self.root / "turns"
        self.checkpoints_root = self.root / "checkpoints"
        self.summaries_root = self.root / "summaries"
        self.summary_history_root = self.root / "summary_history"
        self.archives_root = self.root / "archives"
        self.index_support = ChatThreadStoreIndexSupport(self)
        self.turn_support = ChatThreadStoreTurnSupport(self)
        self.record_support = ChatThreadStoreRecordSupport(self)

    def ensure_thread(self, thread_id: str | None) -> str:
        return self.index_support.ensure_thread(thread_id)

    def has_thread(self, *, thread_id: str) -> bool:
        return self.index_support.has_thread(thread_id=thread_id)

    def append_turn(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        turn_type: str,
        intent: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        return self.turn_support.append_turn(
            thread_id=thread_id,
            role=role,
            content=content,
            turn_type=turn_type,
            intent=intent,
            run_id=run_id,
            metadata=metadata,
            recorded_at=recorded_at,
        )

    def read_recent_turns(self, *, thread_id: str, limit: int = 6) -> list[dict[str, Any]]:
        return self.turn_support.read_recent_turns(thread_id=thread_id, limit=limit)

    def read_all_turns(self, *, thread_id: str) -> list[dict[str, Any]]:
        return self.turn_support.read_all_turns(thread_id=thread_id)

    def replace_turns(self, *, thread_id: str, rows: list[dict[str, Any]]) -> None:
        self.turn_support.replace_turns(thread_id=thread_id, rows=rows)

    def list_threads(
        self,
        *,
        limit: int = 20,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[dict[str, Any]]:
        return self.index_support.list_threads(
            limit=limit,
            include_archived=include_archived,
            archived_only=archived_only,
        )

    def load_thread_summary(self, *, thread_id: str) -> dict[str, Any] | None:
        return self.record_support.load_thread_summary(thread_id=thread_id)

    def load_thread_checkpoint(self, *, thread_id: str) -> dict[str, Any] | None:
        return self.record_support.load_thread_checkpoint(thread_id=thread_id)

    def load_thread_metadata(self, *, thread_id: str) -> dict[str, Any] | None:
        return self.record_support.load_thread_metadata(thread_id=thread_id)

    def ensure_thread_metadata(self, *, thread_id: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.record_support.ensure_thread_metadata(thread_id=thread_id, defaults=defaults)

    def write_thread_metadata(self, *, thread_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return self.record_support.write_thread_metadata(thread_id=thread_id, metadata=metadata)

    def archive_thread(
        self,
        *,
        thread_id: str,
        archived_at: str,
        archived_reason: str | None = None,
    ) -> dict[str, Any]:
        return self.record_support.archive_thread(
            thread_id=thread_id,
            archived_at=archived_at,
            archived_reason=archived_reason,
        )

    def unarchive_thread(
        self,
        *,
        thread_id: str,
        unarchived_at: str,
    ) -> dict[str, Any]:
        return self.record_support.unarchive_thread(
            thread_id=thread_id,
            unarchived_at=unarchived_at,
        )

    def write_thread_checkpoint(
        self,
        *,
        thread_id: str,
        session_state: dict[str, Any],
    ) -> dict[str, Any]:
        return self.record_support.write_thread_checkpoint(
            thread_id=thread_id,
            session_state=session_state,
        )

    def write_thread_summary(
        self,
        *,
        thread_id: str,
        summary_text: str,
        last_turn_at: str | None = None,
        source_turn_count: int | None = None,
        metadata: dict[str, Any] | None = None,
        covered_turn_count: int | None = None,
        delta_turn_count: int | None = None,
    ) -> dict[str, Any]:
        return self.record_support.write_thread_summary(
            thread_id=thread_id,
            summary_text=summary_text,
            last_turn_at=last_turn_at,
            source_turn_count=source_turn_count,
            metadata=metadata,
            covered_turn_count=covered_turn_count,
            delta_turn_count=delta_turn_count,
        )

    def read_recent_summaries(self, *, thread_id: str, limit: int = 2) -> list[dict[str, Any]]:
        return self.record_support.read_recent_summaries(thread_id=thread_id, limit=limit)

    def archive_turns(self, *, thread_id: str, rows: list[dict[str, Any]]) -> None:
        self.turn_support.archive_turns(thread_id=thread_id, rows=rows)

    def read_archived_turns(self, *, thread_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        return self.turn_support.read_archived_turns(thread_id=thread_id, limit=limit)
