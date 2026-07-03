from __future__ import annotations

from typing import Any


class MethodologyEvolverMemorySummary:
    def dedupe_memory_context(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for item in items:
            key = (
                str(item.get("memory_type") or ""),
                str(item.get("entity_scope") or item.get("snapshot_id") or item.get("title") or ""),
                str(item.get("snapshot_id") or item.get("summary") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:5]

    def memory_support_summary(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        entity_scopes = {
            str(item.get("entity_scope") or "").strip()
            for item in items
            if str(item.get("entity_scope") or "").strip()
        }
        snapshot_ids = {
            str(item.get("snapshot_id") or "").strip()
            for item in items
            if str(item.get("snapshot_id") or "").strip()
        }
        recorded_ats = sorted(
            {
                value
                for value in (
                    str(item.get("recorded_at") or "").strip()
                    for item in items
                )
                if value
            }
        )
        latest_recorded_at = recorded_ats[-1] if recorded_ats else None
        oldest_recorded_at = recorded_ats[0] if recorded_ats else None
        return {
            "context_count": len(items),
            "entity_scope_count": len(entity_scopes),
            "snapshot_count": len(snapshot_ids),
            "latest_recorded_at": latest_recorded_at,
            "oldest_recorded_at": oldest_recorded_at,
        }

    def average(self, values: list[Any]) -> float | None:
        numeric_values: list[float] = []
        for value in values:
            try:
                numeric_values.append(float(value))
            except Exception:
                continue
        if not numeric_values:
            return None
        return sum(numeric_values) / len(numeric_values)


__all__ = ["MethodologyEvolverMemorySummary"]
