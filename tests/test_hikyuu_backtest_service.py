from __future__ import annotations

from datetime import date, timedelta
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research_system.api.hikyuu_backtest_service import HikyuuBacktestService


class _SyntheticMarket:
    def get_market_data(self, symbol: str, *, refresh: bool = False):
        del symbol, refresh
        bars = []
        current = date(2025, 1, 2)
        for index in range(90):
            while current.weekday() >= 5:
                current += timedelta(days=1)
            cycle = ((index % 18) - 9) * 0.7
            close = 100 + index * 0.08 + cycle
            bars.append(
                {
                    "date": current.isoformat(),
                    "open": close - 0.2,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 10_000,
                    "amount": close * 10_000,
                }
            )
            current += timedelta(days=1)
        return {
            "symbol": "600519",
            "name": "贵州茅台",
            "quality_status": "available",
            "sources": {"kline": {"provider_id": "synthetic-test"}},
            "sections": {"kline": {"bars": bars}},
        }


@unittest.skipUnless(find_spec("hikyuu"), "Hikyuu optional dependency is not installed")
class HikyuuBacktestServiceTests(unittest.TestCase):
    def test_runs_native_engine_and_persists_replayable_result(self) -> None:
        with TemporaryDirectory() as temporary:
            service = HikyuuBacktestService(
                result_root=Path(temporary),
                market_data=_SyntheticMarket(),
            )

            result = service.run(
                symbol="600519",
                initial_cash=1_000_000,
                fast_period=5,
                slow_period=20,
                trade_size=1_000,
            )
            persisted = service.get_result(result["backtest_id"])
            summaries = service.list_results()

        self.assertEqual(result["engine"]["name"], "hikyuu")
        self.assertEqual(result["period"]["bar_count"], 90)
        self.assertEqual(len(result["equity_curve"]), 90)
        self.assertIn("total_return_pct", result["metrics"])
        self.assertEqual(persisted["backtest_id"], result["backtest_id"])
        self.assertEqual(summaries[0]["backtest_id"], result["backtest_id"])


if __name__ == "__main__":
    unittest.main()
