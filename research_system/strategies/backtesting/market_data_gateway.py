from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterable

from research_system.strategies.backtesting.instruments import (
    AssetType,
    TradableInstrument,
)
from research_system.strategies.backtesting.result_store import json_content_hash
from research_system.strategies.errors import StrategyDomainError


@dataclass(frozen=True)
class MarketSeries:
    instrument: TradableInstrument
    bars: tuple[dict[str, Any], ...]
    content_hash: str
    source: dict[str, Any]
    degraded_reasons: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument.payload(),
            "bars": list(self.bars),
            "content_hash": self.content_hash,
            "source": self.source,
            "degraded_reasons": list(self.degraded_reasons),
        }


SeriesLoader = Callable[[TradableInstrument, date, date, date], Iterable[dict[str, Any]]]


class MarketDataGateway:
    def __init__(self, market_data=None, *, series_loader: SeriesLoader | None = None) -> None:
        self.market_data = market_data
        self.series_loader = series_loader

    def get_series(
        self,
        instrument: TradableInstrument,
        *,
        start_date: date,
        end_date: date,
        as_of_date: date,
        fund_sources: Iterable[dict[str, Any]] = (),
    ) -> MarketSeries:
        if end_date > as_of_date:
            raise StrategyDomainError(
                "market_data_as_of_violation",
                "行情结束日期不能晚于 as_of_date",
            )
        if self.series_loader is not None:
            rows = list(self.series_loader(instrument, start_date, end_date, as_of_date))
            source = {"provider": "injected_series_loader"}
        elif instrument.asset_type == AssetType.OTC_FUND:
            rows = self._otc_nav_rows(fund_sources, as_of_date=as_of_date)
            source = {"provider": "point_in_time_fund_artifacts"}
        elif instrument.asset_type == AssetType.EXCHANGE_FUND:
            rows, source = self._exchange_fund_rows(
                instrument,
                start_date=start_date,
                end_date=end_date,
            )
        elif instrument.asset_type == AssetType.INDEX:
            rows, source = self._index_rows(
                instrument,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            if self.market_data is None:
                raise StrategyDomainError("market_data_gateway_unavailable", "行情服务不可用", status_code=503)
            payload = self.market_data.get_market_data(instrument.code, refresh=False)
            rows = list(payload.get("sections", {}).get("kline", {}).get("bars") or [])
            source = dict(payload.get("sources", {}).get("kline") or {})
        bars = self._normalise(rows, start_date=start_date, end_date=end_date, asset_type=instrument.asset_type)
        if not bars:
            code = "fund_nav_history_unavailable" if instrument.asset_type == AssetType.OTC_FUND else "market_history_unavailable"
            raise StrategyDomainError(code, f"{instrument.name} 在请求区间没有可审计行情", status_code=409)
        digest = json_content_hash(
            {"instrument": instrument.payload(), "bars": bars, "as_of_date": as_of_date}
        )
        degraded: list[str] = []
        if instrument.asset_type == AssetType.OTC_FUND and any(
            row.get("purchase_open") is None or row.get("redemption_open") is None for row in bars
        ):
            degraded.append("otc_open_status_unavailable")
        return MarketSeries(
            instrument=instrument,
            bars=tuple(bars),
            content_hash=digest,
            source=source,
            degraded_reasons=tuple(degraded),
        )

    @staticmethod
    def _normalise(
        rows: Iterable[dict[str, Any]],
        *,
        start_date: date,
        end_date: date,
        asset_type: AssetType,
    ) -> list[dict[str, Any]]:
        by_date: dict[str, dict[str, Any]] = {}
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            day = _date(
                raw.get("date")
                or raw.get("datetime")
                or raw.get("as_of")
                or raw.get("日期")
                or raw.get("净值日期")
            )
            if day is None or day < start_date or day > end_date:
                continue
            nav = _number(
                raw.get("unit_nav"),
                raw.get("nav"),
                raw.get("单位净值"),
                raw.get("close"),
                raw.get("收盘"),
            )
            close = nav if asset_type == AssetType.OTC_FUND else _number(raw.get("close"), raw.get("收盘"), nav)
            if close is None or close <= 0:
                continue
            if asset_type == AssetType.OTC_FUND:
                open_price = high = low = close
            else:
                open_price = _number(raw.get("open"), raw.get("开盘"), close)
                high = _number(raw.get("high"), raw.get("最高"), close)
                low = _number(raw.get("low"), raw.get("最低"), close)
                if None in {open_price, high, low} or float(low) > float(high):
                    continue
            by_date[day.isoformat()] = {
                "date": day.isoformat(),
                "open": float(open_price),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(_number(raw.get("volume"), raw.get("成交量")) or 0.0),
                "amount": float(_number(raw.get("amount"), raw.get("成交额")) or 0.0),
                "purchase_open": _open_status(
                    raw.get("purchase_open"),
                    raw.get("purchase_status"),
                    raw.get("申购状态"),
                ),
                "redemption_open": _open_status(
                    raw.get("redemption_open"),
                    raw.get("redemption_status"),
                    raw.get("赎回状态"),
                ),
            }
        return [by_date[key] for key in sorted(by_date)]

    @staticmethod
    def _otc_nav_rows(
        fund_sources: Iterable[dict[str, Any]],
        *,
        as_of_date: date,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for entry in fund_sources:
            if not isinstance(entry, dict):
                continue
            report_as_of = _date(entry.get("report_as_of"))
            if report_as_of is None or report_as_of > as_of_date:
                continue
            source = entry.get("source") if isinstance(entry.get("source"), dict) else entry
            purchase_status = source.get("purchase_status") or source.get("申购状态")
            redemption_status = source.get("redemption_status") or source.get("赎回状态")
            for raw in source.get("nav_history") or []:
                if not isinstance(raw, dict):
                    continue
                nav_date = _date(raw.get("date") or raw.get("as_of") or raw.get("净值日期"))
                if nav_date is None or nav_date > report_as_of or nav_date > as_of_date:
                    continue
                rows.append(
                    {
                        **raw,
                        "purchase_status": raw.get("purchase_status", purchase_status),
                        "redemption_status": raw.get("redemption_status", redemption_status),
                    }
                )
        return rows

    def _exchange_fund_rows(
        self,
        instrument: TradableInstrument,
        *,
        start_date: date,
        end_date: date,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self.market_data is not None:
            try:
                payload = self.market_data.get_market_data(instrument.code, refresh=False)
                rows = list(payload.get("sections", {}).get("kline", {}).get("bars") or [])
                if rows:
                    return rows, dict(payload.get("sources", {}).get("kline") or {})
            except Exception:
                pass
        try:
            import akshare as ak
        except ImportError as exc:
            raise StrategyDomainError(
                "fund_market_history_unavailable",
                "ETF/LOF 行情适配器不可用",
                status_code=503,
            ) from exc
        endpoints = (
            ("fund_etf_hist_em", getattr(ak, "fund_etf_hist_em", None)),
            ("fund_lof_hist_em", getattr(ak, "fund_lof_hist_em", None)),
        )
        diagnostics: list[str] = []
        for endpoint, operation in endpoints:
            if not callable(operation):
                continue
            try:
                frame = operation(
                    symbol=instrument.code,
                    period="daily",
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    adjust="",
                )
                rows = frame.to_dict("records") if frame is not None else []
                if rows:
                    return rows, {
                        "provider": "eastmoney_via_akshare",
                        "endpoint": endpoint,
                        "adjustment": "raw",
                    }
            except Exception as exc:
                diagnostics.append(f"{endpoint}:{exc.__class__.__name__}")
        raise StrategyDomainError(
            "fund_market_history_unavailable",
            f"{instrument.name} 没有可用的 ETF/LOF 历史行情",
            status_code=409,
            details={"provider_attempts": diagnostics},
        )

    @staticmethod
    def _index_rows(
        instrument: TradableInstrument,
        *,
        start_date: date,
        end_date: date,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            import akshare as ak
        except ImportError as exc:
            raise StrategyDomainError(
                "benchmark_history_unavailable",
                "指数基准行情适配器不可用",
                status_code=503,
            ) from exc
        operations = (
            (
                "stock_zh_index_hist_csindex",
                getattr(ak, "stock_zh_index_hist_csindex", None),
                {
                    "symbol": instrument.code,
                    "start_date": start_date.strftime("%Y%m%d"),
                    "end_date": end_date.strftime("%Y%m%d"),
                },
            ),
            (
                "index_zh_a_hist",
                getattr(ak, "index_zh_a_hist", None),
                {
                    "symbol": instrument.code,
                    "period": "daily",
                    "start_date": start_date.strftime("%Y%m%d"),
                    "end_date": end_date.strftime("%Y%m%d"),
                },
            ),
        )
        diagnostics: list[str] = []
        for endpoint, operation, kwargs in operations:
            if not callable(operation):
                continue
            try:
                frame = operation(**kwargs)
                rows = frame.to_dict("records") if frame is not None else []
                if rows:
                    return rows, {
                        "provider": "akshare",
                        "endpoint": endpoint,
                        "adjustment": "raw",
                    }
            except Exception as exc:
                diagnostics.append(f"{endpoint}:{exc.__class__.__name__}")
        raise StrategyDomainError(
            "benchmark_history_unavailable",
            f"基准 {instrument.name} 没有可用历史行情",
            status_code=409,
            details={"provider_attempts": diagnostics},
        )


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _number(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "" or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _open_status(*values: Any) -> bool | None:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "open", "开放", "开放申购", "开放赎回", "正常"}:
            return True
        if normalized in {"0", "false", "closed", "暂停", "暂停申购", "暂停赎回", "封闭"}:
            return False
    return None


__all__ = ["MarketDataGateway", "MarketSeries"]
