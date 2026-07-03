from __future__ import annotations

from typing import Any


class MethodologyEvolverProtocolSupport:
    def protocol_value(self, row: dict[str, Any], key: str) -> str | None:
        content = row.get("content") if isinstance(row.get("content"), dict) else {}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        content_value = str(content.get(key) or "").strip() if isinstance(content, dict) else ""
        if content_value:
            return content_value
        metadata_value = str(metadata.get(key) or "").strip() if isinstance(metadata, dict) else ""
        return metadata_value or None


__all__ = ["MethodologyEvolverProtocolSupport"]
