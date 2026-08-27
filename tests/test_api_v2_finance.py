from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from tests.api_test_support import API_TOKEN, FakeRunService, FakeRunServiceFactory, auth_headers
from domain_packs.finance_ashare.tools.security_master import SecurityResolution
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.api.finance_market_data_service import FinanceMarketDataService
from research_system.api.paper_trading_service import PaperTradingError, PaperTradingService
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


REPO_ROOT = Path(__file__).resolve().parents[1]


class _SecurityMaster:
    def __init__(self) -> None:
        self.resolve_allow_network: list[bool] = []
        self.rows_calls = 0

    def resolve(self, query: str, *, supplied_symbols=(), allow_network: bool = True):
        del supplied_symbols
        self.resolve_allow_network.append(bool(allow_network))
        symbol = "600519" if "茅台" in query or "600519" in query else "600036"
        name = "贵州茅台" if symbol == "600519" else "招商银行"
        return SecurityResolution(
            symbols=[symbol],
            matches=[{"symbol": symbol, "name": name, "source": "test"}],
            ambiguous_terms=[],
            source="test_security_master",
        )

    def rows(self):
        self.rows_calls += 1
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


class _StrategyPaperExecution:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict]] = []

    def submit(self, scope, **payload):
        self.calls.append((scope, dict(payload)))
        return {
            "id": "strategy-paper-order-1",
            "binding_id": payload["binding_id"],
            "signal_id": payload["signal_id"],
            "client_order_id": payload["client_order_id"],
            "status": "filled",
        }

    def list(self, scope, *, binding_id=None):
        self.calls.append((scope, {"binding_id": binding_id}))
        return {"orders": []}


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
        self.market_collector_calls: list[dict] = []

        def recording_market_collector(**kwargs):
            self.market_collector_calls.append(dict(kwargs))
            return _collector(**kwargs)

        self.security_master = _SecurityMaster()
        self.market = FinanceMarketDataService(
            cache_root=workspace / "cache",
            collector=recording_market_collector,
            security_master=self.security_master,
        )
        self.database_path = workspace / "paper_trading" / "paper.sqlite3"
        self.paper = PaperTradingService(
            database_path=self.database_path,
            market_data=self.market,
            clock=lambda: datetime(2026, 7, 15, 2, 0, tzinfo=UTC),
        )
        self.assertEqual(
            self.paper.matcher.runtime_root,
            (self.database_path.parent / "veighna_runtime").resolve(),
        )
        self.assertTrue((self.paper.matcher.runtime_root / ".vntrader").is_dir())
        self.strategy_paper = _StrategyPaperExecution()
        self.app = create_app(
            settings=self.settings,
            api_config=ApiConfig(api_token=API_TOKEN),
            run_service_factory=FakeRunServiceFactory(FakeRunService()),
            finance_market_data_factory=lambda _settings: self.market,
            paper_trading_factory=lambda _settings, _market: self.paper,
            strategy_paper_execution_factory=lambda _database, _paper: self.strategy_paper,
            repo_root=REPO_ROOT,
        )
        self.client_context = TestClient(self.app, raise_server_exceptions=False)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_market_data_v2_requires_an_explicitly_published_bundle(self) -> None:
        response = self.client.get("/api/v2/finance/market-data/贵州茅台", headers=auth_headers())

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "published_bundle_missing")
        # Opening the workspace is a DB-only read.  The injected raw collector
        # is reserved for explicit jobs and must not be used as a GET fallback.
        self.assertEqual(self.market_collector_calls, [])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_public_finance_gets_are_db_only_after_center_binding(self) -> None:
        self.security_master.resolve_allow_network.clear()
        self.security_master.rows_calls = 0

        securities = self.client.get(
            "/api/v2/finance/securities",
            params={"query": "贵州茅台"},
            headers=auth_headers(),
        )
        quote = self.client.get(
            "/api/v2/finance/quotes/600519",
            params={"refresh": "true"},
            headers=auth_headers(),
        )
        workspace = self.client.get(
            "/api/v2/finance/market-data/600519",
            params={"refresh": "true"},
            headers=auth_headers(),
        )

        self.assertEqual(securities.status_code, 200, securities.text)
        self.assertEqual(quote.status_code, 409, quote.text)
        self.assertEqual(quote.json()["error"]["code"], "published_bundle_missing")
        self.assertEqual(workspace.status_code, 409, workspace.text)
        self.assertEqual(workspace.json()["error"]["code"], "published_bundle_missing")
        self.assertEqual(self.market_collector_calls, [])
        self.assertEqual(self.security_master.rows_calls, 0)
        self.assertTrue(self.security_master.resolve_allow_network)
        self.assertFalse(any(self.security_master.resolve_allow_network))

    def test_bundle_quote_projection_is_db_only_and_ignores_get_refresh(self) -> None:
        class _PublishedBundleCenter:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def get_workspace_market_data(self, **kwargs):
                self.calls.append(dict(kwargs))
                return {
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "as_of_date": "2026-07-15",
                    "cutoff_at": "2026-07-15T07:00:00+00:00",
                    "quality_status": "available",
                    "bundle_id": "bundle-1",
                    "bundle_manifest_hash": "a" * 64,
                    "sections": {
                        "market": {
                            "symbol": "600519",
                            "price": 123.45,
                            "pct_change": 1.5,
                            "observed_at": "2026-07-15T06:59:00+00:00",
                        },
                        "kline": {"ohlcv": []},
                    },
                }

        center = _PublishedBundleCenter()
        self.market.bind_market_data_center(center)
        self.security_master.resolve_allow_network.clear()
        self.security_master.rows_calls = 0

        quote = self.market.get_quote("贵州茅台", refresh=True)
        results = self.market.search_securities("600519")

        self.assertEqual(quote["latest_price"], 123.45)
        self.assertEqual(quote["source"]["mode"], "bundle_only")
        self.assertTrue(quote["refresh_requested"])
        self.assertFalse(quote["refresh_performed"])
        self.assertEqual(results[0]["latest_price"], 123.45)
        self.assertEqual(self.market_collector_calls, [])
        self.assertEqual(self.security_master.rows_calls, 0)
        self.assertFalse(any(self.security_master.resolve_allow_network))
        self.assertEqual(
            center.calls,
            [
                {"symbol": "600519", "as_of_date": None},
                {"symbol": "600519", "as_of_date": None},
            ],
        )

    def test_explicit_live_quote_still_uses_the_execution_collector(self) -> None:
        quote = self.market.get_live_quote("600519", refresh=True)

        self.assertEqual(quote["latest_price"], 100.0)
        self.assertEqual(len(self.market_collector_calls), 1)
        self.assertTrue(self.security_master.resolve_allow_network[-1])

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

    def test_strategy_paper_order_requires_and_forwards_one_idempotency_key(self) -> None:
        payload = {
            "signal_id": "signal-1",
            "side": "buy",
            "quantity": 100,
            "technical_reason": "ma5_cross_above_ma20",
            "trading_date": "2026-08-04",
        }
        missing = self.client.post(
            "/api/v2/finance/paper-strategy-bindings/binding-1/orders",
            headers=auth_headers(),
            json=payload,
        )
        self.assertEqual(missing.status_code, 400, missing.text)
        self.assertEqual(missing.json()["error"]["code"], "idempotency_key_required")

        created = self.client.post(
            "/api/v2/finance/paper-strategy-bindings/binding-1/orders",
            headers={**auth_headers(), "Idempotency-Key": "strategy-order-1"},
            json=payload,
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["client_order_id"], "strategy-order-1")
        scope, forwarded = self.strategy_paper.calls[-1]
        self.assertEqual(scope.tenant_id, "api_token")
        self.assertEqual(scope.user_id, "api_token")
        self.assertEqual(forwarded["binding_id"], "binding-1")
        self.assertEqual(forwarded["client_order_id"], "strategy-order-1")

        replay = self.client.post(
            "/api/v2/finance/paper-strategy-bindings/binding-1/orders",
            headers={**auth_headers(), "Idempotency-Key": "strategy-order-1"},
            json=payload,
        )
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.headers["Idempotent-Replay"], "true")
        self.assertEqual(len(self.strategy_paper.calls), 1)

        reused = self.client.post(
            "/api/v2/finance/paper-strategy-bindings/binding-1/orders",
            headers={**auth_headers(), "Idempotency-Key": "strategy-order-1"},
            json={**payload, "quantity": 200},
        )
        self.assertEqual(reused.status_code, 409, reused.text)
        self.assertEqual(reused.json()["error"]["code"], "idempotency_key_reused")

        mismatch = self.client.post(
            "/api/v2/finance/paper-strategy-bindings/binding-1/orders",
            headers={**auth_headers(), "Idempotency-Key": "header-key"},
            json={**payload, "client_order_id": "body-key"},
        )
        self.assertEqual(mismatch.status_code, 400, mismatch.text)
        self.assertEqual(mismatch.json()["error"]["code"], "idempotency_key_mismatch")

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

        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE paper_position_lots SET available_date = ?",
                ("2026-07-14",),
            )
            connection.commit()
        finally:
            connection.close()
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
        second_page = self.client.get(
            "/api/v2/finance/paper/orders?limit=1&offset=1",
            headers=auth_headers(),
        ).json()
        self.assertEqual(second_page["total_count"], 2)
        self.assertEqual(second_page["limit"], 1)
        self.assertEqual(second_page["offset"], 1)
        self.assertEqual(len(second_page["orders"]), 1)

    def test_paper_service_uses_frozen_execution_profile_and_rejects_key_reuse(self) -> None:
        profile = {
            "model_id": "strategy-stock:test-version",
            "asset_type": "stock",
            "board_lot": 10,
            "commission_rate": 0,
            "minimum_commission": 0,
            "transfer_fee_rate": 0,
            "stamp_duty_rate": 0,
            "settlement": "T+1",
        }
        order = self.paper.place_order(
            tenant_id="api_token",
            user_id="api_token",
            symbol="600519",
            side="buy",
            quantity=10,
            client_order_id="frozen-profile-order",
            execution_profile=profile,
        )
        self.assertEqual(order["execution_profile"], profile)
        self.assertEqual(order["fees"]["total"], 0)
        with self.assertRaises(PaperTradingError) as reused:
            self.paper.place_order(
                tenant_id="api_token",
                user_id="api_token",
                symbol="600519",
                side="buy",
                quantity=20,
                client_order_id="frozen-profile-order",
                execution_profile=profile,
            )
        self.assertEqual(reused.exception.code, "idempotency_key_reused")

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

    def test_veighna_limit_order_reserves_and_releases_cash(self) -> None:
        engine = self.client.get("/api/v2/finance/paper/engine", headers=auth_headers())
        pending = self.client.post(
            "/api/v2/finance/paper/orders",
            headers=auth_headers(),
            json={
                "symbol": "600519",
                "side": "buy",
                "quantity": 100,
                "order_type": "limit",
                "limit_price": 99,
                "client_order_id": "limit-order-1",
            },
        )

        self.assertEqual(engine.status_code, 200, engine.text)
        self.assertEqual(engine.json()["engine"], "veighna-paperaccount")
        self.assertTrue(engine.json()["available"])
        self.assertEqual(pending.status_code, 201, pending.text)
        pending_payload = pending.json()
        self.assertEqual(pending_payload["status"], "pending")
        self.assertEqual(pending_payload["order_type"], "limit")
        self.assertGreater(pending_payload["reserved_cash"], 0)

        frozen = self.client.get(
            "/api/v2/finance/paper/portfolio",
            headers=auth_headers(),
        ).json()
        self.assertEqual(frozen["summary"]["open_order_count"], 1)
        self.assertGreater(frozen["summary"]["frozen_cash"], 0)
        self.assertLess(frozen["summary"]["cash"], frozen["summary"]["cash_balance"])

        cancelled = self.client.post(
            f"/api/v2/finance/paper/orders/{pending_payload['order_id']}/cancel",
            headers=auth_headers(),
        )
        released = self.client.get(
            "/api/v2/finance/paper/portfolio",
            headers=auth_headers(),
        ).json()
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(released["summary"]["open_order_count"], 0)
        self.assertEqual(released["summary"]["frozen_cash"], 0)
        self.assertEqual(released["summary"]["cash"], released["summary"]["cash_balance"])

    def test_veighna_limit_order_matches_and_updates_the_ledger(self) -> None:
        latest_price = 100.0

        def mutable_quote(_symbol: str, *, refresh: bool = False):
            del refresh
            return {
                "symbol": "600519",
                "name": "贵州茅台",
                "latest_price": latest_price,
                "bid_price_1": latest_price * 0.9995,
                "ask_price_1": latest_price * 1.0005,
                "as_of": "2026-07-15T02:00:00+00:00",
                "source": {"provider_id": "mutable-test"},
                "tradable": True,
            }

        self.market.get_live_quote = mutable_quote  # type: ignore[method-assign]
        pending = self.client.post(
            "/api/v2/finance/paper/orders",
            headers=auth_headers(),
            json={
                "symbol": "600519",
                "side": "buy",
                "quantity": 100,
                "order_type": "limit",
                "limit_price": 99,
                "client_order_id": "limit-order-trigger",
            },
        )
        self.assertEqual(pending.status_code, 201, pending.text)
        self.assertEqual(pending.json()["status"], "pending")

        latest_price = 98.0
        matched = self.client.post(
            "/api/v2/finance/paper/orders/match",
            headers=auth_headers(),
        )
        portfolio = self.client.get(
            "/api/v2/finance/paper/portfolio",
            headers=auth_headers(),
        ).json()

        self.assertEqual(matched.status_code, 200, matched.text)
        self.assertEqual(matched.json()["matched_count"], 1)
        filled = matched.json()["matched"][0]
        self.assertEqual(filled["status"], "filled")
        self.assertEqual(filled["matching_engine"], "veighna-paperaccount")
        self.assertLessEqual(filled["fill_price"], 99)
        self.assertEqual(filled["reserved_cash"], 0)
        self.assertEqual(portfolio["summary"]["open_order_count"], 0)
        self.assertEqual(portfolio["summary"]["frozen_cash"], 0)
        self.assertEqual(portfolio["positions"][0]["quantity"], 100)
        self.assertAlmostEqual(
            portfolio["summary"]["cash_balance"],
            portfolio["account"]["initial_cash"] + filled["cash_effect"],
            places=2,
        )

    def test_legacy_free_parameter_backtest_api_is_not_exposed(self) -> None:
        engine = self.client.get("/api/v2/finance/backtests/engine", headers=auth_headers())
        run = self.client.post(
            "/api/v2/finance/backtests",
            headers=auth_headers(),
            json={"symbol": "600519", "strategy": "ma_cross"},
        )

        self.assertEqual(engine.status_code, 404, engine.text)
        self.assertEqual(run.status_code, 404, run.text)

    def test_openapi_and_cors_publish_the_complete_strategy_contract(self) -> None:
        schema = self.client.get("/openapi.json").json()
        paths = set(schema["paths"])
        expected = {
            "/api/v2/finance/strategies",
            "/api/v2/finance/strategies/{strategy_id}/draft",
            "/api/v2/finance/strategies/{strategy_id}/publish",
            "/api/v2/finance/strategy-assignments",
            "/api/v2/finance/report-jobs",
            "/api/v2/finance/report-backfills",
            "/api/v2/finance/daily-report-sets",
            "/api/v2/finance/signal-previews",
            "/api/v2/finance/trade-signals",
            "/api/v2/finance/strategy-backtests",
            "/api/v2/finance/strategy-verifications",
            "/api/v2/finance/paper-strategy-bindings/{binding_id}/orders",
            "/api/v2/finance/strategy-paper-orders",
        }
        self.assertFalse(expected - paths, expected - paths)
        self.assertNotIn("/api/v2/finance/backtests", paths)
        self.assertNotIn("/api/v2/finance/backtests/engine", paths)

        preflight = self.client.options(
            "/api/v2/finance/strategies/example",
            headers={
                "Origin": "http://localhost:4173",
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "Authorization,If-Match,Idempotency-Key",
            },
        )
        self.assertEqual(preflight.status_code, 200, preflight.text)
        allowed = str(preflight.headers.get("access-control-allow-headers") or "").lower()
        for header in ("authorization", "if-match", "idempotency-key"):
            self.assertIn(header, allowed)
        self.assertIn("DELETE", str(preflight.headers.get("access-control-allow-methods")))


if __name__ == "__main__":
    unittest.main()
