from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from domain_packs.finance_ashare.tools.security_master import AShareSecurityMaster
from domain_packs.finance_ashare.tools.snapshot_collector import collect_snapshots


SnapshotCollector = Callable[..., list[dict[str, Any]]]
_MARKET_DATA_SNAPSHOT_TYPES = (
    "market",
    "kline",
    "fundamental",
    "capital_flow",
    "sentiment",
    "notice",
    "events",
    "lhb",
)


class FinanceMarketDataError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class FinanceMarketDataService:
    """Read-only Finance Pack facade used by the workspace and paper ledger."""

    def __init__(
        self,
        *,
        cache_root: Path,
        collector: SnapshotCollector = collect_snapshots,
        security_master: AShareSecurityMaster | None = None,
    ) -> None:
        self.cache_root = Path(cache_root)
        self.collector = collector
        self.security_master = security_master or AShareSecurityMaster()
        self._market_data_center = None
        self._lock = RLock()

    def bind_market_data_center(self, service) -> None:
        """Bind the DB-only workspace projection after the shared DB starts."""

        self._market_data_center = service

    def search_securities(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []
        normalized_limit = min(max(int(limit), 1), 20)
        db_only = self._market_data_center is not None
        resolution = self.security_master.resolve(
            normalized_query,
            # Once the database-first center is bound, a GET may use only
            # deterministic aliases/codes plus already-published Bundles.
            allow_network=not db_only,
        )
        rows_by_symbol = {} if db_only else self._security_rows_by_symbol()
        results: list[dict[str, Any]] = []
        for symbol in resolution.symbols:
            row = (
                self._workspace_security_row(symbol)
                if db_only
                else rows_by_symbol.get(symbol, {})
            )
            results.append(
                {
                    "symbol": symbol,
                    "name": str(row.get("name") or self._name_from_matches(resolution.matches, symbol) or symbol),
                    "latest_price": row.get("latest_price"),
                    "pct_change": row.get("pct_change"),
                    "source": str(row.get("source") or resolution.source),
                }
            )
        if not results:
            folded = normalized_query.casefold()
            for row in rows_by_symbol.values():
                if folded in str(row.get("symbol") or "").casefold() or folded in str(row.get("name") or "").casefold():
                    results.append(self._public_security_row(row))
                    if len(results) >= normalized_limit:
                        break
        return results[:normalized_limit]

    def get_market_data(
        self,
        query: str,
        *,
        refresh: bool = False,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        if self._market_data_center is not None:
            # ``refresh`` is intentionally ignored in DB mode. A GET must never
            # turn into acquisition; callers use POST collection-jobs instead.
            symbol, _ = self.resolve_security(query, allow_network=False)
            payload = self._workspace_market_data(symbol, as_of_date=as_of_date)
            payload["refresh_requested"] = bool(refresh)
            payload["refresh_performed"] = False
            return payload
        symbol, name = self.resolve_security(query)
        snapshots = self._collect(
            query=f"{name}（{symbol}）行情与基本面数据",
            symbol=symbol,
            snapshot_types=_MARKET_DATA_SNAPSHOT_TYPES,
            refresh=refresh,
        )
        by_type = {
            str(snapshot.get("snapshot_type") or ""): snapshot
            for snapshot in snapshots
            if isinstance(snapshot, dict)
        }
        market = self._facts(by_type.get("market"))
        kline = self._facts(by_type.get("kline"))
        quality = {
            snapshot_type: self._quality(by_type.get(snapshot_type))
            for snapshot_type in _MARKET_DATA_SNAPSHOT_TYPES
        }
        sources = {
            snapshot_type: self._source(by_type.get(snapshot_type))
            for snapshot_type in _MARKET_DATA_SNAPSHOT_TYPES
        }
        return {
            "symbol": symbol,
            "name": name,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "quality_status": self._overall_quality(quality.values()),
            "quality": quality,
            "sources": sources,
            "sections": {
                "market": {
                    "overview": dict(market.get("market_overview") or {}),
                    "history_summary": dict(market.get("market_hist") or {}),
                },
                "kline": dict(kline.get("history") or market.get("market_hist") or {}),
                "fundamental": self._facts(by_type.get("fundamental")),
                "capital_flow": self._facts(by_type.get("capital_flow")),
                "sentiment": self._facts(by_type.get("sentiment")),
                "notices": self._facts(by_type.get("notice")),
                "events": self._facts(by_type.get("events")),
                "lhb": self._facts(by_type.get("lhb")),
            },
        }

    def get_quote(self, query: str, *, refresh: bool = False) -> dict[str, Any]:
        """Return a public, side-effect-free quote projection.

        In production the market-data center is bound during application
        startup, so this method reads only the latest published Bundle.  The
        legacy collector remains available through :meth:`get_live_quote` for
        explicit execution workflows, never as a GET fallback.
        """

        if self._market_data_center is not None:
            workspace = self.get_market_data(query, refresh=refresh)
            return self._quote_from_workspace(workspace)
        return self.get_live_quote(query, refresh=refresh)

    def get_live_quote(self, query: str, *, refresh: bool = True) -> dict[str, Any]:
        """Acquire a quote for an explicit server-side execution workflow."""

        symbol, name = self.resolve_security(query)
        snapshot = self._collect(
            query=f"{name}（{symbol}）最新行情",
            symbol=symbol,
            snapshot_types=("market",),
            refresh=refresh,
        )
        market_snapshot = next(
            (item for item in snapshot if str(item.get("snapshot_type") or "") == "market"),
            {},
        )
        facts = self._facts(market_snapshot)
        overview = dict(facts.get("market_overview") or {})
        history = dict(facts.get("market_hist") or {})
        latest_bar = dict(history.get("latest_bar") or {})
        price = self._number(overview.get("latest_price"), latest_bar.get("close"))
        if price is None or price <= 0:
            raise FinanceMarketDataError(
                "quote_unavailable",
                f"{name}（{symbol}）当前没有可用于模拟成交的行情价格",
            )
        return {
            "symbol": symbol,
            "name": name,
            "latest_price": price,
            "bid_price_1": self._number(
                overview.get("bid_price_1"),
                overview.get("bid1"),
                overview.get("买一"),
            ),
            "ask_price_1": self._number(
                overview.get("ask_price_1"),
                overview.get("ask1"),
                overview.get("卖一"),
            ),
            "pct_change": self._number(overview.get("pct_change")),
            "as_of": latest_bar.get("date") or overview.get("valuation_date") or market_snapshot.get("snapshot_date"),
            "source": self._source(market_snapshot),
            "quality_status": self._quality(market_snapshot),
        }

    def resolve_security(self, query: str, *, allow_network: bool = True) -> tuple[str, str]:
        normalized = str(query or "").strip()
        if not normalized:
            raise FinanceMarketDataError("security_required", "请输入股票代码或名称")
        resolution = self.security_master.resolve(normalized, allow_network=allow_network)
        if resolution.ambiguous_terms:
            raise FinanceMarketDataError(
                "security_ambiguous",
                f"股票名称存在歧义：{', '.join(resolution.ambiguous_terms)}，请改用六位股票代码",
            )
        if not resolution.symbols:
            raise FinanceMarketDataError("security_not_found", "未识别到 A 股股票代码或名称")
        if len(resolution.symbols) > 1:
            raise FinanceMarketDataError("security_ambiguous", "识别到多只股票，请一次只输入一只")
        symbol = resolution.symbols[0]
        # ``rows()`` may refresh the provider-backed security master.  Offline
        # Bundle reads must therefore use only matches returned by the
        # deterministic alias/code resolver.
        row = self._security_rows_by_symbol().get(symbol, {}) if allow_network else {}
        name = str(row.get("name") or self._name_from_matches(resolution.matches, symbol) or symbol)
        return symbol, name

    def _workspace_market_data(
        self,
        symbol: str,
        *,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for candidate in self._database_symbol_candidates(symbol):
            try:
                return dict(
                    self._market_data_center.get_workspace_market_data(
                        symbol=candidate,
                        # Interactive reads use the exchange-local current day;
                        # historical consumers must bind the exact replay cutoff.
                        as_of_date=as_of_date,
                    )
                )
            except Exception as exc:
                last_error = exc
                if str(getattr(exc, "code", "")) != "published_bundle_missing":
                    break
        assert last_error is not None
        code = str(getattr(last_error, "code", "market_data_bundle_unavailable"))
        message = str(
            getattr(last_error, "message", "数据库中没有可读取的完整行情 Bundle")
        )
        raise FinanceMarketDataError(code, message) from last_error

    @staticmethod
    def _database_symbol_candidates(symbol: str) -> tuple[str, ...]:
        normalized = str(symbol or "").strip().upper()
        candidates = [normalized]
        if len(normalized) == 6 and normalized.isdigit():
            if normalized.startswith(("4", "8")):
                suffix = "BJ"
            elif normalized.startswith(("5", "6", "9")):
                suffix = "SH"
            else:
                suffix = "SZ"
            candidates.append(f"{normalized}.{suffix}")
        return tuple(dict.fromkeys(candidates))

    def _workspace_security_row(self, symbol: str) -> dict[str, Any]:
        """Best-effort DB enrichment for offline security search."""

        try:
            workspace = self._workspace_market_data(symbol)
            quote = self._quote_from_workspace(workspace)
        except FinanceMarketDataError:
            return {}
        return {
            "symbol": quote["symbol"],
            "name": quote["name"],
            "latest_price": quote["latest_price"],
            "pct_change": quote.get("pct_change"),
            "source": "market_data_bundle",
        }

    def _quote_from_workspace(self, workspace: dict[str, Any]) -> dict[str, Any]:
        sections = dict(workspace.get("sections") or {})
        market = dict(sections.get("market") or {})
        overview = dict(
            market.get("market_overview")
            or market.get("overview")
            or market
        )
        kline = dict(sections.get("kline") or {})
        history = dict(
            market.get("market_hist")
            or kline.get("history")
            or kline
        )
        bars = history.get("ohlcv") or history.get("bars") or []
        latest_bar = dict(history.get("latest_bar") or {})
        if not latest_bar and isinstance(bars, list) and bars and isinstance(bars[-1], dict):
            latest_bar = dict(bars[-1])
        price = self._number(
            overview.get("latest_price"),
            overview.get("price"),
            latest_bar.get("close"),
        )
        symbol = str(workspace.get("symbol") or overview.get("symbol") or "")
        name = str(workspace.get("name") or overview.get("name") or symbol)
        if price is None or price <= 0:
            raise FinanceMarketDataError(
                "quote_unavailable",
                f"{name}（{symbol}）的已发布 Bundle 没有有效行情价格",
            )
        source = {
            "mode": "bundle_only",
            "bundle_id": workspace.get("bundle_id"),
            "manifest_hash": workspace.get("bundle_manifest_hash"),
        }
        return {
            "symbol": symbol,
            "name": name,
            "latest_price": price,
            "bid_price_1": self._number(
                overview.get("bid_price_1"),
                overview.get("bid1"),
                overview.get("买一"),
            ),
            "ask_price_1": self._number(
                overview.get("ask_price_1"),
                overview.get("ask1"),
                overview.get("卖一"),
            ),
            "pct_change": self._number(overview.get("pct_change")),
            "as_of": (
                overview.get("observed_at")
                or latest_bar.get("date")
                or workspace.get("cutoff_at")
                or workspace.get("as_of_date")
            ),
            "source": source,
            "quality_status": str(workspace.get("quality_status") or "available"),
            "refresh_requested": bool(workspace.get("refresh_requested")),
            "refresh_performed": False,
        }

    def _collect(
        self,
        *,
        query: str,
        symbol: str,
        snapshot_types: Iterable[str],
        refresh: bool,
    ) -> list[dict[str, Any]]:
        # The Pack cache writer is synchronous and file-backed. Serialising only
        # this very small facade prevents two refreshes from replacing one cache
        # partition at the same time while snapshot providers still run in parallel.
        with self._lock:
            return self.collector(
                query=query,
                symbols=[symbol],
                snapshot_types=snapshot_types,
                cache_root=None if refresh else self.cache_root,
                research_profile="workspace_market_data",
                # Several AkShare endpoints initialise the embedded JavaScript
                # runtime lazily. Initialising those providers in parallel can
                # abort the whole process on macOS, so this public workspace
                # facade deliberately serialises provider calls. Pack caches
                # keep subsequent reads fast.
                max_workers=1,
            )

    def _security_rows_by_symbol(self) -> dict[str, dict[str, Any]]:
        try:
            return {
                str(row.get("symbol") or ""): dict(row)
                for row in self.security_master.rows()
                if str(row.get("symbol") or "")
            }
        except Exception:
            return {}

    @staticmethod
    def _facts(snapshot: dict[str, Any] | None) -> dict[str, Any]:
        facts = (snapshot or {}).get("facts")
        return dict(facts) if isinstance(facts, dict) else {}

    @staticmethod
    def _quality(snapshot: dict[str, Any] | None) -> str:
        payload = snapshot or {}
        facts = FinanceMarketDataService._facts(payload)
        declared = str(
            payload.get("quality_status")
            or payload.get("status")
            or facts.get("availability")
            or ""
        ).strip().lower()
        if declared in {
            "data_gap",
            "degraded",
            "assumption_only",
            "no_data",
            "provider_unavailable",
            "rate_limited",
            "parse_failed",
        }:
            return declared
        return "degraded" if payload.get("degraded") else "available"

    @staticmethod
    def _overall_quality(values: Iterable[str]) -> str:
        states = set(values)
        if states == {"available"}:
            return "available"
        if "available" in states:
            return "partial"
        return "degraded"

    @staticmethod
    def _source(snapshot: dict[str, Any] | None) -> dict[str, Any]:
        payload = snapshot or {}
        source = payload.get("primary_source") or payload.get("source") or {}
        return dict(source) if isinstance(source, dict) else {}

    @staticmethod
    def _name_from_matches(matches: Iterable[dict[str, Any]], symbol: str) -> str:
        for match in matches:
            if str(match.get("symbol") or "") == symbol and match.get("name"):
                return str(match["name"])
        return ""

    @staticmethod
    def _public_security_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": row.get("symbol"),
            "name": row.get("name"),
            "latest_price": row.get("latest_price"),
            "pct_change": row.get("pct_change"),
            "source": row.get("source"),
        }

    @staticmethod
    def _number(*values: Any) -> float | None:
        for value in values:
            if isinstance(value, bool) or value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None


__all__ = ["FinanceMarketDataError", "FinanceMarketDataService"]
