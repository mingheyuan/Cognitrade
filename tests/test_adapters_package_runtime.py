from __future__ import annotations

import unittest

from research_system.adapters import sources as sources_package
from research_system.adapters import storage as storage_package
from research_system.adapters import vendors as vendors_package
from research_system.adapters.sources.xueqiu_statuses import load_statuses
from research_system.adapters.storage.local_memory_adapter import LocalMemoryAdapter
from research_system.adapters.storage.qdrant_adapter import QdrantAdapter, QdrantUnavailableError
from research_system.adapters.storage.redis_route_store import (
    RedisRouteTableBackend,
    RouteTableBackendError,
)
from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.adapters.vendors.fallback_merge import merge_provider_facts
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.mcp_source_registry import McpSourceRegistry
from research_system.adapters.vendors.network_profile import NetworkProfile
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry


class AdaptersPackageRuntimeTests(unittest.TestCase):
    def test_sources_package_exports(self) -> None:
        self.assertIs(sources_package.load_statuses, load_statuses)

    def test_storage_package_exports(self) -> None:
        self.assertIs(storage_package.LocalMemoryAdapter, LocalMemoryAdapter)
        self.assertIs(storage_package.QdrantAdapter, QdrantAdapter)
        self.assertIs(storage_package.QdrantUnavailableError, QdrantUnavailableError)
        self.assertIs(storage_package.RedisRouteTableBackend, RedisRouteTableBackend)
        self.assertIs(storage_package.RouteTableBackendError, RouteTableBackendError)

    def test_vendors_package_exports(self) -> None:
        self.assertIs(vendors_package.DataSourceRegistry, DataSourceRegistry)
        self.assertIs(vendors_package.MarketRouter, MarketRouter)
        self.assertIs(vendors_package.McpSourceRegistry, McpSourceRegistry)
        self.assertIs(vendors_package.NetworkProfile, NetworkProfile)
        self.assertIs(vendors_package.VendorProviderRegistry, VendorProviderRegistry)
        self.assertIs(vendors_package.merge_provider_facts, merge_provider_facts)


if __name__ == "__main__":
    unittest.main()
