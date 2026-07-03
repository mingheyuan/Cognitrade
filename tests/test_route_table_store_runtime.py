from __future__ import annotations

import unittest

from research_system.services.route_table import IntentRouteTable
from research_system.services.route_table_cache_support import RouteTableCacheSupport
from research_system.services.route_table_health_support import RouteTableHealthSupport
from research_system.services.route_table_load_support import RouteTableLoadSupport
from research_system.services.route_table_store import IntentRouteTableStore
from research_system.services.routing.tables.route_table_cache_support import RouteTableCacheSupport as PackageRouteTableCacheSupport
from research_system.services.routing.tables.route_table_health_support import RouteTableHealthSupport as PackageRouteTableHealthSupport
from research_system.services.routing.tables.route_table_load_support import RouteTableLoadSupport as PackageRouteTableLoadSupport
from research_system.services.routing.tables.route_table_store import IntentRouteTableStore as PackageIntentRouteTableStore


class RouteTableStoreRuntimeTests(unittest.TestCase):
    def test_route_table_store_exposes_split_supports(self) -> None:
        store = IntentRouteTableStore(default_table=IntentRouteTable(version="v1", rules=[]), cache_ttl_seconds=0.0)

        self.assertIsNotNone(store.cache)
        self.assertIsNotNone(store.health)
        self.assertIsNotNone(store.loader)
        self.assertIsInstance(store.cache, RouteTableCacheSupport)
        self.assertIsInstance(store.health, RouteTableHealthSupport)
        self.assertIsInstance(store.loader, RouteTableLoadSupport)
        self.assertIs(IntentRouteTableStore, PackageIntentRouteTableStore)
        self.assertIs(RouteTableCacheSupport, PackageRouteTableCacheSupport)
        self.assertIs(RouteTableHealthSupport, PackageRouteTableHealthSupport)
        self.assertIs(RouteTableLoadSupport, PackageRouteTableLoadSupport)


if __name__ == "__main__":
    unittest.main()
