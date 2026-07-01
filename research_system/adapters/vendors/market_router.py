from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketRoute:
    market: str
    exchange: str
    security_type: str
    normalized_symbol: str | None
    route_hint: str


class MarketRouter:
    def classify(self, *, symbol: str | None, query: str | None = None) -> MarketRoute:
        normalized_symbol = (symbol or "").strip()
        if not normalized_symbol:
            return MarketRoute(
                market="cn",
                exchange="unknown",
                security_type="unknown",
                normalized_symbol=None,
                route_hint="query_only",
            )

        if len(normalized_symbol) == 6 and normalized_symbol.isdigit():
            exchange = self._infer_exchange(normalized_symbol)
            security_type = self._infer_security_type(normalized_symbol)
            route_hint = "stock_snapshot"
            if security_type in {"etf", "lof", "mutual_fund"}:
                route_hint = "fund_like_snapshot"
            elif security_type == "convertible_bond":
                route_hint = "bond_like_snapshot"
            elif security_type == "index":
                route_hint = "index_snapshot"
            return MarketRoute(
                market=f"cn_{security_type}" if security_type != "stock" else "cn_equity",
                exchange=exchange,
                security_type=security_type,
                normalized_symbol=normalized_symbol,
                route_hint=route_hint,
            )

        lowered = (query or "").lower()
        if any(token in lowered for token in ("etf", "lof", "基金", "fund")):
            return MarketRoute(
                market="cn_fund",
                exchange="unknown",
                security_type="mutual_fund",
                normalized_symbol=normalized_symbol or None,
                route_hint="fund_like_snapshot",
            )
        if any(token in lowered for token in ("指数", "index", "hs300", "沪深300")):
            return MarketRoute(
                market="cn_index",
                exchange="unknown",
                security_type="index",
                normalized_symbol=normalized_symbol or None,
                route_hint="index_snapshot",
            )
        return MarketRoute(
            market="cn",
            exchange="unknown",
            security_type="unknown",
            normalized_symbol=normalized_symbol or None,
            route_hint="query_only",
        )

    def _infer_exchange(self, symbol: str) -> str:
        if symbol.startswith(("600", "601", "603", "605", "688", "689", "510", "511", "512", "513", "515")):
            return "sh"
        if symbol.startswith(("000", "001", "002", "003", "300", "301", "159", "160", "161", "162", "163", "164", "165", "166")):
            return "sz"
        if symbol.startswith(("430", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "874", "875", "876", "877", "878", "879", "920")):
            return "bj"
        return "unknown"

    def _infer_security_type(self, symbol: str) -> str:
        if symbol.startswith(("510", "511", "512", "513", "515", "159")):
            return "etf"
        if symbol.startswith(("160", "161", "162", "163", "164", "165", "166")):
            return "lof"
        if symbol.startswith(("110", "113", "118", "123", "127", "128")):
            return "convertible_bond"
        if symbol.startswith(("399", "000")) and symbol in {"000300", "000905", "000852", "399001", "399006"}:
            return "index"
        if symbol.startswith(("500", "501", "502")):
            return "mutual_fund"
        return "stock"
