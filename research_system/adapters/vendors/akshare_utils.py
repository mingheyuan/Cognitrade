from __future__ import annotations

import math
import re
from datetime import date, datetime
from time import sleep
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def normalize_ashare_symbol(symbol: str) -> str:
    normalized = str(symbol).strip().lower()
    if normalized.startswith(("sh", "sz", "bj")):
        return normalized
    if normalized.startswith(("6", "9")):
        return f"sh{normalized}"
    if normalized.startswith(("0", "2", "3")):
        return f"sz{normalized}"
    if normalized.startswith(("4", "8")):
        return f"bj{normalized}"
    return normalized


def call_with_retry(func: Callable[[], T], *, retries: int = 3, delay_seconds: float = 1.0) -> T:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return func()
        except Exception as exc:  # pragma: no cover - vendor/network variance
            last_error = exc
            if attempt == retries - 1:
                break
            sleep(delay_seconds * (attempt + 1))
    assert last_error is not None
    raise last_error


def normalize_value(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            normalized = value.item()
            if isinstance(normalized, float) and math.isnan(normalized):
                return None
            return normalized
        except Exception:
            return str(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


_CN_UNIT_MULTIPLIERS: dict[str, float] = {
    "万亿": 1_000_000_000_000.0,
    "亿": 100_000_000.0,
    "万": 10_000.0,
}


def coerce_numeric(value: Any) -> float | None:
    normalized = normalize_value(value)
    if normalized in (None, "", False):
        return None
    if isinstance(normalized, bool):
        return None
    if isinstance(normalized, (int, float)):
        if isinstance(normalized, float) and math.isnan(normalized):
            return None
        return float(normalized)
    text = str(normalized).strip().replace(",", "")
    if not text or text.lower() == "false":
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    if text.startswith("-"):
        negative = True
        text = text[1:].strip()
    percentage = text.endswith("%")
    if percentage:
        text = text[:-1].strip()
    multiplier = 1.0
    for unit, factor in _CN_UNIT_MULTIPLIERS.items():
        if text.endswith(unit):
            multiplier = factor
            text = text[: -len(unit)].strip()
            break
    if not text:
        return None
    compact = text.replace(" ", "")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", compact):
        return None
    parsed = float(compact) * multiplier
    if percentage:
        parsed = parsed / 100.0
    if negative:
        parsed = -parsed
    return parsed
