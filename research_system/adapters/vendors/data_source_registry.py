from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from research_system.adapters.vendors.uzi_source_catalog import (
    UZI_SOURCE_ROWS,
    mapped_dimensions,
    mapped_markets,
)


SourceAccess = Literal["sdk", "http", "browser", "official_disclosure", "search", "api"]
SourceTier = Literal["tier1", "tier2", "tier3"]
SourceStability = Literal["high", "medium", "low"]
LatencyLevel = Literal["low", "medium", "high"]
SourceHealth = Literal["known_good", "flaky", "blocked_often", "needs_browser", "unknown"]


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
    upstream_dims: tuple[str, ...] = ()
    health: SourceHealth = "unknown"
    upstream: str | None = None


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
        sources = self._uzi_sources()
        local_sources = [
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
                dims=("market", "fundamental", "notice", "sector", "capital_flow", "lhb", "sentiment", "fund"),
                tier="tier2",
                official=False,
                access="http",
                stability="medium",
                latency_level="medium",
                notes="Useful fallback for quotes, notice mirrors, sector breadth, and flow dimensions.",
            ),
            DataSource(
                id="direct_http",
                name_cn="UZI 直连 HTTP 行情链",
                base_url="https://push2his.eastmoney.com",
                markets=("cn", "cn_equity", "cn_index", "hk_equity", "us_equity", "global_equity"),
                dims=("market", "kline"),
                tier="tier1",
                official=False,
                access="http",
                stability="medium",
                latency_level="low",
                notes="Runtime aggregate for EastMoney, Sina, Tencent, Yahoo Chart v8 and Stooq.",
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
                dims=("fundamental", "market", "capital_flow", "lhb"),
                tier="tier2",
                official=False,
                access="http",
                requires_key=True,
                stability="medium",
                latency_level="medium",
                notes="Optional keyed fallback for market, fundamentals, money flow and LHB datasets.",
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
                id="danjuan_public",
                name_cn="蛋卷基金公开数据",
                base_url="https://danjuanfunds.com",
                markets=("cn",),
                dims=("fund",),
                tier="tier2",
                official=False,
                access="http",
                stability="medium",
                latency_level="medium",
                notes="Public no-login fund overview and benchmark performance source; failures remain data gaps.",
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
            DataSource(
                id="hottrend",
                name_cn="六平台公开热榜",
                base_url="https://top.baidu.com",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("sentiment", "trap"),
                tier="tier2",
                official=False,
                access="http",
                stability="low",
                latency_level="medium",
                notes="Runtime aggregate for Weibo, Zhihu, Baidu, Douyin, Toutiao and Bilibili public hot lists.",
            ),
        ]
        by_id = {source.id: source for source in sources}
        for local in local_sources:
            upstream = by_id.get(local.id)
            if upstream is None:
                by_id[local.id] = local
                continue
            by_id[local.id] = replace(
                upstream,
                markets=tuple(dict.fromkeys((*upstream.markets, *local.markets))),
                dims=tuple(dict.fromkeys((*upstream.dims, *local.dims))),
                requires_key=upstream.requires_key or local.requires_key,
                notes=f"{upstream.notes} {local.notes}".strip(),
            )
        return list(by_id.values())

    @staticmethod
    def _uzi_sources() -> list[DataSource]:
        tier_map: dict[int, SourceTier] = {1: "tier1", 2: "tier2", 3: "tier3"}
        access_map: dict[str, SourceAccess] = {
            "http": "http",
            "akshare": "sdk",
            "yfinance": "sdk",
            "mx_api": "api",
            "playwright": "browser",
            "ddgs": "search",
        }
        stability_map: dict[str, SourceStability] = {
            "known_good": "high",
            "flaky": "low",
            "blocked_often": "low",
            "needs_browser": "medium",
        }
        official_ids = {
            "cninfo", "hkexnews", "sse", "szse", "csrc", "gov_cn", "miit", "ndrc", "samr",
            "shfe", "dce", "czce", "cfachina", "pbc", "safe", "stats_gov", "chinamoney",
            "chinabond", "ine",
        }
        rows: list[DataSource] = []
        for source_id, name_cn, base_url, markets, dims, tier, access, health in UZI_SOURCE_ROWS:
            upstream_dims = tuple(str(item) for item in dims)
            rows.append(
                DataSource(
                    id=str(source_id),
                    name_cn=str(name_cn),
                    base_url=str(base_url),
                    markets=mapped_markets(tuple(str(item) for item in markets)),
                    dims=mapped_dimensions(upstream_dims),
                    tier=tier_map[int(tier)],
                    official=str(source_id) in official_ids,
                    access=access_map[str(access)],
                    requires_key=str(source_id) == "mx_api",
                    stability=stability_map.get(str(health), "medium"),
                    latency_level="high" if str(access) == "playwright" else "medium",
                    notes="UZI-Skill 3.9.4 source registry parity entry; availability is verified per run.",
                    upstream_dims=upstream_dims,
                    health=str(health),
                    upstream="UZI-Skill@0acf25122cabf493e7bc442faff62ceceb7f2f02",
                )
            )
        return rows
