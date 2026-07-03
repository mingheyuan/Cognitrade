from __future__ import annotations

from typing import Any


class SessionHistoryDebugSupport:
    def debug_payload(self, result_payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result_payload, dict):
            return {}
        report_payload = result_payload.get("report")
        if isinstance(report_payload, dict):
            debug_payload = report_payload.get("debug")
            if isinstance(debug_payload, dict):
                return debug_payload
        debug_payload = result_payload.get("debug")
        if isinstance(debug_payload, dict):
            return debug_payload
        return {}


__all__ = ["SessionHistoryDebugSupport"]
