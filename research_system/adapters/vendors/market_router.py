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
            return MarketRoute(
                market="cn",
                exchange="unknown",
                security_type="unknown",
                normalized_symbol=None,
                route_hint="query_only",
                currency="CNY",
                country="CN",
                market_code="A",
            )

        if normalized_symbol.startswith("SEHK.") and normalized_symbol.removeprefix("SEHK.").isdigit():
            code = normalized_symbol.removeprefix("SEHK.").lstrip("0") or "0"
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

        a_share = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", normalized_symbol)
        if a_share:
            code, exchange = a_share.groups()
            security_type = self._infer_security_type(code)
            return MarketRoute(
                market=f"cn_{security_type}" if security_type != "stock" else "cn_equity",
                exchange=exchange.lower(),
                security_type=security_type,
                normalized_symbol=code,
                route_hint=self._route_hint(security_type),
                currency="CNY",
                country="CN",
                market_code="A",
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
                currency="CNY",
                country="CN",
                market_code="A",
            )

        hk_match = re.fullmatch(r"(?:HK)?(\d{1,5})(?:\.HK)?", normalized_symbol)
        if hk_match and (normalized_symbol.startswith("HK") or normalized_symbol.endswith(".HK") or len(normalized_symbol) < 6):
            code = hk_match.group(1).zfill(5)
            return MarketRoute(
                market="hk_equity",
                exchange="hkex",
                security_type="stock",
                normalized_symbol=f"{code}.HK",
                route_hint="stock_snapshot",
                currency="HKD",
                country="HK",
                market_code="H",
            )

        for suffix, (country, exchange, currency) in sorted(
            _GLOBAL_SUFFIXES.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if normalized_symbol.endswith(suffix) and len(normalized_symbol) > len(suffix):
                return MarketRoute(
                    market="global_equity",
                    exchange=exchange,
                    security_type="stock",
                    normalized_symbol=normalized_symbol,
                    route_hint="stock_snapshot",
                    currency=currency,
                    country=country,
                    market_code=country,
                )

        if re.fullmatch(r"[A-Z][A-Z.\-]{0,5}", normalized_symbol):
            return MarketRoute(
                market="us_equity",
                exchange="us",
                security_type="stock",
                normalized_symbol=normalized_symbol,
                route_hint="stock_snapshot",
                currency="USD",
                country="US",
                market_code="U",
            )

        unknown_global = re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,30}\.([A-Z0-9]{1,5})", normalized_symbol)
        if unknown_global:
            return MarketRoute(
                market="global_equity",
                exchange=unknown_global.group(1).lower(),
                security_type="stock",
                normalized_symbol=normalized_symbol,
                route_hint="stock_snapshot",
                market_code="G",
            )

        lowered = (query or "").lower()
        if any(token in lowered for token in ("etf", "lof", "基金", "fund")):
            return MarketRoute(
                market="cn_fund",
                exchange="unknown",
                security_type="mutual_fund",
                normalized_symbol=normalized_symbol or None,
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
                normalized_symbol=normalized_symbol or None,
                route_hint="index_snapshot",
                currency="CNY",
                country="CN",
                market_code="A",
            )
        return MarketRoute(
            market="cn",
            exchange="unknown",
            security_type="unknown",
            normalized_symbol=normalized_symbol or None,
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
