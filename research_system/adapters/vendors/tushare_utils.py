from __future__ import annotations

from typing import Any

from research_system.adapters.vendors.akshare_utils import normalize_value


def tushare_code(symbol: str) -> str:
    raw = (
        str(symbol)
        .lower()
        .replace("sh", "")
        .replace("sz", "")
        .replace("bj", "")
        .replace(".", "")[-6:]
    )
    exchange = (
        "BJ"
        if raw.startswith(("4", "8"))
        else "SH"
        if raw.startswith(("5", "6", "9"))
        else "SZ"
    )
    return f"{raw}.{exchange}"


def frame_records(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    return [
        {str(key): normalize_value(value) for key, value in row.items()}
        for row in frame.to_dict("records")
    ]


__all__ = ["frame_records", "tushare_code"]
