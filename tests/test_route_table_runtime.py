from __future__ import annotations

import unittest

from research_system.services.routing.tables import (
    IntentRouteRule,
    IntentRouteTable,
    IntentRouteTableStore,
    build_default_intent_route_table,
    build_default_intent_route_table_from_settings,
    build_intent_route_table_store,
)


class RouteTableRuntimeTests(unittest.TestCase):
    def test_route_table_package_exports_models_defaults_and_store_builder(self) -> None:
        default_table = build_default_intent_route_table()

        self.assertIsInstance(default_table, IntentRouteTable)
        self.assertTrue(default_table.rules)
        self.assertIsInstance(default_table.rules[0], IntentRouteRule)
        self.assertIsInstance(build_default_intent_route_table_from_settings(None), IntentRouteTable)
        self.assertIsInstance(IntentRouteTableStore(default_table=default_table, cache_ttl_seconds=0.0), IntentRouteTableStore)
        self.assertIsInstance(build_intent_route_table_store(None), IntentRouteTableStore)


if __name__ == "__main__":
    unittest.main()
