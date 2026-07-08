from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from research_system.runtime.storage.jsonl import read_json, write_json


class SubagentDagCacheStore:
    def __init__(self, *, run_root: Path) -> None:
        self.run_root = Path(run_root)
        self.runs_root = self.run_root.parent

    def save_snapshot(
        self,
        *,
        dag_id: str,
        node_id: str,
        cache_scope: str | None,
        cache_key: str | None,
        run_id: str,
        thread_id: str,
        request_id: str,
        status: str,
        attempts: int,
        outputs: dict[str, Any],
        record_payload: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
    ) -> Path | None:
        if not self._cache_enabled(cache_scope):
            return None
        snapshot_path = self._snapshot_path(
            dag_id=dag_id,
            node_id=node_id,
            cache_scope=cache_scope,
            cache_key=cache_key,
            run_root=self.run_root,
            thread_id=thread_id,
        )
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            snapshot_path,
            {
                "dag_id": dag_id,
                "node_id": node_id,
                "cache_scope": cache_scope,
                "cache_key": cache_key,
                "run_id": run_id,
                "thread_id": thread_id,
                "request_id": request_id,
                "status": status,
                "attempts": attempts,
                "outputs": dict(outputs),
                "record": dict(record_payload or {}),
                "metadata": dict(metadata or {}),
            },
        )
        return snapshot_path

    def load_snapshot(
        self,
        *,
        dag_id: str,
        node_id: str,
        cache_scope: str | None,
        cache_key: str | None,
        thread_id: str,
        source_run_root: Path | None = None,
    ) -> dict[str, Any] | None:
        if not self._cache_enabled(cache_scope):
            return None
        candidate_roots = [Path(source_run_root)] if source_run_root is not None else [self.run_root]
        for root in candidate_roots:
            snapshot_path = self._snapshot_path(
                dag_id=dag_id,
                node_id=node_id,
                cache_scope=cache_scope,
                cache_key=cache_key,
                run_root=root,
                thread_id=thread_id,
            )
            payload = read_json(snapshot_path)
            if isinstance(payload, dict):
                return payload
        return None

    def _snapshot_path(
        self,
        *,
        dag_id: str,
        node_id: str,
        cache_scope: str | None,
        cache_key: str | None,
        run_root: Path,
        thread_id: str,
    ) -> Path:
        scope = str(cache_scope or "").strip() or "run"
        normalized_dag = self._safe_bucket_name(dag_id)
        normalized_key = self._safe_cache_name(f"{node_id}:{str(cache_key or node_id).strip() or node_id}")
        if scope == "run":
            base = Path(run_root) / "artifacts" / "subagent_dag_cache" / "run" / normalized_dag
        elif scope == "thread":
            normalized_thread = self._safe_bucket_name(thread_id or "thread")
            base = self.runs_root / "_subagent_dag_cache" / "thread" / normalized_thread / normalized_dag
        else:
            base = self.runs_root / "_subagent_dag_cache" / "global" / normalized_dag
        return base / f"{normalized_key}.json"

    def _safe_bucket_name(self, value: str) -> str:
        readable = self._safe_token(value)[:48] or "cache"
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        return f"{readable}-{digest}"

    def _safe_cache_name(self, value: str) -> str:
        readable = self._safe_token(value)[:40] or "node"
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        return f"{readable}-{digest}"

    def _safe_token(self, value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip()).strip("._-") or "cache"

    def _cache_enabled(self, cache_scope: str | None) -> bool:
        return bool(str(cache_scope or "").strip())


__all__ = ["SubagentDagCacheStore"]
