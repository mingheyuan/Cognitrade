from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SourceAccess = Literal["sdk", "http", "browser", "official_disclosure"]
SourceTier = Literal["tier1", "tier2", "tier3"]
SourceStability = Literal["high", "medium", "low"]
LatencyLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class DataSource:
    id: str
    name_cn: str
    base_url: str
    markets: tuple[str, ...]
    dims: tuple[str, ...]
    tier: SourceTier
    official: bool
    access: SourceAccess
    requires_key: bool = False
    stability: SourceStability = "medium"
    latency_level: LatencyLevel = "medium"
    notes: str = ""


class DataSourceRegistry:
    def __init__(self, sources: list[DataSource] | None = None) -> None:
        self._sources = {source.id: source for source in (sources or self._default_sources())}

    def get(self, source_id: str) -> DataSource | None:
        return self._sources.get(source_id)

    def list_all(self) -> list[DataSource]:
        return list(self._sources.values())

    def list_for_dim(self, dim: str, market: str | None = None) -> list[DataSource]:
        normalized_dim = dim.strip().lower()
        normalized_market = (market or "").strip().lower()
        rows = [item for item in self._sources.values() if normalized_dim in item.dims]
        if normalized_market:
            rows = [item for item in rows if normalized_market in item.markets or "cn" in item.markets and normalized_market.startswith("cn_")]
        return rows

    def _default_sources(self) -> list[DataSource]:
        return [
            DataSource(
                id="akshare",
                name_cn="AkShare",
                base_url="https://akshare.akfamily.xyz",
                markets=("cn", "cn_equity", "cn_sector", "cn_index"),
                dims=("market", "fundamental", "sector"),
                tier="tier1",
                official=False,
                access="sdk",
                stability="medium",
                latency_level="low",
                notes="Current primary SDK for A-share market and sector snapshots.",
            ),
            DataSource(
                id="cninfo",
                name_cn="巨潮资讯",
                base_url="https://www.cninfo.com.cn",
                markets=("cn", "cn_equity"),
                dims=("notice", "policy", "events", "fundamental"),
                tier="tier3",
                official=True,
                access="official_disclosure",
                stability="high",
                latency_level="medium",
                notes="Official disclosure channel for notices and periodic reports.",
            ),
            DataSource(
                id="eastmoney",
                name_cn="东方财富",
                base_url="https://www.eastmoney.com",
                markets=("cn", "cn_equity", "cn_sector"),
                dims=("market", "fundamental", "notice", "sector", "capital_flow", "lhb", "sentiment"),
                tier="tier2",
                official=False,
                access="http",
                stability="medium",
                latency_level="medium",
                notes="Useful fallback for quotes, notice mirrors, sector breadth, and flow dimensions.",
            ),
            DataSource(
                id="baostock",
                name_cn="BaoStock",
                base_url="http://baostock.com",
                markets=("cn", "cn_equity", "cn_index"),
                dims=("market",),
                tier="tier1",
                official=False,
                access="sdk",
                stability="medium",
                latency_level="low",
                notes="Fallback quote/kline provider for market snapshots.",
            ),
            DataSource(
                id="efinance",
                name_cn="eFinance",
                base_url="https://efinance.readthedocs.io",
                markets=("cn", "cn_equity"),
                dims=("market",),
                tier="tier2",
                official=False,
                access="sdk",
                stability="medium",
                latency_level="medium",
                notes="Optional quote fallback when AkShare path degrades.",
            ),
            DataSource(
                id="sse",
                name_cn="上交所",
                base_url="https://www.sse.com.cn",
                markets=("cn", "cn_equity"),
                dims=("notice", "policy", "events"),
                tier="tier3",
                official=True,
                access="official_disclosure",
                stability="high",
                latency_level="medium",
                notes="Official fallback for Shanghai-listed disclosure data.",
            ),
            DataSource(
                id="szse",
                name_cn="深交所",
                base_url="https://www.szse.cn",
                markets=("cn", "cn_equity"),
                dims=("notice", "policy", "events"),
                tier="tier3",
                official=True,
                access="official_disclosure",
                stability="high",
                latency_level="medium",
                notes="Official fallback for Shenzhen-listed disclosure data.",
            ),
            DataSource(
                id="tushare",
                name_cn="TuShare",
                base_url="https://tushare.pro",
                markets=("cn", "cn_equity"),
                dims=("fundamental", "market"),
                tier="tier2",
                official=False,
                access="http",
                requires_key=True,
                stability="medium",
                latency_level="medium",
                notes="Optional keyed fallback for fundamental datasets.",
            ),
            DataSource(
                id="xueqiu",
                name_cn="雪球",
                base_url="https://xueqiu.com",
                markets=("cn", "cn_equity", "cn_sector"),
                dims=("sentiment", "contests"),
                tier="tier2",
                official=False,
                access="browser",
                stability="low",
                latency_level="high",
                notes="Sensitive authenticated source; never active without explicit runtime policy and credentials.",
            ),
            DataSource(
                id="cninfo_events",
                name_cn="巨潮资讯事件",
                base_url="https://www.cninfo.com.cn",
                markets=("cn", "cn_equity"),
                dims=("events",),
                tier="tier3",
                official=True,
                access="official_disclosure",
                stability="high",
                latency_level="medium",
                notes="Disclosure-backed event source for earnings, contracts, and milestone notices.",
            ),
        ]
