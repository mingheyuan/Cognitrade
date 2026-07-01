from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.runtime.settings import Settings


ProviderAccess = Literal["sdk", "http", "browser", "official_disclosure"]


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    source_id: str
    vendor_name: str
    markets: tuple[str, ...]
    dims: tuple[str, ...]
    access: ProviderAccess
    requires_key: bool = False
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
        override = self._env_override(dim)
        if override:
            resolved = [self._providers[item] for item in override if item in self._providers]
            if resolved:
                return resolved

        normalized_dim = dim.strip().lower()
        normalized_market = market.strip().lower()
        rows = [
            provider
            for provider in self._providers.values()
            if provider.enabled
            and normalized_dim in provider.dims
            and (normalized_market in provider.markets or "cn" in provider.markets and normalized_market.startswith("cn_"))
        ]
        return rows

    def describe_dim(self, dim: str, market: str) -> dict[str, Any]:
        chain = self.get_provider_chain(dim, market)
        return {
            "dim": dim,
            "market": market,
            "provider_ids": [item.provider_id for item in chain],
            "source_ids": [item.source_id for item in chain],
        }

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
            ),
            ProviderSpec(
                provider_id="efinance_market",
                source_id="efinance",
                vendor_name="efinance",
                markets=("cn", "cn_equity"),
                dims=("market",),
                access="sdk",
                notes="Optional tertiary market fallback.",
            ),
            ProviderSpec(
                provider_id="akshare_fundamental",
                source_id="akshare",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("fundamental",),
                access="sdk",
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
                notes="Keyed fallback for fundamentals.",
            ),
            ProviderSpec(
                provider_id="cninfo_fundamental",
                source_id="cninfo",
                vendor_name="cninfo",
                markets=("cn", "cn_equity"),
                dims=("fundamental",),
                access="official_disclosure",
                notes="Official fallback for partial fundamental facts.",
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
                notes="Official fallback for SSE issuers.",
            ),
            ProviderSpec(
                provider_id="szse_notice",
                source_id="szse",
                vendor_name="szse",
                markets=("cn", "cn_equity"),
                dims=("notice",),
                access="official_disclosure",
                notes="Official fallback for SZSE issuers.",
            ),
            ProviderSpec(
                provider_id="eastmoney_notice",
                source_id="eastmoney",
                vendor_name="eastmoney",
                markets=("cn", "cn_equity"),
                dims=("notice",),
                access="http",
                notes="Mirror fallback for notice headlines and links.",
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
                notes="Sector breadth and fallback provider.",
            ),
            ProviderSpec(
                provider_id="akshare_lhb",
                source_id="akshare",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("lhb",),
                access="sdk",
                notes="Primary lhb snapshot provider.",
            ),
            ProviderSpec(
                provider_id="akshare_capital_flow",
                source_id="akshare",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("capital_flow",),
                access="sdk",
                notes="Primary capital flow snapshot provider.",
            ),
            ProviderSpec(
                provider_id="akshare_sentiment",
                source_id="akshare",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("sentiment",),
                access="sdk",
                notes="Primary sentiment snapshot provider.",
            ),
            ProviderSpec(
                provider_id="akshare_events",
                source_id="cninfo_events",
                vendor_name="akshare",
                markets=("cn", "cn_equity"),
                dims=("events",),
                access="sdk",
                notes="Primary event snapshot provider built on cninfo disclosures.",
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
        ]
