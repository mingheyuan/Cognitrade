from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Literal

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.runtime.platform.settings import Settings


ProviderAccess = Literal["sdk", "http", "browser", "official_disclosure", "search", "api"]


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    source_id: str
    vendor_name: str
    markets: tuple[str, ...]
    dims: tuple[str, ...]
    access: ProviderAccess
    requires_key: bool = False
    dependency: str | None = None
    priority: int = 100
    enabled: bool = True
    notes: str = ""


class VendorProviderRegistry:
    def __init__(self, settings: Settings, data_sources: DataSourceRegistry | None = None) -> None:
        self.settings = settings
        self.data_sources = data_sources or DataSourceRegistry()
        self._providers = {provider.provider_id: provider for provider in self._default_providers()}

    def get(self, provider_id: str) -> ProviderSpec | None:
        return self._providers.get(provider_id)

    def list_all(self) -> list[ProviderSpec]:
        return list(self._providers.values())

    def get_provider_chain(self, dim: str, market: str) -> list[ProviderSpec]:
        normalized_dim = dim.strip().lower()
        normalized_market = market.strip().lower()
        override = self._env_override(dim)
        if override:
            resolved = [
                self._providers[item]
                for item in override
                if item in self._providers
                and self._providers[item].enabled
                and self._credentials_available(self._providers[item])
                and normalized_dim in self._providers[item].dims
                and (
                    normalized_market in self._providers[item].markets
                    or "cn" in self._providers[item].markets and normalized_market.startswith("cn_")
                )
            ]
            if resolved:
                return resolved

        rows = [
            provider
            for provider in self._providers.values()
            if provider.enabled
            and self._credentials_available(provider)
            and normalized_dim in provider.dims
            and (normalized_market in provider.markets or "cn" in provider.markets and normalized_market.startswith("cn_"))
        ]
        return sorted(rows, key=lambda provider: provider.priority)

    def source_confidence(self, provider: ProviderSpec | None) -> str:
        source = self.data_sources.get(provider.source_id) if provider else None
        return source.stability if source is not None else "low"

    def _credentials_available(self, provider: ProviderSpec) -> bool:
        if provider.dependency and find_spec(provider.dependency) is None:
            return False
        if provider.access == "browser":
            permitted = (
                self.settings.sensitive_sources_enabled
                and provider.source_id.lower() in self.settings.sensitive_source_allowlist
                and (provider.source_id != "xueqiu" or bool(self.settings.xueqiu_cookie))
            )
            return permitted and (
                provider.provider_id == "xueqiu_contests" or self.settings.browser_sources_enabled
            )
        if not provider.requires_key:
            return True
        if provider.provider_id.startswith("tushare_"):
            return bool(self.settings.tushare_token)
        if provider.provider_id.startswith("mx_api_"):
            return bool(self.settings.mx_api_key)
        return False

    def _env_override(self, dim: str) -> list[str]:
        env_map = self.settings.provider_policy_overrides
        value = env_map.get(dim.strip().lower())
        if not value:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    def _default_providers(self) -> list[ProviderSpec]:
        return [
            ProviderSpec(
                provider_id="akshare_market",
                source_id="akshare",
                vendor_name="akshare",
                markets=("cn", "cn_equity", "cn_index"),
                dims=("market",),
                access="sdk",
                priority=10,
                notes="Primary market snapshot provider.",
            ),
            ProviderSpec(
                provider_id="baostock_market",
                source_id="baostock",
                vendor_name="baostock",
                markets=("cn", "cn_equity", "cn_index"),
                dims=("market",),
                access="sdk",
                notes="Fallback market snapshot provider.",
                dependency="baostock",
                priority=40,
            ),
            ProviderSpec(
                provider_id="efinance_market",
                source_id="efinance",
                vendor_name="efinance",
                markets=("cn", "cn_equity"),
                dims=("market",),
                access="sdk",
                dependency="efinance",
                priority=20,
                notes="Zero-key A/H/US market fallback aligned with UZI 3.9.4.",
            ),
            ProviderSpec(
                provider_id="tushare_market",
                source_id="tushare",
                vendor_name="tushare",
                markets=("cn", "cn_equity", "cn_index"),
                dims=("market",),
                access="http",
                requires_key=True,
                dependency="tushare",
                priority=30,
                notes="Keyed A-share market fallback.",
            ),
            ProviderSpec(
                provider_id="direct_http_market",
                source_id="direct_http",
                vendor_name="direct_http",
                markets=("cn", "cn_equity", "cn_index", "hk_equity", "us_equity", "global_equity"),
                dims=("market",),
                access="http",
                priority=50,
                notes="EastMoney/Sina/Tencent/Yahoo/Stooq direct HTTP fallback chain.",
            ),
            ProviderSpec(
                provider_id="mx_api_fundamental",
                source_id="mx_api",
                vendor_name="mx_api",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("fundamental",),
                access="api",
                requires_key=True,
                priority=5,
                notes="Authenticated EastMoney MX NLP snapshot provider.",
            ),
            ProviderSpec(
                provider_id="akshare_fundamental",
                source_id="akshare",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("fundamental",),
                access="sdk",
                dependency="akshare",
                priority=10,
                notes="Primary fundamental snapshot provider.",
            ),
            ProviderSpec(
                provider_id="tushare_fundamental",
                source_id="tushare",
                vendor_name="tushare",
                markets=("cn", "cn_equity"),
                dims=("fundamental",),
                access="http",
                requires_key=True,
                dependency="tushare",
                priority=20,
                notes="Keyed fallback for fundamentals.",
            ),
            ProviderSpec(
                provider_id="baostock_fundamental",
                source_id="baostock",
                vendor_name="baostock",
                markets=("cn", "cn_equity"),
                dims=("fundamental",),
                access="sdk",
                dependency="baostock",
                priority=30,
                notes="Zero-key quarterly statement and ratio fallback.",
            ),
            ProviderSpec(
                provider_id="yfinance_fundamental",
                source_id="yfinance",
                vendor_name="yfinance",
                markets=("hk_equity", "us_equity", "global_equity"),
                dims=("fundamental",),
                access="sdk",
                dependency="yfinance",
                priority=10,
                notes="HK/US/global financial statement provider.",
            ),
            ProviderSpec(
                provider_id="cninfo_fundamental",
                source_id="cninfo",
                vendor_name="cninfo",
                markets=("cn", "cn_equity"),
                dims=("fundamental",),
                access="official_disclosure",
                enabled=False,
                notes="Reserved until an official-report metric parser is installed.",
            ),
            ProviderSpec(
                provider_id="cninfo_notice",
                source_id="cninfo",
                vendor_name="cninfo",
                markets=("cn", "cn_equity"),
                dims=("notice",),
                access="official_disclosure",
                notes="Primary notice provider.",
            ),
            ProviderSpec(
                provider_id="sse_notice",
                source_id="sse",
                vendor_name="sse",
                markets=("cn", "cn_equity"),
                dims=("notice",),
                access="official_disclosure",
                enabled=False,
                notes="Reserved until the SSE notice adapter is installed.",
            ),
            ProviderSpec(
                provider_id="szse_notice",
                source_id="szse",
                vendor_name="szse",
                markets=("cn", "cn_equity"),
                dims=("notice",),
                access="official_disclosure",
                enabled=False,
                notes="Reserved until the SZSE notice adapter is installed.",
            ),
            ProviderSpec(
                provider_id="eastmoney_notice",
                source_id="eastmoney",
                vendor_name="eastmoney",
                markets=("cn", "cn_equity"),
                dims=("notice",),
                access="http",
                enabled=False,
                notes="Reserved until the EastMoney notice adapter is installed.",
            ),
            ProviderSpec(
                provider_id="akshare_sector",
                source_id="akshare",
                vendor_name="akshare",
                markets=("cn", "cn_sector"),
                dims=("sector",),
                access="sdk",
                notes="Primary sector snapshot provider.",
            ),
            ProviderSpec(
                provider_id="eastmoney_sector",
                source_id="eastmoney",
                vendor_name="eastmoney",
                markets=("cn", "cn_sector"),
                dims=("sector",),
                access="http",
                enabled=False,
                notes="Reserved until an independent EastMoney sector adapter is installed.",
            ),
            ProviderSpec(
                provider_id="akshare_lhb",
                source_id="akshare_lhb",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("lhb",),
                access="sdk",
                priority=10,
                notes="Primary lhb snapshot provider.",
            ),
            ProviderSpec(
                provider_id="tushare_lhb",
                source_id="tushare",
                vendor_name="tushare",
                markets=("cn", "cn_equity"),
                dims=("lhb",),
                access="api",
                requires_key=True,
                dependency="tushare",
                priority=20,
                notes="TuShare top_list/top_inst LHB fallback; account points and interface permission apply.",
            ),
            ProviderSpec(
                provider_id="yuncaijing_lhb",
                source_id="yuncaijing",
                vendor_name="playwright",
                markets=("cn", "cn_equity"),
                dims=("lhb",),
                access="browser",
                priority=30,
                notes="Explicitly enabled Playwright LHB evidence fallback.",
            ),
            ProviderSpec(
                provider_id="akshare_capital_flow",
                source_id="em_data",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("capital_flow",),
                access="sdk",
                priority=10,
                notes="Primary capital flow snapshot provider.",
            ),
            ProviderSpec(
                provider_id="tushare_capital_flow",
                source_id="tushare",
                vendor_name="tushare",
                markets=("cn", "cn_equity"),
                dims=("capital_flow",),
                access="api",
                requires_key=True,
                dependency="tushare",
                priority=20,
                notes="TuShare moneyflow fallback; account points and interface permission apply.",
            ),
            ProviderSpec(
                provider_id="akshare_sentiment",
                source_id="em_data",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("sentiment",),
                access="sdk",
                priority=10,
                notes="Primary sentiment snapshot provider.",
            ),
            ProviderSpec(
                provider_id="ddgs_sentiment",
                source_id="ddgs",
                vendor_name="ddgs",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("sentiment",),
                access="search",
                dependency="ddgs",
                priority=20,
            ),
            ProviderSpec(
                provider_id="uzi_news_sentiment",
                source_id="jin10_flash",
                vendor_name="uzi_news_http",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("sentiment",),
                access="http",
                priority=30,
            ),
            ProviderSpec(
                provider_id="hottrend_sentiment",
                source_id="hottrend",
                vendor_name="hottrend",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("sentiment",),
                access="http",
                priority=40,
            ),
            ProviderSpec(
                provider_id="akshare_events",
                source_id="cninfo",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("events",),
                access="sdk",
                priority=10,
                notes="Primary event snapshot provider built on cninfo disclosures.",
            ),
            ProviderSpec(
                provider_id="uzi_news_events",
                source_id="jin10_flash",
                vendor_name="uzi_news_http",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("events",),
                access="http",
                priority=20,
            ),
            ProviderSpec(
                provider_id="ddgs_events",
                source_id="ddgs",
                vendor_name="ddgs",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("events",),
                access="search",
                dependency="ddgs",
                priority=30,
            ),
            ProviderSpec(
                provider_id="akshare_policy",
                source_id="akshare",
                vendor_name="akshare",
                markets=("cn", "cn_sector"),
                dims=("policy",),
                access="sdk",
                notes="Primary policy snapshot provider derived from catalyst signals.",
            ),
            ProviderSpec(
                provider_id="ddgs_policy",
                source_id="ddgs",
                vendor_name="ddgs",
                markets=("cn", "cn_sector", "hk_equity", "us_equity"),
                dims=("policy",),
                access="search",
                dependency="ddgs",
                priority=20,
            ),
            ProviderSpec(
                provider_id="uzi_news_policy",
                source_id="jin10_flash",
                vendor_name="uzi_news_http",
                markets=("cn", "cn_sector", "hk_equity", "us_equity"),
                dims=("policy",),
                access="http",
                priority=30,
            ),
            ProviderSpec(
                provider_id="xueqiu_contests",
                source_id="xueqiu",
                vendor_name="xueqiu",
                markets=("cn", "cn_equity"),
                dims=("contests",),
                access="browser",
                requires_key=True,
                notes="Authenticated Xueqiu cube source; disabled unless the global sensitive-source switch, xueqiu allowlist entry and cookie are all configured.",
            ),
            ProviderSpec(
                provider_id="uzi_kline_multi",
                source_id="em_push2",
                vendor_name="uzi_market_chain",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("kline",),
                access="http",
                priority=10,
                notes="Same executable market fallback chain as market snapshots, requested with a one-year window.",
            ),
            ProviderSpec(
                provider_id="uzi_macro_multi",
                source_id="ddgs",
                vendor_name="uzi_macro",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("macro",),
                access="search",
                dependency="ddgs",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_peers_multi",
                source_id="em_data",
                vendor_name="uzi_peers",
                markets=("cn", "cn_equity"),
                dims=("peers",),
                access="sdk",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_chain_multi",
                source_id="em_data",
                vendor_name="uzi_chain",
                markets=("cn", "cn_equity"),
                dims=("chain",),
                access="sdk",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_research_multi",
                source_id="em_data",
                vendor_name="uzi_research",
                markets=("cn", "cn_equity"),
                dims=("research",),
                access="sdk",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_industry_multi",
                source_id="em_data",
                vendor_name="uzi_industry",
                markets=("cn", "cn_equity"),
                dims=("industry",),
                access="sdk",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_materials_multi",
                source_id="ddgs",
                vendor_name="uzi_materials",
                markets=("cn", "cn_equity"),
                dims=("materials",),
                access="search",
                dependency="ddgs",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_futures_multi",
                source_id="ddgs",
                vendor_name="uzi_futures",
                markets=("cn", "cn_equity"),
                dims=("futures",),
                access="search",
                dependency="ddgs",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_valuation_multi",
                source_id="em_push2",
                vendor_name="uzi_valuation",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("valuation",),
                access="http",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_governance_multi",
                source_id="cninfo",
                vendor_name="uzi_governance",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("governance",),
                access="http",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_moat_multi",
                source_id="ddgs",
                vendor_name="uzi_moat",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("moat",),
                access="search",
                dependency="ddgs",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_trap_multi",
                source_id="ddgs",
                vendor_name="uzi_trap",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("trap",),
                access="search",
                dependency="ddgs",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_contests_multi",
                source_id="guba_em_list",
                vendor_name="uzi_contests",
                markets=("cn", "cn_equity", "hk_equity", "us_equity"),
                dims=("contests",),
                access="http",
                priority=20,
                notes="Public contest evidence chain; authenticated Xueqiu is separately policy-gated.",
            ),
            ProviderSpec(
                provider_id="uzi_fund_holders",
                source_id="em_data",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("fund_holders",),
                access="sdk",
                priority=10,
            ),
            ProviderSpec(
                provider_id="uzi_similar_stocks",
                source_id="em_data",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("similar_stocks",),
                access="sdk",
                priority=10,
            ),
            ProviderSpec(
                provider_id="eastmoney_fund",
                source_id="eastmoney",
                vendor_name="eastmoney",
                markets=("cn",),
                dims=("fund",),
                access="http",
                notes="Public fund catalog, ranking, NAV, holdings, manager tenure and scale/share source.",
            ),
            ProviderSpec(
                provider_id="danjuan_fund",
                source_id="danjuan_public",
                vendor_name="danjuan",
                markets=("cn",),
                dims=("fund",),
                access="http",
                notes="Public no-login fund overview and benchmark-performance source.",
            ),
        ]
