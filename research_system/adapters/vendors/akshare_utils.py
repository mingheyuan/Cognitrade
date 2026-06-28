from __future__ import annotations

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
            return value.item()
        except Exception:
            return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
