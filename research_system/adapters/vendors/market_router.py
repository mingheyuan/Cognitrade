from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MarketRoute:
    market: str
    exchange: str
    security_type: str
    normalized_symbol: str | None
    route_hint: str
    currency: str = ""
    country: str = ""
    market_code: str = ""


_GLOBAL_SUFFIXES: dict[str, tuple[str, str, str]] = {
    ".T": ("JP", "tse", "JPY"),
    ".KS": ("KR", "krx", "KRW"),
    ".KQ": ("KR", "kosdaq", "KRW"),
    ".TW": ("TW", "twse", "TWD"),
    ".TWO": ("TW", "tpex", "TWD"),
    ".SI": ("SG", "sgx", "SGD"),
    ".TO": ("CA", "tsx", "CAD"),
    ".V": ("CA", "tsxv", "CAD"),
    ".AX": ("AU", "asx", "AUD"),
    ".L": ("GB", "lse", "GBP"),
    ".DE": ("DE", "xetra", "EUR"),
    ".F": ("DE", "frankfurt", "EUR"),
    ".PA": ("FR", "euronext_paris", "EUR"),
    ".AS": ("NL", "euronext_amsterdam", "EUR"),
    ".SW": ("CH", "six", "CHF"),
    ".MC": ("ES", "bme", "EUR"),
    ".MI": ("IT", "borsa_italiana", "EUR"),
    ".ST": ("SE", "omx_stockholm", "SEK"),
    ".OL": ("NO", "oslo", "NOK"),
    ".CO": ("DK", "omx_copenhagen", "DKK"),
    ".HE": ("FI", "omx_helsinki", "EUR"),
    ".BR": ("BE", "euronext_brussels", "EUR"),
    ".LS": ("PT", "euronext_lisbon", "EUR"),
    ".SA": ("BR", "b3", "BRL"),
    ".MX": ("MX", "bmv", "MXN"),
    ".BK": ("TH", "set", "THB"),
    ".JK": ("ID", "idx", "IDR"),
    ".KL": ("MY", "bursa_malaysia", "MYR"),
    ".NZ": ("NZ", "nzx", "NZD"),
    ".JO": ("ZA", "jse", "ZAR"),
    ".TA": ("IL", "tase", "ILS"),
    ".VI": ("AT", "vienna", "EUR"),
    ".WA": ("PL", "wse", "PLN"),
    ".PR": ("CZ", "prague", "CZK"),
    ".BD": ("HU", "budapest", "HUF"),
    ".NS": ("IN", "nse", "INR"),
    ".BO": ("IN", "bse", "INR"),
}


class MarketRouter:
    def classify(self, *, symbol: str | None, query: str | None = None) -> MarketRoute:
        normalized_symbol = (symbol or "").strip().upper()
        if not normalized_symbol:
            return self._unknown_cn_route(None)

        for classifier in (
            self._classify_sehk,
            self._classify_china,
            self._classify_hk,
            self._classify_global,
        ):
            route = classifier(normalized_symbol)
            if route is not None:
                return route
        return self._classify_query(normalized_symbol, query)

    @staticmethod
    def _classify_sehk(symbol: str) -> MarketRoute | None:
        code = symbol.removeprefix("SEHK.")
        if not symbol.startswith("SEHK.") or not code.isdigit():
            return None
        return MarketRouter._hk_route(code.lstrip("0") or "0")

    def _classify_china(self, symbol: str) -> MarketRoute | None:
        suffix_match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", symbol)
        if suffix_match:
            code, exchange = suffix_match.groups()
            return self._china_route(code, exchange.lower())
        if len(symbol) == 6 and symbol.isdigit():
            return self._china_route(symbol, self._infer_exchange(symbol))
        return None

    @staticmethod
    def _classify_hk(symbol: str) -> MarketRoute | None:
        match = re.fullmatch(r"(?:HK)?(\d{1,5})(?:\.HK)?", symbol)
        has_hk_marker = symbol.startswith("HK") or symbol.endswith(".HK")
        if match is None or not (has_hk_marker or len(symbol) < 6):
            return None
        return MarketRouter._hk_route(match.group(1))

    @staticmethod
    def _classify_global(symbol: str) -> MarketRoute | None:
        for suffix, (country, exchange, currency) in sorted(
            _GLOBAL_SUFFIXES.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if symbol.endswith(suffix) and len(symbol) > len(suffix):
                return MarketRoute(
                    market="global_equity",
                    exchange=exchange,
                    security_type="stock",
                    normalized_symbol=symbol,
                    route_hint="stock_snapshot",
                    currency=currency,
                    country=country,
                    market_code=country,
                )
        if re.fullmatch(r"[A-Z][A-Z.\-]{0,5}", symbol):
            return MarketRoute(
                market="us_equity",
                exchange="us",
                security_type="stock",
                normalized_symbol=symbol,
                route_hint="stock_snapshot",
                currency="USD",
                country="US",
                market_code="U",
            )
        unknown_global = re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,30}\.([A-Z0-9]{1,5})", symbol)
        if unknown_global is None:
            return None
        return MarketRoute(
            market="global_equity",
            exchange=unknown_global.group(1).lower(),
            security_type="stock",
            normalized_symbol=symbol,
            route_hint="stock_snapshot",
            market_code="G",
        )

    def _china_route(self, symbol: str, exchange: str) -> MarketRoute:
        security_type = self._infer_security_type(symbol)
        return MarketRoute(
            market=f"cn_{security_type}" if security_type != "stock" else "cn_equity",
            exchange=exchange,
            security_type=security_type,
            normalized_symbol=symbol,
            route_hint=self._route_hint(security_type),
            currency="CNY",
            country="CN",
            market_code="A",
        )

    @staticmethod
    def _hk_route(code: str) -> MarketRoute:
        return MarketRoute(
            market="hk_equity",
            exchange="hkex",
            security_type="stock",
            normalized_symbol=f"{code.zfill(5)}.HK",
            route_hint="stock_snapshot",
            currency="HKD",
            country="HK",
            market_code="H",
        )

    @staticmethod
    def _classify_query(symbol: str, query: str | None) -> MarketRoute:
        lowered = (query or "").lower()
        if any(token in lowered for token in ("etf", "lof", "基金", "fund")):
            return MarketRoute(
                market="cn_fund",
                exchange="unknown",
                security_type="mutual_fund",
                normalized_symbol=symbol or None,
                route_hint="fund_like_snapshot",
                currency="CNY",
                country="CN",
                market_code="A",
            )
        if any(token in lowered for token in ("指数", "index", "hs300", "沪深300")):
            return MarketRoute(
                market="cn_index",
                exchange="unknown",
                security_type="index",
                normalized_symbol=symbol or None,
                route_hint="index_snapshot",
                currency="CNY",
                country="CN",
                market_code="A",
            )
        return MarketRouter._unknown_cn_route(symbol)

    @staticmethod
    def _unknown_cn_route(symbol: str | None) -> MarketRoute:
        return MarketRoute(
            market="cn",
            exchange="unknown",
            security_type="unknown",
            normalized_symbol=symbol or None,
            route_hint="query_only",
            currency="CNY",
            country="CN",
            market_code="A",
        )

    @staticmethod
    def _route_hint(security_type: str) -> str:
        if security_type in {"etf", "lof", "mutual_fund"}:
            return "fund_like_snapshot"
        if security_type == "convertible_bond":
            return "bond_like_snapshot"
        if security_type == "index":
            return "index_snapshot"
        return "stock_snapshot"

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
