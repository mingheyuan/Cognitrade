from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from api_test_support import API_TOKEN, FakeOrchestrator, FakeOrchestratorFactory, auth_headers
from domain_packs.finance_ashare.tools.security_master import SecurityResolution
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.api.finance_market_data_service import FinanceMarketDataService
from research_system.api.paper_trading_service import PaperTradingService
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


REPO_ROOT = Path(__file__).resolve().parents[1]


class _SecurityMaster:
    def resolve(self, query: str, *, supplied_symbols=(), allow_network: bool = True):
        del supplied_symbols, allow_network
        symbol = "600519" if "茅台" in query or "600519" in query else "600036"
        name = "贵州茅台" if symbol == "600519" else "招商银行"
        return SecurityResolution(
            symbols=[symbol],
            matches=[{"symbol": symbol, "name": name, "source": "test"}],
            ambiguous_terms=[],
            source="test_security_master",
        )

    def rows(self):
        return [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "latest_price": 100.0,
                "pct_change": 1.2,
                "source": "test_security_master",
            },
            {
                "symbol": "600036",
                "name": "招商银行",
                "latest_price": 40.0,
                "pct_change": -0.3,
                "source": "test_security_master",
            },
        ]


def _collector(*, query, symbols, snapshot_types, **kwargs):
    del query, kwargs
    symbol = list(symbols)[0]
    snapshots = {
        "market": {
            "market_overview": {
                "latest_price": 100.0,
                "pct_change": 1.2,
                "pe_ttm": 18.0,
                "pb": 4.0,
                "market_cap": 1_000_000_000,
                "valuation_date": "2026-07-13",
            },
            "market_hist": {
                "start_date": "20260613",
                "end_date": "20260713",
                "adjust": "",
                "bar_count": 20,
                "latest_bar": {
                    "date": "2026-07-13",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "close": 100.0,
                    "volume": 2000,
                    "amount": 200000,
                    "turnover": 0.4,
                },
            },
        },
        "kline": {
            "history": {
                "start_date": "20260413",
                "end_date": "20260713",
                "adjust": "",
                "bar_count": 3,
                "bars": [
                    {"date": "2026-07-09", "open": 98.0, "high": 100.0, "low": 97.0, "close": 99.0, "volume": 1800, "amount": 178200},
                    {"date": "2026-07-10", "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0, "volume": 2000, "amount": 200000},
                    {"date": "2026-07-13", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 2200, "amount": 222200},
                ],
                "latest_bar": {"date": "2026-07-13", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 2200, "amount": 222200},
            },
        },
        "fundamental": {"info": {"股票简称": "贵州茅台"}, "banking_metrics": {}},
        "capital_flow": {"latest_flow": {"主力净流入": 1000}, "recent_flows": []},
        "sentiment": {"hot_keywords": ["白酒"], "news_samples": []},
        "notice": {"notice_count": 2, "recent_notices": []},
        "events": {"event_keywords": ["回购"], "event_notices": []},
        "lhb": {"latest_lhb_date": "2026-07-11", "buy_detail": [], "sell_detail": []},
    }
    return [
        {
            "snapshot_type": snapshot_type,
            "symbol": symbol,
            "snapshot_date": "2026-07-13",
            "facts": snapshots[snapshot_type],
            "primary_source": {"provider_id": "test_provider", "vendor": "test"},
        }
        for snapshot_type in snapshot_types
    ]


class ApiV2FinanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        workspace = Path(self.temp_dir.name)
        self.settings = Settings.from_env(REPO_ROOT).model_copy(
            update={
                "workspace_root": workspace,
                "runs_root": workspace / "runs",
                "memory_root": workspace / "memory",
                "domain_packs_root": REPO_ROOT / "domain_packs",
            }
        )
        ensure_workspace_layout(self.settings)
        self.market = FinanceMarketDataService(
            cache_root=workspace / "cache",
            collector=_collector,
            security_master=_SecurityMaster(),
        )
        self.database_path = workspace / "paper_trading" / "paper.sqlite3"
        self.paper = PaperTradingService(
            database_path=self.database_path,
            market_data=self.market,
            clock=lambda: datetime(2026, 7, 15, 2, 0, tzinfo=UTC),
        )
        self.app = create_app(
            settings=self.settings,
            api_config=ApiConfig(api_token=API_TOKEN),
            orchestrator_factory=FakeOrchestratorFactory(FakeOrchestrator()),
            finance_market_data_factory=lambda _settings: self.market,
            paper_trading_factory=lambda _settings, _market: self.paper,
            repo_root=REPO_ROOT,
        )
        self.client_context = TestClient(self.app, raise_server_exceptions=False)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_market_data_v2_aggregates_all_workspace_sections(self) -> None:
        response = self.client.get("/api/v2/finance/market-data/贵州茅台", headers=auth_headers())

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["symbol"], "600519")
        self.assertEqual(payload["name"], "贵州茅台")
        self.assertEqual(payload["sections"]["market"]["overview"]["latest_price"], 100.0)
        self.assertEqual(payload["sections"]["kline"]["bar_count"], 3)
        self.assertEqual(len(payload["sections"]["kline"]["bars"]), 3)
        self.assertEqual(payload["sections"]["kline"]["bars"][-1]["close"], 101.0)
        self.assertEqual(payload["sections"]["fundamental"]["info"]["股票简称"], "贵州茅台")
        self.assertEqual(payload["sections"]["events"]["event_keywords"], ["回购"])
        self.assertEqual(payload["quality_status"], "available")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_workspace_collection_serializes_provider_initialization(self) -> None:
        calls: list[int | None] = []

        def recording_collector(**kwargs):
            calls.append(kwargs.get("max_workers"))
            return _collector(**kwargs)

        service = FinanceMarketDataService(
            cache_root=Path(self.temp_dir.name) / "serial-cache",
            collector=recording_collector,
            security_master=_SecurityMaster(),
        )

        payload = service.get_market_data("600519", refresh=True)

        self.assertEqual(payload["symbol"], "600519")
        self.assertEqual(calls, [1])

    def test_v2_requires_the_same_bearer_authentication(self) -> None:
        response = self.client.get("/api/v2/finance/quotes/600519")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_required")

    def test_paper_account_executes_idempotent_buy_and_t_plus_one_sell(self) -> None:
        buy_request = {
            "symbol": "600519",
            "side": "buy",
            "quantity": 100,
            "client_order_id": "browser-order-1",
        }
        buy = self.client.post("/api/v2/finance/paper/orders", headers=auth_headers(), json=buy_request)
        duplicate = self.client.post("/api/v2/finance/paper/orders", headers=auth_headers(), json=buy_request)
        blocked_sell = self.client.post(
            "/api/v2/finance/paper/orders",
            headers=auth_headers(),
            json={**buy_request, "side": "sell", "client_order_id": "browser-order-2"},
        )

        self.assertEqual(buy.status_code, 201, buy.text)
        buy_payload = buy.json()
        self.assertEqual(buy_payload["status"], "filled")
        self.assertAlmostEqual(
            buy_payload["cash_effect"],
            -(buy_payload["gross_amount"] + buy_payload["fees"]["total"]),
            places=2,
        )
        self.assertEqual(duplicate.status_code, 201, duplicate.text)
        self.assertTrue(duplicate.json()["idempotent_replay"])
        self.assertEqual(blocked_sell.status_code, 409, blocked_sell.text)
        self.assertEqual(blocked_sell.json()["error"]["code"], "insufficient_position")

        after_buy = self.client.get("/api/v2/finance/paper/portfolio", headers=auth_headers()).json()
        self.assertAlmostEqual(
            after_buy["summary"]["cash"],
            after_buy["account"]["initial_cash"] + buy_payload["cash_effect"],
            places=2,
        )

        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE paper_position_lots SET available_date = ?",
                ("2026-07-14",),
            )
        sell = self.client.post(
            "/api/v2/finance/paper/orders",
            headers=auth_headers(),
            json={**buy_request, "side": "sell", "client_order_id": "browser-order-3"},
        )
        portfolio = self.client.get("/api/v2/finance/paper/portfolio", headers=auth_headers())
        order_history = self.client.get("/api/v2/finance/paper/orders", headers=auth_headers())

        self.assertEqual(sell.status_code, 201, sell.text)
        self.assertEqual(sell.json()["side"], "sell")
        self.assertEqual(portfolio.status_code, 200, portfolio.text)
        self.assertEqual(portfolio.json()["positions"], [])
        self.assertEqual(len(order_history.json()["orders"]), 2)

    def test_reset_archives_account_and_starts_a_new_generation(self) -> None:
        first = self.client.get("/api/v2/finance/paper/account", headers=auth_headers()).json()
        reset = self.client.post(
            "/api/v2/finance/paper/account/reset",
            headers=auth_headers(),
            json={"initial_cash": 500000},
        )

        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertNotEqual(reset.json()["account_id"], first["account_id"])
        self.assertEqual(reset.json()["generation"], first["generation"] + 1)
        self.assertEqual(reset.json()["cash"], 500000)


if __name__ == "__main__":
    unittest.main()
