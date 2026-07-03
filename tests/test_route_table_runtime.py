from __future__ import annotations

import unittest

from research_system.services.route_table import (
    IntentRouteRule,
    IntentRouteTable,
    IntentRouteTableStore,
    build_default_intent_route_table,
    build_default_intent_route_table_from_settings,
    build_intent_route_table_store,
)
from research_system.services.routing.tables import (
    IntentRouteRule as PackageIntentRouteRule,
    IntentRouteTable as PackageIntentRouteTable,
    IntentRouteTableStore as PackageIntentRouteTableStore,
    build_default_intent_route_table as package_build_default_intent_route_table,
    build_default_intent_route_table_from_settings as package_build_default_intent_route_table_from_settings,
    build_intent_route_table_store as package_build_intent_route_table_store,
)


class RouteTableRuntimeTests(unittest.TestCase):
    def test_route_table_facade_exports_models_defaults_and_store_builder(self) -> None:
        default_table = build_default_intent_route_table()

        self.assertIsInstance(default_table, IntentRouteTable)
        self.assertTrue(default_table.rules)
        self.assertIsInstance(default_table.rules[0], IntentRouteRule)
        self.assertIsInstance(build_default_intent_route_table_from_settings(None), IntentRouteTable)
        self.assertIsInstance(IntentRouteTableStore(default_table=default_table, cache_ttl_seconds=0.0), IntentRouteTableStore)
        self.assertIsInstance(build_intent_route_table_store(None), IntentRouteTableStore)
        self.assertIs(IntentRouteRule, PackageIntentRouteRule)
        self.assertIs(IntentRouteTable, PackageIntentRouteTable)
        self.assertIs(IntentRouteTableStore, PackageIntentRouteTableStore)
        self.assertIs(build_default_intent_route_table, package_build_default_intent_route_table)
        self.assertIs(build_default_intent_route_table_from_settings, package_build_default_intent_route_table_from_settings)
        self.assertIs(build_intent_route_table_store, package_build_intent_route_table_store)


if __name__ == "__main__":
    unittest.main()
