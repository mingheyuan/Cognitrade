from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from threading import RLock
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_utils import call_with_retry, coerce_numeric, normalize_value


_CODE_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_DEFAULT_ALIASES = {
    "贵州茅台": "600519",
    "茅台": "600519",
    "宁德时代": "300750",
    "比亚迪": "002594",
    "京东方a": "000725",
    "寒武纪": "688256",
    "新易盛": "300502",
    "平安银行": "000001",
    "招商银行": "600036",
    "宁波银行": "002142",
    "兴业银行": "601166",
    "江苏银行": "600919",
    "成都银行": "601838",
}
_CODE_KEYS = ("代码", "股票代码", "证券代码", "code", "symbol")
_NAME_KEYS = ("名称", "股票简称", "证券简称", "name")
_AMOUNT_KEYS = ("成交额", "amount", "turnover")
_MARKET_CAP_KEYS = ("总市值", "market_cap", "marketCapitalization")
_PRICE_KEYS = ("最新价", "price", "close")
_CHANGE_KEYS = ("涨跌幅", "pct_change", "change_percent")


@dataclass(frozen=True)
class SecurityResolution:
    symbols: list[str]
    matches: list[dict[str, Any]]
    ambiguous_terms: list[str]
    source: str


class AShareSecurityMaster:
    """Finance Pack-owned security-name resolution and candidate-universe access."""

    _cached_rows: list[dict[str, Any]] | None = None
    _cached_at: datetime | None = None
    _cache_lock = RLock()
    _cache_ttl = timedelta(minutes=10)

    def __init__(
        self,
        *,
        universe_loader: Callable[[], Iterable[dict[str, Any]]] | None = None,
        aliases: dict[str, str] | None = None,
    ) -> None:
        self.universe_loader = universe_loader
        self.aliases = {
            self._normalize_name(alias): self._normalize_symbol(symbol)
            for alias, symbol in {**_DEFAULT_ALIASES, **dict(aliases or {})}.items()
        }

    def resolve(
        self,
        query: str,
        *,
        supplied_symbols: Iterable[object] = (),
        allow_network: bool = True,
    ) -> SecurityResolution:
        symbols: list[str] = []
        matches: list[dict[str, Any]] = []
        for value in supplied_symbols:
            symbol = self._normalize_symbol(value)
            if symbol:
                self._append_symbol(symbols, symbol)
                matches.append({"symbol": symbol, "matched_text": str(value), "source": "structured_input"})
        for symbol in _CODE_PATTERN.findall(query):
            self._append_symbol(symbols, symbol)
            matches.append({"symbol": symbol, "matched_text": symbol, "source": "query_code"})

        normalized_query = self._normalize_name(query)
        for alias, symbol in sorted(self.aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if alias and alias in normalized_query:
                self._append_symbol(symbols, symbol)
                matches.append({"symbol": symbol, "name": alias, "matched_text": alias, "source": "pack_alias"})

        provider_matches: list[dict[str, Any]] = []
        ambiguous_terms: list[str] = []
        if allow_network and self._should_query_provider(query, symbols):
            rows = self.rows()
            provider_matches, ambiguous_terms = self._match_rows(query, rows)
            for match in provider_matches:
                self._append_symbol(symbols, str(match["symbol"]))
            matches.extend(provider_matches)

        source = "provider_security_master" if provider_matches else "pack_alias_or_code"
        return SecurityResolution(
            symbols=symbols,
            matches=self._dedupe_matches(matches),
            ambiguous_terms=ambiguous_terms,
            source=source,
        )

    def candidate_universe(
        self,
        *,
        limit: int,
        supplied_symbols: Iterable[object] = (),
    ) -> list[dict[str, Any]]:
        normalized_limit = max(int(limit), 1)
        supplied = [self._normalize_symbol(value) for value in supplied_symbols]
        supplied = [symbol for symbol in supplied if symbol]
        rows = self.rows()
        if not supplied and not any(row.get("amount") is not None for row in rows):
            market_rows = self._load_market_rows()
            if market_rows:
                rows = market_rows
        by_symbol = {str(row.get("symbol") or ""): row for row in rows}
        if supplied:
            return [
                dict(by_symbol.get(symbol) or {"symbol": symbol, "name": symbol, "source": "structured_input"})
                for symbol in supplied[:normalized_limit]
            ]
        ranked = [row for row in rows if self._eligible_candidate(row)]
        ranked.sort(
            key=lambda row: (
                coerce_numeric(row.get("amount")) or -1.0,
                coerce_numeric(row.get("market_cap")) or -1.0,
                str(row.get("symbol") or ""),
            ),
            reverse=True,
        )
        return [dict(row) for row in ranked[:normalized_limit]]

    def rows(self) -> list[dict[str, Any]]:
        if self.universe_loader is not None:
            return [self._normalize_row(dict(row)) for row in self.universe_loader()]
        now = datetime.now(UTC)
        with self._cache_lock:
            if self._cached_rows is not None and self._cached_at is not None:
                if now - self._cached_at <= self._cache_ttl:
                    return [dict(row) for row in self._cached_rows]
        rows = self._load_provider_rows()
        with self._cache_lock:
            type(self)._cached_rows = rows
            type(self)._cached_at = now
        return [dict(row) for row in rows]

    def _load_provider_rows(self) -> list[dict[str, Any]]:
        errors: list[str] = []
        for loader in (
            lambda: call_with_retry(ak.stock_info_a_code_name, retries=2, delay_seconds=0.25),
            lambda: call_with_retry(ak.stock_zh_a_spot_em, retries=2, delay_seconds=0.25),
            lambda: call_with_retry(ak.stock_zh_a_spot, retries=2, delay_seconds=0.25),
        ):
            try:
                frame = loader()
                if frame is not None and not frame.empty:
                    return [self._normalize_row(row) for row in frame.to_dict("records")]
            except Exception as exc:  # provider fallback is intentionally best-effort
                errors.append(f"{exc.__class__.__name__}:{exc}")
        if errors:
            return []
        return []

    def _load_market_rows(self) -> list[dict[str, Any]]:
        for loader in (
            lambda: call_with_retry(ak.stock_zh_a_spot_em, retries=2, delay_seconds=0.25),
            lambda: call_with_retry(ak.stock_zh_a_spot, retries=2, delay_seconds=0.25),
        ):
            try:
                frame = loader()
                if frame is not None and not frame.empty:
                    return [self._normalize_row(row) for row in frame.to_dict("records")]
            except Exception:
                continue
        return []

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        symbol = self._normalize_symbol(self._first(row, _CODE_KEYS))
        name = str(self._first(row, _NAME_KEYS) or symbol).strip()
        return {
            "symbol": symbol,
            "name": name,
            "amount": coerce_numeric(self._first(row, _AMOUNT_KEYS)),
            "market_cap": coerce_numeric(self._first(row, _MARKET_CAP_KEYS)),
            "latest_price": coerce_numeric(self._first(row, _PRICE_KEYS)),
            "pct_change": coerce_numeric(self._first(row, _CHANGE_KEYS)),
            "source": "akshare_security_master",
            "raw": {str(key): normalize_value(value) for key, value in row.items()},
        }

    def _match_rows(
        self,
        query: str,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        normalized_query = self._normalize_name(query)
        candidates: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            symbol = str(row.get("symbol") or "")
            name = str(row.get("name") or "").strip()
            normalized_name = self._normalize_name(name)
            if not symbol or len(symbol) != 6 or not normalized_name:
                continue
            aliases = {normalized_name, self._short_name(normalized_name)}
            for alias in aliases:
                if len(alias) >= 2 and alias in normalized_query:
                    candidates.setdefault(alias, []).append(row)
        matches: list[dict[str, Any]] = []
        ambiguous: list[str] = []
        occupied: set[str] = set()
        for alias in sorted(candidates, key=len, reverse=True):
            if any(alias in existing for existing in occupied):
                continue
            rows_for_alias = candidates[alias]
            unique_symbols = {str(row.get("symbol")) for row in rows_for_alias}
            if len(unique_symbols) > 1:
                ambiguous.append(alias)
                continue
            row = rows_for_alias[0]
            occupied.add(alias)
            matches.append(
                {
                    "symbol": str(row["symbol"]),
                    "name": str(row.get("name") or row["symbol"]),
                    "matched_text": alias,
                    "source": "akshare_security_master",
                }
            )
        return matches, ambiguous

    @staticmethod
    def _should_query_provider(query: str, symbols: list[str]) -> bool:
        if symbols:
            return False
        lowered = query.casefold()
        workflow_only = (
            "选股",
            "筛选",
            "板块",
            "行业",
            "sector",
            "screen",
            "review",
            "复核",
            "蒸馏",
            "distill",
        )
        if any(keyword in lowered for keyword in workflow_only):
            return False
        if not re.search(r"[\u4e00-\u9fff]{2,}", query):
            return False
        residual = AShareSecurityMaster._normalize_name(query)
        for term in (
            "请",
            "帮我",
            "分析一下",
            "分析",
            "研究一下",
            "研究",
            "看看",
            "查一下",
            "查询",
            "对比",
            "比较",
            "哪家",
            "哪个好",
            "哪只",
            "股票",
            "公司",
            "a股",
            "怎么样",
            "如何",
            "值得买吗",
        ):
            residual = residual.replace(term, "")
        residual = residual.strip("和与及的好吗呢")
        return len(residual) >= 2

    @staticmethod
    def _eligible_candidate(row: dict[str, Any]) -> bool:
        symbol = str(row.get("symbol") or "")
        name = str(row.get("name") or "")
        if len(symbol) != 6 or not symbol.isdigit():
            return False
        if "ST" in name.upper() or "退" in name:
            return False
        return symbol.startswith(("00", "30", "60", "68", "83", "87", "92"))

    @staticmethod
    def _short_name(name: str) -> str:
        result = name
        for suffix in ("股份有限公司", "有限责任公司", "有限公司", "股份", "集团"):
            if result.endswith(suffix) and len(result) > len(suffix) + 1:
                result = result[: -len(suffix)]
        return result

    @staticmethod
    def _normalize_name(value: object) -> str:
        return re.sub(r"[\s·•\-_/（）()]+", "", str(value or "").casefold())

    @staticmethod
    def _normalize_symbol(value: object) -> str:
        text = str(value or "").strip().lower()
        match = _CODE_PATTERN.search(text)
        return match.group(1) if match else ""

    @staticmethod
    def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            if key in row and row[key] not in (None, ""):
                return row[key]
        return None

    @staticmethod
    def _append_symbol(values: list[str], symbol: str) -> None:
        if symbol and symbol not in values:
            values.append(symbol)

    @staticmethod
    def _dedupe_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for match in matches:
            key = (str(match.get("symbol") or ""), str(match.get("matched_text") or ""))
            if key not in seen:
                seen.add(key)
                result.append(match)
        return result


__all__ = ["AShareSecurityMaster", "SecurityResolution"]
