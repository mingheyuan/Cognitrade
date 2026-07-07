from __future__ import annotations

import unittest

from research_system.services.routing.matching import (
    RouteMatcher,
    RouteMatcherResultSupport,
    RouteMatcherSemanticSupport,
    RouteMatcherTextSupport,
)
from research_system.services.routing.tables import IntentRouteTable, IntentRouteTableStore


class RouteMatcherRuntimeTests(unittest.TestCase):
    def test_matcher_wires_text_semantic_and_result_support(self) -> None:
        matcher = RouteMatcher(IntentRouteTableStore(default_table=IntentRouteTable(version="v1", rules=[]), cache_ttl_seconds=0.0))

        self.assertIsInstance(matcher.text_support, RouteMatcherTextSupport)
        self.assertIsInstance(matcher.semantic_support, RouteMatcherSemanticSupport)
        self.assertIsInstance(matcher.result_support, RouteMatcherResultSupport)


if __name__ == "__main__":
    unittest.main()
