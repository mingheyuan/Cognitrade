from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from threading import RLock
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_value


_FUND_CODE_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_CODE_KEYS = ("基金代码", "代码", "fund_code", "code")
_NAME_KEYS = ("基金简称", "基金名称", "名称", "fund_name", "name")
_TYPE_KEYS = ("基金类型", "类型", "fund_type", "type")


@dataclass(frozen=True)
class FundResolution:
    fund_codes: list[str]
    matches: list[dict[str, Any]]
    ambiguous_terms: list[str]
    source: str
    provider_error: str | None = None


class FundSecurityMaster:
    """Resolve Chinese public-fund codes and names from a real provider catalog."""

    _cached_rows: list[dict[str, Any]] | None = None
    _cached_at: datetime | None = None
    _cache_lock = RLock()
    _cache_ttl = timedelta(hours=6)

    def __init__(
        self,
        *,
        catalog_loader: Callable[[], Any] | None = None,
    ) -> None:
        self.catalog_loader = catalog_loader

    def resolve(
        self,
        query: str,
        *,
        supplied_codes: Iterable[object] = (),
        allow_network: bool = True,
    ) -> FundResolution:
        requested_codes = self._unique_codes(
            [*supplied_codes, *_FUND_CODE_PATTERN.findall(str(query or ""))]
        )
        if not allow_network:
            return FundResolution([], [], [], "fund_catalog_disabled")

        try:
            rows = self.rows()
        except Exception as exc:
            return FundResolution(
                [],
                [],
                [],
                "fund_catalog_unavailable",
                exc.__class__.__name__,
            )

        by_code = {str(row["fund_code"]): row for row in rows if row.get("fund_code")}
        fund_codes: list[str] = []
        matches: list[dict[str, Any]] = []
        for code in requested_codes:
            row = by_code.get(code)
            if row is None:
                continue
            fund_codes.append(code)
            matches.append({**row, "matched_text": code, "source": "akshare_fund_catalog"})

        normalized_query = self._normalize_name(query)
        name_matches: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            normalized_name = self._normalize_name(row.get("fund_name"))
            if len(normalized_name) >= 2 and normalized_name in normalized_query:
                name_matches.setdefault(normalized_name, []).append(row)

        ambiguous: list[str] = []
        occupied: set[str] = set()
        for name in sorted(name_matches, key=len, reverse=True):
            if any(name in existing for existing in occupied):
                continue
            candidates = name_matches[name]
            codes = {str(item.get("fund_code") or "") for item in candidates}
            if len(codes) != 1:
                ambiguous.append(name)
                continue
            row = candidates[0]
            code = str(row["fund_code"])
            occupied.add(name)
            if code not in fund_codes:
                fund_codes.append(code)
            matches.append({**row, "matched_text": name, "source": "akshare_fund_catalog"})

        return FundResolution(
            fund_codes=fund_codes,
            matches=self._dedupe_matches(matches),
            ambiguous_terms=ambiguous,
            source="akshare_fund_catalog",
        )

    def rows(self) -> list[dict[str, Any]]:
        if self.catalog_loader is not None:
            return self._normalize_catalog(self.catalog_loader())
        now = datetime.now(UTC)
        with self._cache_lock:
            if (
                self._cached_rows is not None
                and self._cached_at is not None
                and now - self._cached_at <= self._cache_ttl
            ):
                return [dict(row) for row in self._cached_rows]
        frame = call_with_retry(ak.fund_name_em, retries=2, delay_seconds=0.25)
        rows = self._normalize_catalog(frame)
        with self._cache_lock:
            type(self)._cached_rows = rows
            type(self)._cached_at = now
        return [dict(row) for row in rows]

    @classmethod
    def is_known_fund_code(cls, code: object, rows: Iterable[dict[str, Any]]) -> bool:
        normalized = cls.normalize_code(code)
        return bool(normalized and any(str(row.get("fund_code") or "") == normalized for row in rows))

    @staticmethod
    def normalize_code(value: object) -> str:
        text = str(value or "").strip().upper()
        if "." in text:
            text = text.split(".", 1)[0]
        return text.zfill(6) if text.isdigit() and len(text) <= 6 else ""

    @classmethod
    def _normalize_catalog(cls, payload: Any) -> list[dict[str, Any]]:
        if payload is None:
            return []
        if hasattr(payload, "to_dict"):
            raw_rows = payload.to_dict("records")
        else:
            raw_rows = list(payload)
        rows: list[dict[str, Any]] = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            code = cls.normalize_code(cls._first(raw, _CODE_KEYS))
            name = str(cls._first(raw, _NAME_KEYS) or "").strip()
            if not code or not name:
                continue
            rows.append(
                {
                    "fund_code": code,
                    "fund_name": name,
                    "fund_type": str(cls._first(raw, _TYPE_KEYS) or "").strip() or None,
                    "raw": {str(key): normalize_value(value) for key, value in raw.items()},
                }
            )
        return rows

    @staticmethod
    def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            if key in row and row[key] not in (None, ""):
                return row[key]
        return None

    @staticmethod
    def _normalize_name(value: object) -> str:
        return re.sub(r"[\s\-—_（）()·./]", "", str(value or "").casefold())

    @classmethod
    def _unique_codes(cls, values: Iterable[object]) -> list[str]:
        result: list[str] = []
        for value in values:
            code = cls.normalize_code(value)
            if code and code not in result:
                result.append(code)
        return result

    @staticmethod
    def _dedupe_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (str(row.get("fund_code") or ""), str(row.get("matched_text") or ""))
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result


__all__ = ["FundResolution", "FundSecurityMaster"]
