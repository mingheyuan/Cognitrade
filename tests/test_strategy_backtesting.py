from __future__ import annotations

import time
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from typing import Any

from research_system.strategies.backtesting.adapters import ExchangeExecutionAdapter, OTCExecutionAdapter
from research_system.strategies.backtesting.instruments import InstrumentResolver
from research_system.strategies.backtesting.market_data_gateway import MarketDataGateway
from research_system.strategies.backtesting.portfolio_ledger import run_holdings_portfolio, run_otc_single_asset
from research_system.strategies.backtesting.service import StrategyBacktestService
from research_system.strategies.contracts import BacktestMode
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict, StrategyDomainError
from research_system.strategies.models import BacktestRun, DailyReportNode, DailyReportSet
from research_system.strategies.service import OwnerScope, StrategyService
from research_system.strategies.signals import SignalEngineService
from research_system.strategies.verification import StrategyVerificationService


class FakeSignalCore:
    engine_version = "fake-hikyuu-crtSG-v1"

    def events(self, *, instrument, bars, signals, **_kwargs):
        if not signals:
            return []
        signal = signals[0]
        return [
            {
                "date": "2026-08-04",
                "side": "buy",
                "signal_id": signal["id"],
                "source_report_set_id": signal["source_report_set_id"],
                "technical_reason": "ma5_cross_above_ma20",
            },
            {
                "date": "2026-08-07",
                "side": "sell",
                "signal_id": signal["id"],
                "source_report_set_id": signal["source_report_set_id"],
                "technical_reason": "ma5_cross_below_ma20",
            },
        ]


class BlockingSignalCore(FakeSignalCore):
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def events(self, **kwargs):
        self.started.set()
        self.release.wait(5)
        return super().events(**kwargs)


class EarlyTerminalBacktestService(StrategyBacktestService):
    def __init__(self, *args, **kwargs) -> None:
        self.terminal_written = Event()
        self.release = Event()
        super().__init__(*args, **kwargs)

    def _execute(self, backtest_id: str) -> None:
        with self.database.session() as session:
            row = session.get(BacktestRun, backtest_id)
            row.status = "completed"
            row.completed_at = datetime.now(UTC)
        self.terminal_written.set()
        self.release.wait(5)


def _series_loader(_instrument, start_date: date, end_date: date, _as_of: date):
    rows = []
    cursor = max(start_date, date(2026, 8, 3))
    offset = 0
    while cursor <= end_date:
        if cursor.weekday() < 5:
            price = 10.0 + offset * 0.2
            rows.append(
                {
                    "date": cursor.isoformat(),
                    "open": price,
                    "high": price + 0.1,
                    "low": price - 0.1,
                    "close": price + 0.05,
                    "volume": 1000,
                    "amount": 10000,
                }
            )
            offset += 1
        cursor += timedelta(days=1)
    return rows


class StrategyBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.database = StrategyDatabase("sqlite:///:memory:")
        self.strategy_service = StrategyService(
            self.database,
            workspace_root=Path(self.temporary.name),
            auto_bootstrap=True,
        )
        self.owner = OwnerScope("tenant-a", "user-a", "actor-a")
        strategy = self.strategy_service.create_strategy(self.owner, name="回测策略")
        self.version = self.strategy_service.publish(self.owner, strategy["id"])
        self.strategy_id = strategy["id"]
        self.fund_assignment = self.strategy_service.create_assignment(
            self.owner,
            strategy_id=self.strategy_id,
            fund_code="510300",
            fund_name="测试ETF",
            mode=BacktestMode.FUND_ONLY,
        )
        self.holding_assignment = self.strategy_service.create_assignment(
            self.owner,
            strategy_id=self.strategy_id,
            fund_code="510300",
            fund_name="测试ETF",
            mode=BacktestMode.HOLDINGS_PORTFOLIO,
        )
        self.report_id = self._report()
        engine = SignalEngineService(self.database)
        engine.recompute(
            self.owner,
            assignment_id=self.fund_assignment["id"],
            report_set_id=self.report_id,
        )
        engine.recompute(
            self.owner,
            assignment_id=self.holding_assignment["id"],
            report_set_id=self.report_id,
        )
        self.backtests = StrategyBacktestService(
            self.database,
            market_data_gateway=MarketDataGateway(series_loader=_series_loader),
            object_store_root=Path(self.temporary.name) / "objects",
            signal_core=FakeSignalCore(),
            max_workers=1,
            recover=False,
        )

    def tearDown(self) -> None:
        self.backtests.close()
        self.strategy_service.close()
        self.temporary.cleanup()

    def _report(self) -> str:
        with self.database.session() as session:
            report = DailyReportSet(
                tenant_id=self.owner.tenant_id,
                user_id=self.owner.user_id,
                assignment_id=self.fund_assignment["id"],
                fund_code="510300",
                fund_name="测试ETF",
                as_of_date=date(2026, 8, 3),
                effective_date=date(2026, 8, 4),
                pin_set_hash="a" * 64,
                revision=1,
                status="final",
                max_top_n=10,
                count_coverage=1.0,
                weight_coverage=1.0,
                coverage=1.0,
                source_hash="b" * 64,
                source_run_id="source-run",
                source_artifact_id="fund-artifact",
                data_gaps_json=[],
                finalized_at=datetime.now(UTC),
            )
            session.add(report)
            session.flush()
            session.add(
                DailyReportNode(
                    tenant_id=self.owner.tenant_id,
                    user_id=self.owner.user_id,
                    report_set_id=report.id,
                    node_type="fund_report",
                    subject_code="510300",
                    subject_name="测试ETF",
                    canonical_key="fund:510300:2026-08-03",
                    status="completed",
                    confidence=1.0,
                    data_gaps_json=[],
                    source_json={
                        "fund_code": "510300",
                        "fund_name": "测试ETF",
                        "fund_type": "ETF",
                        "benchmark_name": "沪深300",
                        "disclosure_age_days": 20,
                        "report_allowed": True,
                        "critical_count": 0,
                        "factor_values": {
                            "year_1_return": 45,
                            "since_inception_annualized": 30,
                            "benchmark_excess_year_1": 25,
                            "max_drawdown": 0,
                            "volatility": 0,
                            "fee": 0,
                            "return_consistency": 100,
                        },
                        "confidence": 1.0,
                    },
                )
            )
            for index in range(10):
                session.add(
                    DailyReportNode(
                        tenant_id=self.owner.tenant_id,
                        user_id=self.owner.user_id,
                        report_set_id=report.id,
                        node_type="holding_uzi",
                        subject_code=f"600{index:03d}",
                        subject_name=f"股票{index}",
                        rank=index + 1,
                        disclosed_weight_pct=float(10 - index) / 2,
                        effective_weight_pct=float(10 - index) / 2,
                        canonical_key=f"uzi:600{index:03d}:2026-08-03",
                        status="completed",
                        confidence=1.0,
                        data_gaps_json=[],
                        source_json={
                            "uzi_overall_score": 85.0,
                            "report_allowed": True,
                            "critical_count": 0,
                            "supported": True,
                        },
                    )
                )
            return report.id

    def _wait(self, backtest_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = self.backtests.get(self.owner, backtest_id)
            if row["status"] not in {"queued", "running"}:
                return row
            time.sleep(0.01)
        self.fail("backtest did not finish")

    def test_fund_only_is_deterministic_and_every_trade_has_signal_lineage(self) -> None:
        request = {
            "strategy_version_id": self.version["id"],
            "fund_code": "510300",
            "mode": BacktestMode.FUND_ONLY,
            "start_date": date(2026, 8, 4),
            "end_date": date(2026, 8, 10),
            "initial_cash": 1_000_000,
        }
        first = self._wait(self.backtests.create(self.owner, **request)["id"])
        second = self._wait(self.backtests.create(self.owner, **request)["id"])
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["result_content_hash"], second["result_content_hash"])
        self.assertGreater(first["result"]["metrics"]["trade_count"], 0)
        self.assertTrue(all(trade["signal_id"] for trade in first["result"]["trades"]))
        self.assertTrue(all(trade["source_report_set_id"] for trade in first["result"]["trades"]))
        self.assertTrue(all(trade["strategy_id"] == self.strategy_id for trade in first["result"]["trades"]))
        self.assertTrue(all(trade["strategy_version_id"] == self.version["id"] for trade in first["result"]["trades"]))
        self.assertTrue(all(trade["strategy_content_hash"] == self.version["content_hash"] for trade in first["result"]["trades"]))
        self.assertTrue(all(trade["signal_lineage"] for trade in first["result"]["trades"]))
        self.assertEqual(first["result"]["benchmarks"]["category_index"]["symbol"], "000300")
        self.assertEqual(first["result"]["benchmarks"]["category_index"]["status"], "available")

    def test_holdings_portfolio_keeps_unallocated_weight_as_cash(self) -> None:
        completed = self._wait(
            self.backtests.create(
                self.owner,
                strategy_version_id=self.version["id"],
                fund_code="510300",
                mode=BacktestMode.HOLDINGS_PORTFOLIO,
                start_date=date(2026, 8, 4),
                end_date=date(2026, 8, 10),
                initial_cash=1_000_000,
            )["id"]
        )
        self.assertEqual(completed["status"], "completed")
        result = completed["result"]
        post_entry = next(row for row in result["equity_curve"] if row["date"] == "2026-08-05")
        self.assertGreater(post_entry["cash"], 650_000)
        self.assertTrue(all(trade["signal_ids"] for trade in result["trades"]))
        static = result["benchmarks"]["initial_disclosed_weights_static"]
        self.assertGreater(static["cash_weight"], 0.7)
        self.assertEqual(result["benchmarks"]["hs300"]["status"], "available")

    def test_verified_and_promoted_require_machine_checks_and_human_confirmation(self) -> None:
        request = {
            "strategy_version_id": self.version["id"],
            "fund_code": "510300",
            "mode": BacktestMode.FUND_ONLY,
            "start_date": date(2026, 8, 4),
            "end_date": date(2026, 8, 10),
            "initial_cash": 1_000_000,
        }
        first = self._wait(self.backtests.create(self.owner, **request)["id"])
        verification_service = StrategyVerificationService(self.database)
        failed = verification_service.create(
            self.owner,
            strategy_version_id=self.version["id"],
            backtest_run_id=first["id"],
            thresholds={"minimum_sample_days": 5},
        )
        self.assertFalse(failed["machine_passed"])
        self.assertFalse(failed["checks"]["deterministic_replay"]["passed"])
        with self.assertRaises(StrategyConflict):
            verification_service.confirm(self.owner, failed["id"])

        second = self._wait(self.backtests.create(self.owner, **request)["id"])
        passed = verification_service.create(
            self.owner,
            strategy_version_id=self.version["id"],
            backtest_run_id=second["id"],
            thresholds={"minimum_sample_days": 5},
        )
        failed_checks = {
            key: value
            for key, value in passed["checks"].items()
            if value.get("required", True) and not value.get("passed")
        }
        self.assertTrue(passed["machine_passed"], failed_checks)
        confirmed = verification_service.confirm(self.owner, passed["id"])
        self.assertEqual(confirmed["status"], "verified")
        promoted = self.strategy_service.promote(
            self.owner,
            strategy_id=self.strategy_id,
            version_id=self.version["id"],
            account_id="paper-default",
            fund_code="510300",
            mode=BacktestMode.FUND_ONLY,
        )
        self.assertEqual(promoted["governance_state"], "promoted")

    def test_verification_threshold_contract_rejects_non_finite_and_invalid_limits(self) -> None:
        for thresholds in (
            {"minimum_sample_days": 1.5},
            {"maximum_drawdown_pct": -1},
            {"maximum_fee_impact_pct": 101},
            {"minimum_sharpe": float("nan")},
        ):
            with self.subTest(thresholds=thresholds):
                with self.assertRaises(StrategyDomainError) as raised:
                    StrategyVerificationService._thresholds(thresholds)
                self.assertEqual(raised.exception.code, "invalid_verification_threshold")

    def test_running_backtest_renews_lease_and_honours_cancellation(self) -> None:
        core = BlockingSignalCore()
        service = StrategyBacktestService(
            self.database,
            market_data_gateway=MarketDataGateway(series_loader=_series_loader),
            object_store_root=Path(self.temporary.name) / "blocking-objects",
            signal_core=core,
            max_workers=1,
            recover=False,
            lease_seconds=1,
        )
        try:
            created = service.create(
                self.owner,
                strategy_version_id=self.version["id"],
                fund_code="510300",
                mode=BacktestMode.FUND_ONLY,
                start_date=date(2026, 8, 4),
                end_date=date(2026, 8, 10),
                initial_cash=1_000_000,
            )
            self.assertTrue(core.started.wait(2))
            with self.database.session() as session:
                first_lease = session.get(BacktestRun, created["id"]).lease_until
            time.sleep(1.15)
            with self.database.session() as session:
                second_lease = session.get(BacktestRun, created["id"]).lease_until
            self.assertGreater(second_lease, first_lease)
            service.cancel(self.owner, created["id"])
            core.release.set()
            completed = self._wait_with(service, created["id"])
            self.assertEqual(completed["status"], "cancelled")
        finally:
            core.release.set()
            service.close()

    def test_close_waits_for_running_worker_and_heartbeat(self) -> None:
        core = BlockingSignalCore()
        service = StrategyBacktestService(
            self.database,
            market_data_gateway=MarketDataGateway(series_loader=_series_loader),
            object_store_root=Path(self.temporary.name) / "closing-objects",
            signal_core=core,
            max_workers=1,
            recover=False,
            lease_seconds=1,
        )
        closed = Event()

        def close_service() -> None:
            service.close()
            closed.set()

        closer = Thread(target=close_service, name="test-backtest-close", daemon=True)
        try:
            created = service.create(
                self.owner,
                strategy_version_id=self.version["id"],
                fund_code="510300",
                mode=BacktestMode.FUND_ONLY,
                start_date=date(2026, 8, 4),
                end_date=date(2026, 8, 10),
                initial_cash=1_000_000,
            )
            self.assertTrue(core.started.wait(2))
            closer.start()
            self.assertFalse(closed.wait(0.1))

            core.release.set()
            self.assertTrue(closed.wait(2))
            closer.join(timeout=1)
            self.assertFalse(closer.is_alive())

            with self.database.session() as session:
                row = session.get(BacktestRun, created["id"])
                self.assertEqual(row.status, "completed")
        finally:
            core.release.set()
            service.close()

    def test_terminal_status_is_hidden_until_worker_exits(self) -> None:
        service = EarlyTerminalBacktestService(
            self.database,
            market_data_gateway=MarketDataGateway(series_loader=_series_loader),
            object_store_root=Path(self.temporary.name) / "terminal-visibility-objects",
            signal_core=FakeSignalCore(),
            max_workers=1,
            recover=False,
        )
        try:
            created = service.create(
                self.owner,
                strategy_version_id=self.version["id"],
                fund_code="510300",
                mode=BacktestMode.FUND_ONLY,
                start_date=date(2026, 8, 4),
                end_date=date(2026, 8, 10),
                initial_cash=1_000_000,
            )
            self.assertTrue(service.terminal_written.wait(2))
            self.assertEqual(service.get(self.owner, created["id"])["status"], "running")

            service.release.set()
            completed = self._wait_with(service, created["id"])
            self.assertEqual(completed["status"], "completed")
        finally:
            service.release.set()
            service.close()

    def test_backtest_rejects_unknown_benchmark_fields(self) -> None:
        with self.assertRaises(StrategyDomainError) as raised:
            self.backtests.create(
                self.owner,
                strategy_version_id=self.version["id"],
                fund_code="510300",
                mode=BacktestMode.FUND_ONLY,
                start_date=date(2026, 8, 4),
                end_date=date(2026, 8, 10),
                initial_cash=1_000_000,
                benchmark={"fast_period": 3},
            )
        self.assertEqual(raised.exception.code, "invalid_benchmark_config")

    def _wait_with(self, service: StrategyBacktestService, backtest_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = service.get(self.owner, backtest_id)
            if row["status"] not in {"queued", "running"}:
                return row
            time.sleep(0.01)
        self.fail("backtest did not finish")


class OTCFundLedgerTests(unittest.TestCase):
    def test_market_gateway_normalizes_chinese_fund_bars_and_enforces_as_of(self) -> None:
        instrument = InstrumentResolver.resolve_fund({"fund_type": "ETF"}, code="510300")
        gateway = MarketDataGateway(
            series_loader=lambda *_args: [
                {"日期": "2026-08-03", "开盘": 4.0, "最高": 4.1, "最低": 3.9, "收盘": 4.05, "成交量": 100, "成交额": 405},
                {"日期": "2026-08-04", "开盘": 4.1, "最高": 4.2, "最低": 4.0, "收盘": 4.15, "成交量": 120, "成交额": 498},
                {"日期": "2026-08-05", "开盘": 4.2, "最高": 4.3, "最低": 4.1, "收盘": 4.25, "成交量": 140, "成交额": 595},
            ]
        )
        series = gateway.get_series(
            instrument,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 4),
            as_of_date=date(2026, 8, 4),
        )
        self.assertEqual([row["date"] for row in series.bars], ["2026-08-03", "2026-08-04"])
        self.assertEqual(series.bars[-1]["close"], 4.15)
        with self.assertRaises(StrategyDomainError) as raised:
            gateway.get_series(
                instrument,
                start_date=date(2026, 8, 3),
                end_date=date(2026, 8, 5),
                as_of_date=date(2026, 8, 4),
            )
        self.assertEqual(raised.exception.code, "market_data_as_of_violation")

    def test_stock_exchange_fund_and_lof_use_distinct_fee_contracts(self) -> None:
        from research_system.strategies.contracts import blank_strategy_document

        document = blank_strategy_document()
        stock = ExchangeExecutionAdapter.stock(document)
        exchange_fund = ExchangeExecutionAdapter.exchange_fund(document)
        stock_sell = stock.fees("sell", 100_000)
        fund_sell = exchange_fund.fees("sell", 100_000)
        self.assertGreater(stock_sell.stamp_duty, 0)
        self.assertGreater(stock_sell.transfer_fee, 0)
        self.assertEqual(fund_sell.stamp_duty, 0)
        self.assertEqual(fund_sell.transfer_fee, 0)
        lof = InstrumentResolver.resolve_fund({"fund_type": "LOF"}, code="160119", name="测试LOF")
        self.assertEqual(lof.asset_type.value, "exchange_fund")

    def test_blocked_gate_does_not_manufacture_a_portfolio_sell(self) -> None:
        from research_system.strategies.contracts import blank_strategy_document

        instrument = InstrumentResolver.resolve_stock("600000", "测试股票")
        bars = {
            "600000": [
                {"date": f"2026-08-0{day}", "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0}
                for day in range(3, 8)
            ]
        }
        result = run_holdings_portfolio(
            instruments={"600000": instrument},
            bars_by_symbol=bars,
            events_by_symbol={"600000": [{"date": "2026-08-03", "side": "buy", "signal_id": "stock-1", "source_report_set_id": "report-1", "technical_reason": "ma_cross"}]},
            stock_signals={"600000": [{"id": "stock-1", "state": "ALLOW_BUY", "effective_from": "2026-08-03", "source_report_set_id": "report-1"}]},
            fund_signals=[{"id": "fund-1", "state": "ALLOW_BUY", "effective_from": "2026-08-03", "expires_after": "2026-08-04", "source_report_set_id": "report-1"}],
            report_schedule=[{"id": "report-1", "effective_date": "2026-08-03", "revision": 1, "target_weights": {"600000": 0.2}}],
            adapter=ExchangeExecutionAdapter.stock(blank_strategy_document()),
            initial_cash=100_000,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 7),
            max_single_stock_pct=20,
            drift_pct_points=2,
        )
        self.assertEqual([trade["side"] for trade in result["trades"]], ["buy"])
        self.assertGreater(result["ending_positions"]["600000"], 0)

    def test_missing_open_status_blocks_and_confirmation_rules_are_applied(self) -> None:
        instrument = InstrumentResolver.resolve_fund(
            {"fund_type": "偏股混合型", "fund_name": "场外基金"},
            code="110011",
        )
        bars = [
            {
                "date": (date(2026, 8, 3) + timedelta(days=index)).isoformat(),
                "open": 1.0 + index * 0.01,
                "high": 1.0 + index * 0.01,
                "low": 1.0 + index * 0.01,
                "close": 1.0 + index * 0.01,
                "purchase_open": None,
                "redemption_open": None,
            }
            for index in range(8)
        ]
        adapter = OTCExecutionAdapter(
            subscription_fee_rate=0.0015,
            redemption_fee_rate=0.005,
            subscription_confirmation_days=1,
            redemption_confirmation_days=2,
            require_open_status=True,
        )
        events = [
            {
                "date": "2026-08-03",
                "side": "buy",
                "signal_id": "signal-1",
                "source_report_set_id": "report-1",
                "technical_reason": "ma_cross",
            }
        ]
        with self.assertRaises(StrategyDomainError):
            run_otc_single_asset(
                instrument=instrument,
                bars=bars,
                events=events,
                adapter=adapter,
                initial_cash=100_000,
                start_date=date(2026, 8, 3),
                end_date=date(2026, 8, 10),
            )

        for row in bars:
            row["purchase_open"] = True
            row["redemption_open"] = True
        result = run_otc_single_asset(
            instrument=instrument,
            bars=bars,
            events=events,
            adapter=adapter,
            initial_cash=100_000,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 10),
        )
        self.assertEqual(result["trades"][0]["order_date"], "2026-08-04")
        self.assertEqual(result["trades"][0]["date"], "2026-08-05")
        self.assertTrue(result["execution_adapter"]["degraded_estimate"])


if __name__ == "__main__":
    unittest.main()
