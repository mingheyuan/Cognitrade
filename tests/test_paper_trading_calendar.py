from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from research_system.api.paper_trading_service import PaperTradingError, PaperTradingService
from research_system.api.trading_calendar import AShareTradingCalendar


class _MarketData:
    def get_quote(self, symbol: str, *, refresh: bool):
        return {
            "symbol": symbol,
            "name": "测试股票",
            "latest_price": 10,
            "as_of": "2026-07-17T10:00:00+08:00",
            "source": {"provider_id": "test"},
        }


class PaperTradingCalendarTests(unittest.TestCase):
    def test_friday_buy_becomes_available_on_next_exchange_session(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "paper.sqlite3"
            service = PaperTradingService(
                database_path=database,
                market_data=_MarketData(),
                clock=lambda: datetime(2026, 7, 17, 2, 0, tzinfo=UTC),
            )

            order = service.place_order(
                tenant_id="tenant",
                user_id="user",
                symbol="600000",
                side="buy",
                quantity=100,
                client_order_id="friday-buy",
            )
            with sqlite3.connect(database) as connection:
                available_date = connection.execute(
                    "SELECT available_date FROM paper_position_lots WHERE source_order_id = ?",
                    (order["order_id"],),
                ).fetchone()[0]

        self.assertEqual(order["trade_date"], "2026-07-17")
        self.assertEqual(available_date, "2026-07-20")

    def test_order_is_rejected_outside_continuous_auction_hours(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = PaperTradingService(
                database_path=Path(temporary_directory) / "paper.sqlite3",
                market_data=_MarketData(),
                clock=lambda: datetime(2026, 7, 15, 8, 30, tzinfo=UTC),
            )

            with self.assertRaises(PaperTradingError) as context:
                service.place_order(
                    tenant_id="tenant",
                    user_id="user",
                    symbol="600000",
                    side="buy",
                    quantity=100,
                    client_order_id="closed-order",
                )

        self.assertEqual(context.exception.code, "market_closed")

    def test_calendar_reports_market_sessions_and_lunch_break(self) -> None:
        calendar = AShareTradingCalendar()

        self.assertTrue(calendar.is_market_open(datetime(2026, 7, 15, 2, 0, tzinfo=UTC)))
        self.assertFalse(calendar.is_market_open(datetime(2026, 7, 15, 4, 0, tzinfo=UTC)))


if __name__ == "__main__":
    unittest.main()
