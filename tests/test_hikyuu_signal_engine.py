from __future__ import annotations

from datetime import date, timedelta
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from research_system.strategies.backtesting.hikyuu_signal_engine import prepare_hikyuu_home
from research_system.strategies.backtesting.instruments import InstrumentResolver
from research_system.strategies.backtesting.signal_core import HikyuuSignalCore


def _synthetic_bars() -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
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
    return bars


class HikyuuSignalEngineTests(unittest.TestCase):
    def test_prepares_local_only_runtime_home(self) -> None:
        with TemporaryDirectory() as temporary:
            home = Path(temporary)
            prepare_hikyuu_home(home)
            with sqlite3.connect(home / ".hikyuu" / "hub.db") as connection:
                row = connection.execute(
                    "SELECT name, hub_type, url FROM hub_repo WHERE id = 1"
                ).fetchone()

        self.assertEqual(row, ("default", "local", ""))

    @unittest.skipUnless(find_spec("hikyuu"), "Hikyuu optional dependency is not installed")
    def test_report_gate_crtsg_preserves_external_signal_lineage(self) -> None:
        bars = _synthetic_bars()
        signals = [
            {
                "id": "allow-signal",
                "state": "ALLOW_BUY",
                "effective_from": bars[0]["date"],
                "source_report_set_id": "report-allow",
                "revision": 1,
            },
            {
                "id": "blocked-signal",
                "state": "BLOCKED",
                "effective_from": bars[45]["date"],
                "source_report_set_id": "report-blocked",
                "revision": 1,
            },
            {
                "id": "avoid-signal",
                "state": "AVOID",
                "effective_from": bars[70]["date"],
                "source_report_set_id": "report-avoid",
                "revision": 1,
            },
        ]

        with TemporaryDirectory() as temporary:
            events = HikyuuSignalCore(Path(temporary)).events(
                instrument=InstrumentResolver.resolve_stock("600519", "贵州茅台"),
                bars=bars,
                signals=signals,
                fast_period=5,
                slow_period=20,
            )

        self.assertTrue(all(row.get("signal_id") for row in events))
        self.assertFalse(
            any(
                row.get("signal_id") == "blocked-signal"
                and row.get("technical_reason") == "report_gate_avoid"
                for row in events
            )
        )
        self.assertTrue(
            any(
                row.get("signal_id") == "avoid-signal"
                and row.get("side") == "sell"
                and row.get("technical_reason") == "report_gate_avoid"
                for row in events
            )
        )


if __name__ == "__main__":
    unittest.main()
