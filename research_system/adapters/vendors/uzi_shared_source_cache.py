from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from io import StringIO
from pathlib import Path
from threading import Event, RLock
from typing import Any

import pandas as pd

_CACHE_SCHEMA_VERSION = "uzi-shared-source-cache-v1"
_ORIGINAL_ATTR = "__cognitrade_uzi_shared_source_original__"


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    value: Any


class UziSharedSourceCache:
    """Cache exact high-cost provider responses without changing UZI output.

    The pinned UZI fetchers request several full-market tables once per stock.
    A ten-holding batch would otherwise download the same quarterly/universe
    table ten times.  This boundary keeps the original AkShare function as the
    sole producer, gives concurrent callers single-flight semantics, and
    returns defensive copies of the unmodified provider result.
    """

    def __init__(self, root: Path, *, waiter_timeout_seconds: float = 1200.0) -> None:
        self.root = Path(root).resolve()
        self.waiter_timeout_seconds = max(float(waiter_timeout_seconds), 1.0)
        self._lock = RLock()
        self._memory: dict[str, _CacheEntry] = {}
        self._inflight: dict[str, Event] = {}

    def wrap(
        self,
        source_id: str,
        function: Callable[..., Any],
        *,
        ttl_seconds: float,
        empty_ttl_seconds: float = 300.0,
    ) -> Callable[..., Any]:
        original = getattr(function, _ORIGINAL_ATTR, function)

        @wraps(original)
        def cached_call(*args: Any, **kwargs: Any) -> Any:
            key = self._key(source_id, args, kwargs)
            while True:
                now = time.time()
                with self._lock:
                    entry = self._memory.get(key)
                    if entry is not None and entry.expires_at > now:
                        return _clone(entry.value)
                    if entry is not None:
                        self._memory.pop(key, None)
                    inflight = self._inflight.get(key)
                    if inflight is None:
                        inflight = Event()
                        self._inflight[key] = inflight
                        leader = True
                    else:
                        leader = False
                if leader:
                    break
                if not inflight.wait(self.waiter_timeout_seconds):
                    raise TimeoutError(
                        f"shared source wait timed out: {source_id}"
                    )

            try:
                disk_entry = self._read(key)
                if disk_entry is not None:
                    with self._lock:
                        self._memory[key] = disk_entry
                    return _clone(disk_entry.value)

                value = original(*args, **kwargs)
                if value is None:
                    return value
                effective_ttl = (
                    min(float(ttl_seconds), float(empty_ttl_seconds))
                    if _empty(value)
                    else float(ttl_seconds)
                )
                entry = _CacheEntry(
                    expires_at=time.time() + max(effective_ttl, 1.0),
                    value=_clone(value),
                )
                with self._lock:
                    self._memory[key] = entry
                self._write(key, source_id, entry)
                return value
            finally:
                with self._lock:
                    event = self._inflight.pop(key, None)
                    if event is not None:
                        event.set()

        setattr(cached_call, _ORIGINAL_ATTR, original)
        return cached_call

    def _key(self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        payload = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "source_id": source_id,
            "args": _stable_json(args),
            "kwargs": _stable_json(kwargs),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def _read(self, key: str) -> _CacheEntry | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
                return None
            expires_at = float(payload.get("expires_at") or 0.0)
            if expires_at <= time.time():
                return None
            value = _deserialize(payload)
            return _CacheEntry(expires_at=expires_at, value=value)
        except (KeyError, OSError, TypeError, ValueError):
            return None

    def _write(self, key: str, source_id: str, entry: _CacheEntry) -> None:
        try:
            payload = {
                "schema_version": _CACHE_SCHEMA_VERSION,
                "source_id": source_id,
                "created_at": time.time(),
                "expires_at": entry.expires_at,
                **_serialize(entry.value),
            }
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(key)
            temporary = path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
        except (OSError, TypeError, ValueError):
            # A disk-cache failure must never alter the original provider call.
            return


_SOURCE_TTLS: dict[str, float] = {
    # UZI's per-stock basic fallback can otherwise pull these full-market
    # tables once for every holding.  The short quote TTL preserves UZI's
    # real-time freshness class while still collapsing a concurrent batch.
    "stock_info_a_code_name": 7 * 24 * 60 * 60,
    "stock_zh_a_spot_em": 60,
    # Peer fetches for holdings in the same sector use the same constituent
    # table.  Five minutes matches UZI's intraday-data freshness class.
    "stock_board_industry_cons_em": 5 * 60,
    # Quarterly full-market institutional holdings; one response is filtered
    # by every stock in the batch.
    "stock_report_fund_hold_detail": 24 * 60 * 60,
    # CNInfo returns one market-wide industry table for a given publication
    # date, even though UZI consumes a different row for each stock.
    "stock_industry_pe_ratio_cninfo": 24 * 60 * 60,
    # Historical main-contract series are shared by companies in the same
    # industry.  Match UZI's daily-data freshness class.
    "futures_main_sina": 2 * 60 * 60,
    # These universe endpoints are already cached by UZI on disk.  Wrapping
    # them adds the missing concurrent single-flight guarantee.
    "stock_dzjy_mrtj": 24 * 60 * 60,
    "stock_restricted_release_summary_em": 24 * 60 * 60,
    "stock_restricted_release_detail_em": 24 * 60 * 60,
    "stock_margin_detail_szse": 2 * 60 * 60,
    "stock_margin_detail_sse": 2 * 60 * 60,
}
_INSTALL_LOCK = RLock()
_INSTALLED_ROOT: Path | None = None
_MANAGER: UziSharedSourceCache | None = None


def install_uzi_shared_source_cache(workspace_root: Path) -> UziSharedSourceCache:
    """Patch only high-cost AkShare transports used by pinned UZI fetchers."""

    import akshare as ak  # type: ignore

    global _INSTALLED_ROOT, _MANAGER
    cache_root = Path(workspace_root).resolve() / "cache" / "uzi_shared_sources"
    with _INSTALL_LOCK:
        if _MANAGER is not None and _INSTALLED_ROOT == cache_root:
            return _MANAGER
        manager = UziSharedSourceCache(
            cache_root,
            waiter_timeout_seconds=float(
                os.environ.get("UZI_SHARED_SOURCE_WAIT_TIMEOUT_SECONDS", "1200")
            ),
        )
        for source_id, ttl_seconds in _SOURCE_TTLS.items():
            current = getattr(ak, source_id, None)
            if not callable(current):
                continue
            setattr(
                ak,
                source_id,
                manager.wrap(source_id, current, ttl_seconds=ttl_seconds),
            )
        _MANAGER = manager
        _INSTALLED_ROOT = cache_root
        return manager


def _clone(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return value.copy(deep=True)
    return deepcopy(value)


def _empty(value: Any) -> bool:
    empty = getattr(value, "empty", None)
    if isinstance(empty, bool):
        return empty
    if isinstance(value, (dict, list, tuple, set, str, bytes)):
        return not value
    return False


def _serialize(value: Any) -> dict[str, Any]:
    if isinstance(value, pd.DataFrame):
        return {
            "value_kind": "pandas_dataframe_table",
            "value": value.to_json(
                orient="table",
                date_format="iso",
                date_unit="ms",
                force_ascii=False,
            ),
        }
    return {"value_kind": "json", "value": _stable_json(value)}


def _deserialize(payload: dict[str, Any]) -> Any:
    if payload.get("value_kind") == "pandas_dataframe_table":
        return pd.read_json(StringIO(str(payload.get("value") or "")), orient="table")
    return payload.get("value")


def _stable_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable_json(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_json(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _stable_json(item())
        except (AttributeError, TypeError, ValueError):
            pass
    return str(value)


__all__ = ["UziSharedSourceCache", "install_uzi_shared_source_cache"]
