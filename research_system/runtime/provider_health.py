from __future__ import annotations

from typing import Any

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.adapters.vendors.network_profile import NetworkProfile
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.runtime.settings import Settings


class ProviderHealthService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.data_sources = DataSourceRegistry()
        self.providers = VendorProviderRegistry(settings, self.data_sources)
        self.network_profile = NetworkProfile(timeout_seconds=settings.provider_probe_timeout_seconds)

    def build_report(self) -> dict[str, Any]:
        market_dims = ("market", "fundamental", "notice", "sector", "lhb", "capital_flow", "sentiment", "events", "policy")
        provider_rows = []
        for dim in market_dims:
            market = "cn_sector" if dim in {"sector", "policy"} else "cn_equity"
            chain = self.providers.get_provider_chain(dim, market)
            provider_rows.append(
                {
                    "dim": dim,
                    "provider_ids": [item.provider_id for item in chain],
                    "source_ids": [item.source_id for item in chain],
                    "provider_count": len(chain),
                    "has_fallback": len(chain) >= 2,
                }
            )
        return {
            "provider_rows": provider_rows,
            "data_sources": [
                {
                    "id": item.id,
                    "tier": item.tier,
                    "official": item.official,
                    "dims": list(item.dims),
                }
                for item in self.data_sources.list_all()
            ],
            "network_profile": self.network_profile.probe_defaults(),
        }
