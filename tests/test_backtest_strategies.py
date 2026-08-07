from __future__ import annotations

import unittest

from research_system.api.backtest_strategies import (
    minimum_required_bars,
    resolve_strategy,
    strategy_catalog,
)
from research_system.api.hikyuu_backtest_service import BacktestError


class BacktestStrategyRegistryTests(unittest.TestCase):
    def test_catalog_exposes_dynamic_parameter_contracts(self) -> None:
        catalog = {row["id"]: row for row in strategy_catalog()}

        self.assertEqual(set(catalog), {"ma_cross", "price_ma_cross"})
        self.assertEqual(
            [row["id"] for row in catalog["ma_cross"]["parameters"]],
            ["fast_period", "slow_period"],
        )

    def test_defaults_are_resolved_and_drive_history_requirement(self) -> None:
        strategy, parameters = resolve_strategy("price_ma_cross", {})

        self.assertEqual(parameters, {"lookback_period": 20})
        self.assertEqual(minimum_required_bars(strategy, parameters), 25)

    def test_invalid_or_unknown_parameters_are_rejected(self) -> None:
        with self.assertRaises(BacktestError) as relationship:
            resolve_strategy("ma_cross", {"fast_period": 30, "slow_period": 20})
        self.assertEqual(relationship.exception.code, "invalid_strategy_parameters")

        with self.assertRaises(BacktestError) as unknown:
            resolve_strategy("price_ma_cross", {"fast_period": 5})
        self.assertEqual(unknown.exception.code, "invalid_strategy_parameters")


if __name__ == "__main__":
    unittest.main()
