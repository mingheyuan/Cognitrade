from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import requests

from research_system.adapters.vendors.akshare_utils import coerce_numeric
from research_system.adapters.vendors.market_router import MarketRouter


class DirectHttpMarketAdapter:
    """UZI-compatible direct HTTP market chain.

    A shares use EastMoney -> Sina -> Tencent. HK/US use Yahoo Chart v8,
    with Tencent HK and Stooq as their independent fallbacks.
    """

    vendor_name = "direct_http"
    _UA = "Mozilla/5.0 (compatible; Cognitrade/1.0)"

    def get_market_snapshot(
        self,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "",
    ) -> dict[str, Any]:
        market = self._market(symbol)
        loaders: tuple[Callable[[], list[dict[str, Any]]], ...]
        if market == "cn_equity":
            loaders = (
                lambda: self._eastmoney_a(symbol, adjust),
                lambda: self._sina_a(symbol),
                lambda: self._tencent_a(symbol, adjust),
            )
        elif market == "hk_equity":
            loaders = (
                lambda: self._yahoo_chart(self._yahoo_symbol(symbol), "2y"),
            )
        elif market == "global_equity":
            loaders = (
                lambda: self._yahoo_chart(self._yahoo_symbol(symbol), "2y"),
            )
        else:
            loaders = (
                lambda: self._yahoo_chart(self._yahoo_symbol(symbol), "2y"),
                lambda: self._stooq_us(symbol),
            )
        errors: list[str] = []
        for loader in loaders:
            try:
                bars = self._filter_dates(loader(), start_date, end_date)
            except Exception as exc:
                errors.append(exc.__class__.__name__)
                continue
            if bars:
                return {
                    "symbol": symbol,
                    "normalized_symbol": self._normalized_symbol(symbol),
                    "start_date": start_date,
                    "end_date": end_date,
                    "adjust": adjust,
                    "bars": bars,
                    "latest_bar": dict(bars[-1]),
                    "bar_count": len(bars),
                }
        raise ConnectionError(f"all direct HTTP market sources failed ({','.join(errors)})")

    def get_market_overview(self, *, symbol: str, latest_bar: dict[str, Any] | None = None) -> dict[str, Any]:
        bar = dict(latest_bar or {})
        close = coerce_numeric(bar.get("close"))
        previous = coerce_numeric(bar.get("pre_close"))
        pct = coerce_numeric(bar.get("pct_change"))
        if pct is None and close is not None and previous not in {None, 0}:
            pct = (close - previous) / previous * 100
        return {
            "symbol": symbol,
            "normalized_symbol": self._normalized_symbol(symbol),
            "latest_price": close,
            "pct_change": pct,
            "amount": coerce_numeric(bar.get("amount")),
            "spot": bar,
            "valuation": {},
            "spot_found": bool(bar),
            "valuation_found": False,
        }

    def _eastmoney_a(self, symbol: str, adjust: str) -> list[dict[str, Any]]:
        code = self._a_code(symbol)
        response = requests.get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "secid": f"1.{code}" if self._a_exchange(code) == "sh" else f"0.{code}",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "1" if adjust == "qfq" else "2" if adjust == "hfq" else "0",
                "lmt": "5000",
            },
            headers={"User-Agent": self._UA},
            timeout=12,
        )
        response.raise_for_status()
        lines = ((response.json().get("data") or {}).get("klines") or [])
        return [self._csv_bar(line) for line in lines if len(str(line).split(",")) >= 7]

    def _sina_a(self, symbol: str) -> list[dict[str, Any]]:
        code = self._a_code(symbol)
        response = requests.get(
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData",
            params={"symbol": f"{self._a_exchange(code)}{code}", "scale": "240", "ma": "no", "datalen": "5000"},
            headers={"User-Agent": self._UA},
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json() if response.text and response.text != "null" else []
        return [
            {
                "date": row.get("day"),
                "open": self._float(row.get("open")),
                "close": self._float(row.get("close")),
                "high": self._float(row.get("high")),
                "low": self._float(row.get("low")),
                "volume": self._float(row.get("volume")),
            }
            for row in payload
        ]

    def _tencent_a(self, symbol: str, adjust: str) -> list[dict[str, Any]]:
        code = self._a_code(symbol)
        ticker = f"{self._a_exchange(code)}{code}"
        response = requests.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{ticker},day,,,5000,{'qfq' if adjust == 'qfq' else ''}"},
            headers={"User-Agent": self._UA},
            timeout=12,
        )
        response.raise_for_status()
        data = response.json().get("data", {}).get(ticker, {})
        lines = data.get("qfqday") or data.get("day") or []
        return [
            {
                "date": row[0],
                "open": self._float(row[1]),
                "close": self._float(row[2]),
                "high": self._float(row[3]),
                "low": self._float(row[4]),
                "volume": self._float(row[5]),
            }
            for row in lines
            if len(row) >= 6
        ]

    def _yahoo_chart(self, symbol: str, range_: str) -> list[dict[str, Any]]:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": range_},
            headers={"User-Agent": self._UA, "Referer": "https://finance.yahoo.com/"},
            timeout=15,
        )
        response.raise_for_status()
        results = (response.json().get("chart") or {}).get("result") or []
        if not results:
            return []
        result = results[0]
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        timestamps = result.get("timestamp") or []
        rows: list[dict[str, Any]] = []
        for index, timestamp in enumerate(timestamps):
            close = self._at(quote.get("close"), index)
            if close is None:
                continue
            rows.append(
                {
                    "date": datetime.fromtimestamp(timestamp).date().isoformat(),
                    "open": self._at(quote.get("open"), index) or close,
                    "close": close,
                    "high": self._at(quote.get("high"), index) or close,
                    "low": self._at(quote.get("low"), index) or close,
                    "volume": self._at(quote.get("volume"), index) or 0,
                }
            )
        return rows

    def _stooq_us(self, symbol: str) -> list[dict[str, Any]]:
        ticker = self._yahoo_symbol(symbol).lower().replace(".", "-")
        response = requests.get(f"https://stooq.com/q/d/l/?s={ticker}.us&i=d", timeout=12)
        response.raise_for_status()
        rows = []
        for line in response.text.strip().splitlines()[1:]:
            fields = line.split(",")
            if len(fields) >= 6:
                rows.append(
                    {
                        "date": fields[0],
                        "open": self._float(fields[1]),
                        "high": self._float(fields[2]),
                        "low": self._float(fields[3]),
                        "close": self._float(fields[4]),
                        "volume": self._float(fields[5]),
                    }
                )
        return rows

    @staticmethod
    def _csv_bar(line: str) -> dict[str, Any]:
        fields = line.split(",")
        return {
            "date": fields[0],
            "open": DirectHttpMarketAdapter._float(fields[1]),
            "close": DirectHttpMarketAdapter._float(fields[2]),
            "high": DirectHttpMarketAdapter._float(fields[3]),
            "low": DirectHttpMarketAdapter._float(fields[4]),
            "volume": DirectHttpMarketAdapter._float(fields[5]),
            "amount": DirectHttpMarketAdapter._float(fields[6]),
            "pct_change": DirectHttpMarketAdapter._float(fields[8]) if len(fields) > 8 else None,
        }

    @staticmethod
    def _filter_dates(rows: list[dict[str, Any]], start_date: str, end_date: str) -> list[dict[str, Any]]:
        start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}" if len(start_date) == 8 else start_date
        end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}" if len(end_date) == 8 else end_date
        return [row for row in rows if start <= str(row.get("date") or "") <= end]

    @staticmethod
    def _market(symbol: str) -> str:
        return MarketRouter().classify(symbol=symbol).market

    @staticmethod
    def _normalized_symbol(symbol: str) -> str:
        return str(symbol or "").strip().upper()

    @staticmethod
    def _yahoo_symbol(symbol: str) -> str:
        raw = str(symbol or "").strip().upper()
        if raw.startswith("HK") and raw[2:].isdigit():
            return f"{str(int(raw[2:])).zfill(4)}.HK"
        if raw.endswith(".HK"):
            return f"{str(int(raw.removesuffix('.HK'))).zfill(4)}.HK"
        return raw

    @staticmethod
    def _a_code(symbol: str) -> str:
        digits = "".join(char for char in str(symbol) if char.isdigit())
        return digits[-6:]

    @staticmethod
    def _a_exchange(code: str) -> str:
        return "sh" if code.startswith(("5", "6", "9")) else "sz"

    @staticmethod
    def _float(value: Any) -> float:
        return float(value or 0)

    @staticmethod
    def _at(values: Any, index: int) -> Any:
        return values[index] if isinstance(values, list) and index < len(values) else None
