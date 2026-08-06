from __future__ import annotations

from typing import Any

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.adapters.vendors.network_profile import NetworkProfile
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.runtime.platform.settings import Settings


class ProviderHealthService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.data_sources = DataSourceRegistry()
        self.providers = VendorProviderRegistry(settings, self.data_sources)
        self.network_profile = NetworkProfile(timeout_seconds=settings.provider_probe_timeout_seconds)

    def build_report(self) -> dict[str, Any]:
        market_dims = (
            "market", "fundamental", "notice", "sector", "lhb", "capital_flow", "sentiment", "events",
            "policy", "kline", "macro", "peers", "chain", "research", "industry", "materials", "futures",
            "valuation", "governance", "moat", "trap", "contests", "fund_holders", "similar_stocks", "fund",
        )
        provider_rows = []
        for dim in market_dims:
            market = "cn_sector" if dim in {"sector", "policy"} else "cn" if dim == "fund" else "cn_equity"
            chain = self.providers.get_provider_chain(dim, market)
            configured = [
                provider
                for provider in self.providers.list_all()
                if dim in provider.dims
                and (market in provider.markets or "cn" in provider.markets and market.startswith("cn_"))
            ]
            provider_rows.append(
                {
                    "dim": dim,
                    "provider_ids": [item.provider_id for item in chain],
                    "source_ids": [item.source_id for item in chain],
                    "provider_count": len(chain),
                    "has_fallback": len(chain) >= 2,
                    "configured_provider_count": len(configured),
                    "disabled_provider_ids": [item.provider_id for item in configured if not item.enabled],
                }
            )
        return {
            "provider_rows": provider_rows,
            "data_sources": [
                {
                    "id": item.id,
                    "tier": item.tier,
                    "official": item.official,
                    "name_cn": item.name_cn,
                    "markets": list(item.markets),
                    "dims": list(item.dims),
                    "access": item.access,
                    "health": item.health,
                    "upstream_dims": list(item.upstream_dims),
                    "upstream": item.upstream,
                }
                for item in self.data_sources.list_all()
            ],
            "network_profile": self.network_profile.probe_defaults(),
            "sensitive_sources": {
                "enabled": self.settings.sensitive_sources_enabled,
                "allowlist": list(self.settings.sensitive_source_allowlist),
                "xueqiu_cookie_configured": bool(self.settings.xueqiu_cookie),
                "browser_sources_enabled": self.settings.browser_sources_enabled,
            },
            "credentials": {
                "tushare_token_configured": bool(self.settings.tushare_token),
                "mx_api_key_configured": bool(self.settings.mx_api_key),
            },
        }
