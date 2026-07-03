from __future__ import annotations

import unittest

from research_system.services.route_matcher import RouteMatcher
from research_system.services.route_matcher_result_support import RouteMatcherResultSupport
from research_system.services.route_matcher_semantic_support import RouteMatcherSemanticSupport
from research_system.services.route_matcher_text_support import RouteMatcherTextSupport
from research_system.services.route_table import IntentRouteTable, IntentRouteTableStore


class RouteMatcherRuntimeTests(unittest.TestCase):
    def test_matcher_wires_text_semantic_and_result_support(self) -> None:
        matcher = RouteMatcher(IntentRouteTableStore(default_table=IntentRouteTable(version="v1", rules=[]), cache_ttl_seconds=0.0))

        self.assertIsInstance(matcher.text_support, RouteMatcherTextSupport)
        self.assertIsInstance(matcher.semantic_support, RouteMatcherSemanticSupport)
        self.assertIsInstance(matcher.result_support, RouteMatcherResultSupport)


if __name__ == "__main__":
    unittest.main()
