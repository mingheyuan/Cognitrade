from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.schemas.planning import ActionSpec
from research_system.schemas.runtime import BlackboardEntry


class ActionCacheStore:
    def __init__(self, *, run_root: Path) -> None:
        self.run_root = Path(run_root)
        self.runs_root = self.run_root.parent

    def save_snapshot(
        self,
        *,
        action: ActionSpec,
        run_id: str,
        thread_id: str,
        request_id: str,
        blackboard_entries: list[BlackboardEntry],
        result_payload: dict[str, Any] | None = None,
    ) -> Path | None:
        if not self._cache_enabled(action):
            return None
        snapshot_path = self._snapshot_path(
            action=action,
            run_root=self.run_root,
            thread_id=thread_id,
        )
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            snapshot_path,
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "request_id": request_id,
                "node_id": action.node_id,
                "cache_scope": action.cache_scope,
                "cache_key": action.cache_key,
                "action": action.model_dump(mode="json"),
                "blackboard_entries": [
                    entry.model_dump(mode="json")
                    for entry in blackboard_entries
                ],
                "result_payload": result_payload,
            },
        )
        return snapshot_path

    def load_snapshot(
        self,
        *,
        action: ActionSpec,
        thread_id: str,
        source_run_root: Path | None = None,
    ) -> dict[str, Any] | None:
        if not self._cache_enabled(action):
            return None
        candidate_roots = [Path(source_run_root)] if source_run_root is not None else [self.run_root]
        for root in candidate_roots:
            snapshot_path = self._snapshot_path(
                action=action,
                run_root=root,
                thread_id=thread_id,
            )
            payload = read_json(snapshot_path)
            if isinstance(payload, dict):
                return payload
        return None

    def restore_snapshot(
        self,
        *,
        action: ActionSpec,
        source_run_root: Path,
        target_run_id: str,
        target_thread_id: str,
        target_request_id: str,
        thread_id_hint: str,
    ) -> dict[str, Any] | None:
        payload = self.load_snapshot(
            action=action,
            thread_id=thread_id_hint,
            source_run_root=source_run_root,
        )
        if payload is None:
            return None
        restored_entries: list[BlackboardEntry] = []
        for row in payload.get("blackboard_entries") or []:
            if not isinstance(row, dict):
                continue
            entry = BlackboardEntry.model_validate(row)
            source_refs = list(entry.source_refs or [])
            source_refs.append(
                {
                    "type": "dag_cache_restore",
                    "source_run_id": payload.get("run_id"),
                    "source_thread_id": payload.get("thread_id"),
                    "source_node_id": action.node_id,
                    "cache_scope": action.cache_scope,
                    "cache_key": action.cache_key,
                }
            )
            restored_entries.append(
                entry.model_copy(
                    update={
                        "run_id": target_run_id,
                        "thread_id": target_thread_id,
                        "request_id": target_request_id,
                        "source_refs": source_refs,
                    }
                )
            )
        return {
            "run_id": payload.get("run_id"),
            "thread_id": payload.get("thread_id"),
            "node_id": payload.get("node_id"),
            "blackboard_entries": restored_entries,
            "result_payload": payload.get("result_payload") if isinstance(payload.get("result_payload"), dict) else None,
        }

    def snapshot_exists(
        self,
        *,
        action: ActionSpec,
        thread_id: str,
        source_run_root: Path | None = None,
    ) -> bool:
        return self.load_snapshot(
            action=action,
            thread_id=thread_id,
            source_run_root=source_run_root,
        ) is not None

    def _snapshot_path(
        self,
        *,
        action: ActionSpec,
        run_root: Path,
        thread_id: str,
    ) -> Path:
        scope = str(action.cache_scope or "").strip() or "run"
        cache_key = str(action.cache_key or action.node_id or "action").strip() or "action"
        if scope == "run":
            base = Path(run_root) / "artifacts" / "action_cache" / "run"
        elif scope == "thread":
            normalized_thread = self._normalize_token(thread_id or "thread")
            base = self.runs_root / "_action_cache" / "thread" / normalized_thread
        else:
            base = self.runs_root / "_action_cache" / "global"
        return base / f"{self._safe_cache_name(cache_key)}.json"

    def _safe_cache_name(self, value: str) -> str:
        readable = self._normalize_token(value)[:48] or "cache"
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        return f"{readable}-{digest}"

    def _normalize_token(self, value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip()).strip("._-") or "cache"

    def _cache_enabled(self, action: ActionSpec) -> bool:
        return bool(action.replayable and str(action.cache_scope or "").strip())


__all__ = ["ActionCacheStore"]
