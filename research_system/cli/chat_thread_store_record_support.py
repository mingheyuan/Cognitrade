from __future__ import annotations

from typing import Any

from research_system.runtime.jsonl import append_jsonl, read_json, read_jsonl, write_json


class ChatThreadStoreRecordSupport:
    def __init__(self, store) -> None:
        self.store = store

    def load_thread_summary(self, *, thread_id: str) -> dict[str, Any] | None:
        history = self.read_recent_summaries(thread_id=thread_id, limit=1)
        if history:
            return history[-1]
        path = self.store.summaries_root / f"{thread_id}.json"
        payload = read_json(path)
        return payload if isinstance(payload, dict) else None

    def load_thread_checkpoint(self, *, thread_id: str) -> dict[str, Any] | None:
        path = self.store.checkpoints_root / f"{thread_id}.json"
        payload = read_json(path)
        return payload if isinstance(payload, dict) else None

    def load_thread_metadata(self, *, thread_id: str) -> dict[str, Any] | None:
        path = self.store.metadata_root / f"{thread_id}.json"
        payload = read_json(path)
        return payload if isinstance(payload, dict) else None

    def ensure_thread_metadata(self, *, thread_id: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        existing = self.load_thread_metadata(thread_id=thread_id) or {"thread_id": thread_id}
        changed = False
        for key, value in dict(defaults or {}).items():
            if existing.get(key) in (None, "") and value not in (None, ""):
                existing[key] = value
                changed = True
        if changed or existing.get("thread_id") != thread_id:
            existing["thread_id"] = thread_id
            self.write_thread_metadata(thread_id=thread_id, metadata=existing)
        return existing

    def write_thread_metadata(self, *, thread_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        path = self.store.metadata_root / f"{thread_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"thread_id": thread_id, **dict(metadata)}
        write_json(path, payload)
        return payload

    def archive_thread(
        self,
        *,
        thread_id: str,
        archived_at: str,
        archived_reason: str | None = None,
    ) -> dict[str, Any]:
        metadata = self.ensure_thread_metadata(
            thread_id=thread_id,
            defaults={
                "archived": False,
                "archived_at": None,
                "unarchived_at": None,
            },
        )
        metadata["archived"] = True
        metadata["archived_at"] = archived_at
        metadata["unarchived_at"] = None
        if archived_reason not in (None, ""):
            metadata["archived_reason"] = archived_reason
        return self.write_thread_metadata(thread_id=thread_id, metadata=metadata)

    def unarchive_thread(
        self,
        *,
        thread_id: str,
        unarchived_at: str,
    ) -> dict[str, Any]:
        metadata = self.ensure_thread_metadata(
            thread_id=thread_id,
            defaults={
                "archived": False,
                "archived_at": None,
                "unarchived_at": None,
            },
        )
        metadata["archived"] = False
        metadata["unarchived_at"] = unarchived_at
        return self.write_thread_metadata(thread_id=thread_id, metadata=metadata)

    def write_thread_checkpoint(
        self,
        *,
        thread_id: str,
        session_state: dict[str, Any],
    ) -> dict[str, Any]:
        path = self.store.checkpoints_root / f"{thread_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "thread_id": thread_id,
            "session_state": session_state,
        }
        write_json(path, payload)
        return payload

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
        path = self.store.summaries_root / f"{thread_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "thread_id": thread_id,
            "summary_text": summary_text,
            "last_turn_at": last_turn_at,
            "source_turn_count": source_turn_count,
            "covered_turn_count": covered_turn_count,
            "delta_turn_count": delta_turn_count,
            "metadata": metadata or {},
        }
        write_json(path, payload)
        append_jsonl(self.store.summary_history_root / f"{thread_id}.jsonl", payload)
        return payload

    def read_recent_summaries(self, *, thread_id: str, limit: int = 2) -> list[dict[str, Any]]:
        path = self.store.summary_history_root / f"{thread_id}.jsonl"
        rows = read_jsonl(path)
        if limit <= 0:
            return []
        return rows[-limit:]
