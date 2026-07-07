from __future__ import annotations

import unittest

from research_system.services.routing.stock_target_resolution import (
    STOCK_NAME_ALIASES,
    extract_named_stock_symbols,
    extract_stock_codes,
    extract_stock_symbols,
    infer_query_intent_hint,
    primary_stock_symbol,
    stock_name_for_symbol,
)


class StockTargetResolutionRuntimeTests(unittest.TestCase):
    def test_routing_stock_target_exports_are_available(self) -> None:
        self.assertTrue(bool(STOCK_NAME_ALIASES))
        self.assertTrue(callable(extract_named_stock_symbols))
        self.assertTrue(callable(extract_stock_codes))
        self.assertTrue(callable(extract_stock_symbols))
        self.assertTrue(callable(infer_query_intent_hint))
        self.assertTrue(callable(primary_stock_symbol))
        self.assertTrue(callable(stock_name_for_symbol))


if __name__ == "__main__":
    unittest.main()
