from __future__ import annotations

import unittest

from research_system.services.routing.stock_target_resolution import (
    STOCK_NAME_ALIASES as package_stock_name_aliases,
    extract_named_stock_symbols as package_extract_named_stock_symbols,
    extract_stock_codes as package_extract_stock_codes,
    extract_stock_symbols as package_extract_stock_symbols,
    infer_query_intent_hint as package_infer_query_intent_hint,
    primary_stock_symbol as package_primary_stock_symbol,
    stock_name_for_symbol as package_stock_name_for_symbol,
)
from research_system.services.stock_target_resolution import (
    STOCK_NAME_ALIASES,
    extract_named_stock_symbols,
    extract_stock_codes,
    extract_stock_symbols,
    infer_query_intent_hint,
    primary_stock_symbol,
    stock_name_for_symbol,
)


class StockTargetResolutionRuntimeTests(unittest.TestCase):
    def test_top_level_exports_match_routing_package_exports(self) -> None:
        self.assertIs(STOCK_NAME_ALIASES, package_stock_name_aliases)
        self.assertIs(extract_named_stock_symbols, package_extract_named_stock_symbols)
        self.assertIs(extract_stock_codes, package_extract_stock_codes)
        self.assertIs(extract_stock_symbols, package_extract_stock_symbols)
        self.assertIs(infer_query_intent_hint, package_infer_query_intent_hint)
        self.assertIs(primary_stock_symbol, package_primary_stock_symbol)
        self.assertIs(stock_name_for_symbol, package_stock_name_for_symbol)


if __name__ == "__main__":
    unittest.main()
