from __future__ import annotations

import time
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Any

from research_system.strategies.backtesting.adapters import (
    ExchangeExecutionAdapter,
    OTCExecutionAdapter,
)
from research_system.strategies.backtesting.instruments import InstrumentResolver
from research_system.strategies.backtesting.market_data_gateway import MarketDataGateway
from research_system.strategies.backtesting.portfolio_ledger import (
    run_holdings_portfolio,
    run_otc_single_asset,
)
from research_system.strategies.backtesting.service import StrategyBacktestService
from research_system.strategies.contracts import BacktestMode, StrategyDocumentV1
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict, StrategyDomainError
from research_system.strategies.models import (
    BacktestRun,
    DailyReportNode,
    DailyReportSet,
)
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
        strategy = self.strategy_service.create_strategy(self.owner, name="回测策略", document=StrategyDocumentV1().model_dump(mode="json"))
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

    def test_fund_only_retry_is_deterministic_and_preserves_trade_lineage(self) -> None:
        request = {
            "strategy_version_id": self.version["id"],
            "fund_code": "510300",
            "mode": BacktestMode.FUND_ONLY,
            "start_date": date(2026, 8, 4),
            "end_date": date(2026, 8, 10),
            "initial_cash": 1_000_000,
        }
        first = self._wait(self.backtests.create(self.owner, **request)["id"])
        second = self._wait(self.backtests.retry(self.owner, first["id"])["id"])
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["parent_backtest_run_id"], first["id"])
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
        self.assertEqual(
            len(first["result"]["benchmarks"]["fund_buy_and_hold"]["equity_curve"]),
            5,
        )
        self.assertEqual(
            first["result"]["benchmarks"]["fund_buy_and_hold"]["equity_curve"][-1]["return_pct"],
            first["result"]["benchmarks"]["fund_buy_and_hold"]["return_pct"],
        )

    def test_fund_only_includes_requested_hs300_benchmark(self) -> None:
        completed = self._wait(
            self.backtests.create(
                self.owner,
                strategy_version_id=self.version["id"],
                fund_code="510300",
                mode=BacktestMode.FUND_ONLY,
                start_date=date(2026, 8, 4),
                end_date=date(2026, 8, 10),
                initial_cash=1_000_000,
                benchmark={"hs300": True},
            )["id"]
        )

        hs300 = completed["result"]["benchmarks"]["hs300"]
        self.assertEqual(hs300["status"], "available")
        self.assertEqual(hs300["symbol"], "000300")
        self.assertEqual(len(hs300["equity_curve"]), 5)

    def test_backtest_history_can_be_scoped_before_it_reaches_the_frontend(self) -> None:
        common = {
            "strategy_version_id": self.version["id"],
            "fund_code": "510300",
            "start_date": date(2026, 8, 4),
            "end_date": date(2026, 8, 10),
            "initial_cash": 1_000_000,
        }
        fund = self._wait(
            self.backtests.create(self.owner, mode=BacktestMode.FUND_ONLY, **common)["id"]
        )
        holdings = self._wait(
            self.backtests.create(
                self.owner,
                mode=BacktestMode.HOLDINGS_PORTFOLIO,
                **common,
            )["id"]
        )

        scoped = self.backtests.list(
            self.owner,
            strategy_id=self.strategy_id,
            fund_code="510300",
            mode=BacktestMode.FUND_ONLY,
            status="completed",
            limit=1,
        )

        self.assertEqual([row["id"] for row in scoped["backtests"]], [fund["id"]])
        self.assertNotEqual(fund["id"], holdings["id"])

    def test_fund_only_loads_final_day_report_for_valuation_without_trading_its_future_signal(self) -> None:
        with self.database.session() as session:
            source = next(
                node
                for node in session.query(DailyReportNode).filter_by(report_set_id=self.report_id)
                if node.node_type == "fund_report"
            )
            report = DailyReportSet(
                tenant_id=self.owner.tenant_id,
                user_id=self.owner.user_id,
                assignment_id=self.fund_assignment["id"],
                fund_code="510300",
                fund_name="测试ETF",
                as_of_date=date(2026, 8, 10),
                effective_date=date(2026, 8, 11),
                pin_set_hash="c" * 64,
                revision=1,
                status="final",
                max_top_n=10,
                count_coverage=1.0,
                weight_coverage=1.0,
                coverage=1.0,
                source_hash="d" * 64,
                source_run_id="source-run-final-day",
                source_artifact_id="fund-artifact-final-day",
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
                    canonical_key="fund:510300:2026-08-10",
                    status="completed",
                    confidence=1.0,
                    data_gaps_json=[],
                    source_json=dict(source.source_json or {}),
                )
            )
            final_day_report_id = report.id
        SignalEngineService(self.database).recompute(
            self.owner,
            assignment_id=self.fund_assignment["id"],
            report_set_id=final_day_report_id,
        )
        completed = self._wait(
            self.backtests.create(
                self.owner,
                strategy_version_id=self.version["id"],
                fund_code="510300",
                mode=BacktestMode.FUND_ONLY,
                start_date=date(2026, 8, 4),
                end_date=date(2026, 8, 10),
                initial_cash=1_000_000,
            )["id"]
        )
        self.assertEqual(completed["status"], "completed")
        self.assertIn(final_day_report_id, completed["result"]["report_set_ids"])
        self.assertTrue(
            all(
                trade["source_report_set_id"] == self.report_id
                for trade in completed["result"]["trades"]
            )
        )

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
        self.assertTrue(passed["machine_passed"])
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

    def test_active_duplicate_is_reused_except_after_cancellation_request(self) -> None:
        core = BlockingSignalCore()
        service = StrategyBacktestService(
            self.database,
            market_data_gateway=MarketDataGateway(series_loader=_series_loader),
            object_store_root=Path(self.temporary.name) / "deduplicated-objects",
            signal_core=core,
            max_workers=1,
            recover=False,
        )
        request = {
            "strategy_version_id": self.version["id"],
            "fund_code": "510300",
            "mode": BacktestMode.FUND_ONLY,
            "start_date": date(2026, 8, 4),
            "end_date": date(2026, 8, 10),
            "initial_cash": 1_000_000,
        }
        try:
            first = service.create(self.owner, **request)
            self.assertEqual(first["report_policy"], "existing_only")
            self.assertTrue(core.started.wait(2))

            duplicate = service.create(self.owner, **request)
            self.assertEqual(duplicate["id"], first["id"])

            service.cancel(self.owner, first["id"])
            replacement = service.create(self.owner, **request)
            self.assertNotEqual(replacement["id"], first["id"])

            core.release.set()
            self.assertEqual(self._wait_with(service, first["id"])["status"], "cancelled")
            self.assertEqual(self._wait_with(service, replacement["id"])["status"], "completed")

            replay = service.create(self.owner, **request)
            self.assertNotEqual(replay["id"], replacement["id"])
            self.assertEqual(self._wait_with(service, replay["id"])["status"], "completed")
        finally:
            core.release.set()
            service.close()

    def test_retry_only_reuses_active_child_with_the_same_parent(self) -> None:
        core = BlockingSignalCore()
        service = StrategyBacktestService(
            self.database,
            market_data_gateway=MarketDataGateway(series_loader=_series_loader),
            object_store_root=Path(self.temporary.name) / "retry-lineage-objects",
            signal_core=core,
            max_workers=1,
            recover=False,
        )
        request = {
            "strategy_version_id": self.version["id"],
            "fund_code": "510300",
            "mode": BacktestMode.FUND_ONLY,
            "start_date": date(2026, 8, 4),
            "end_date": date(2026, 8, 10),
            "initial_cash": 1_000_000,
        }
        try:
            ordinary_active = service.create(self.owner, **request)
            self.assertTrue(core.started.wait(2))
            with self.database.session() as session:
                active_row = session.get(BacktestRun, ordinary_active["id"])
                failed = BacktestRun(
                    tenant_id=active_row.tenant_id,
                    user_id=active_row.user_id,
                    strategy_id=active_row.strategy_id,
                    strategy_version_id=active_row.strategy_version_id,
                    strategy_content_hash=active_row.strategy_content_hash,
                    fund_code=active_row.fund_code,
                    fund_name=active_row.fund_name,
                    mode=active_row.mode,
                    start_date=active_row.start_date,
                    end_date=active_row.end_date,
                    initial_cash=active_row.initial_cash,
                    benchmark_json=dict(active_row.benchmark_json or {}),
                    request_json=dict(active_row.request_json or {}),
                    input_snapshot_hash=active_row.input_snapshot_hash,
                    engine_version=active_row.engine_version,
                    status="failed",
                    error_summary="synthetic failed attempt",
                    completed_at=datetime.now(UTC),
                )
                session.add(failed)
                session.flush()
                failed_id = failed.id

            retry = service.retry(self.owner, failed_id)
            self.assertNotEqual(retry["id"], ordinary_active["id"])
            self.assertEqual(retry["parent_backtest_run_id"], failed_id)

            duplicate_retry = service.retry(self.owner, failed_id)
            self.assertEqual(duplicate_retry["id"], retry["id"])
            self.assertEqual(duplicate_retry["parent_backtest_run_id"], failed_id)

            core.release.set()
            self.assertEqual(
                self._wait_with(service, ordinary_active["id"])["status"],
                "completed",
            )
            self.assertEqual(self._wait_with(service, retry["id"])["status"], "completed")
        finally:
            core.release.set()
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
    def test_stock_gateway_reads_bundle_at_backtest_as_of_date(self) -> None:
        class _DateAwareMarketData:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def get_market_data(self, symbol: str, **kwargs):
                self.calls.append({"symbol": symbol, **kwargs})
                return {
                    "sections": {
                        "kline": {
                            "ohlcv": [
                                {
                                    "date": "2026-08-04",
                                    "open": 10,
                                    "high": 11,
                                    "low": 9,
                                    "close": 10.5,
                                    "volume": 100,
                                    "amount": 1050,
                                }
                            ]
                        }
                    },
                    "sources": {"kline": {"provider": "published_bundle"}},
                }

        market_data = _DateAwareMarketData()
        gateway = MarketDataGateway(market_data)
        series = gateway.get_series(
            InstrumentResolver.resolve_stock("600519", "贵州茅台"),
            start_date=date(2026, 8, 4),
            end_date=date(2026, 8, 4),
            as_of_date=date(2026, 8, 4),
        )

        self.assertEqual(series.bars[-1]["close"], 10.5)
        self.assertEqual(
            market_data.calls,
            [
                {
                    "symbol": "600519",
                    "refresh": False,
                    "as_of_date": date(2026, 8, 4),
                }
            ],
        )

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

    def test_zero_coverage_blocked_report_replays_as_cash_only(self) -> None:
        from research_system.strategies.contracts import blank_strategy_document

        result = run_holdings_portfolio(
            instruments={},
            bars_by_symbol={},
            events_by_symbol={},
            stock_signals={},
            fund_signals=[
                {
                    "id": "fund-blocked",
                    "state": "BLOCKED",
                    "effective_from": "2026-08-03",
                    "expires_after": "2026-08-05",
                    "source_report_set_id": "report-partial",
                }
            ],
            report_schedule=[
                {
                    "id": "report-partial",
                    "effective_date": "2026-08-03",
                    "revision": 1,
                    "target_weights": {},
                }
            ],
            adapter=ExchangeExecutionAdapter.stock(blank_strategy_document()),
            initial_cash=100_000,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 5),
            max_single_stock_pct=20,
            drift_pct_points=2,
            fallback_trading_days=[
                date(2026, 8, 3),
                date(2026, 8, 4),
                date(2026, 8, 5),
            ],
        )

        self.assertEqual(result["metrics"]["trade_count"], 0)
        self.assertEqual(result["metrics"]["final_equity"], 100_000)
        self.assertEqual(
            [row["date"] for row in result["equity_curve"]],
            ["2026-08-03", "2026-08-04", "2026-08-05"],
        )
        self.assertTrue(all(row["cash"] == 100_000 for row in result["equity_curve"]))

    def test_missing_open_status_is_audited_as_degraded_estimate_and_confirmation_rules_are_applied(self) -> None:
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
        assumed_result = run_otc_single_asset(
            instrument=instrument,
            bars=bars,
            events=events,
            adapter=adapter,
            initial_cash=100_000,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 10),
        )
        self.assertEqual(assumed_result["trades"][0]["order_date"], "2026-08-04")
        self.assertEqual(
            assumed_result["execution_adapter"]["open_status_assumptions"],
            [
                {
                    "date": "2026-08-04",
                    "side": "buy",
                    "assumption": "assumed_open",
                    "reason": "historical_purchase_status_unavailable",
                }
            ],
        )

        strict_adapter = OTCExecutionAdapter(
            subscription_fee_rate=0.0015,
            redemption_fee_rate=0.005,
            subscription_confirmation_days=1,
            redemption_confirmation_days=2,
            require_open_status=True,
            missing_open_status_policy="block",
        )
        with self.assertRaises(StrategyDomainError):
            run_otc_single_asset(
                instrument=instrument,
                bars=bars,
                events=events,
                adapter=strict_adapter,
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

    def test_missing_open_status_does_not_block_when_no_order_is_attempted(self) -> None:
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
        result = run_otc_single_asset(
            instrument=instrument,
            bars=bars,
            events=[],
            adapter=OTCExecutionAdapter(
                subscription_fee_rate=0.0015,
                redemption_fee_rate=0.005,
                subscription_confirmation_days=1,
                redemption_confirmation_days=2,
                require_open_status=True,
            ),
            initial_cash=100_000,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 10),
        )
        self.assertEqual(result["metrics"]["trade_count"], 0)
        self.assertEqual(result["metrics"]["final_equity"], 100_000)
        self.assertEqual(result["execution_adapter"]["open_status_validation"], "order_date_and_side")

    def test_otc_staged_entry_respects_position_cap_tranche_limit_and_interval(self) -> None:
        instrument = InstrumentResolver.resolve_fund(
            {"fund_type": "偏股混合型", "fund_name": "分批建仓基金"},
            code="110011",
        )
        bars = [
            {
                "date": (date(2026, 8, 3) + timedelta(days=index)).isoformat(),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "purchase_open": True,
                "redemption_open": True,
            }
            for index in range(10)
        ]
        result = run_otc_single_asset(
            instrument=instrument,
            bars=bars,
            events=[
                {
                    "date": "2026-08-03",
                    "side": "buy",
                    "signal_id": "signal-staged",
                    "source_report_set_id": "report-staged",
                    "technical_reason": "ma_cross",
                }
            ],
            adapter=OTCExecutionAdapter(
                subscription_fee_rate=0.0,
                redemption_fee_rate=0.0,
                subscription_confirmation_days=1,
                redemption_confirmation_days=2,
                require_open_status=True,
                max_position_pct=60.0,
                entry_tranche_pct=25.0,
                max_entry_tranches=4,
                minimum_entry_interval_days=2,
                model_id="otc_fund_staged_entry_v1",
            ),
            initial_cash=100_000,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 12),
        )

        self.assertEqual([row["gross_value"] for row in result["trades"]], [25_000, 25_000, 10_000])
        self.assertEqual(
            [row["order_date"] for row in result["trades"]],
            ["2026-08-04", "2026-08-06", "2026-08-08"],
        )
        self.assertEqual(
            [row["entry_tranche_index"] for row in result["trades"]],
            [1, 2, 3],
        )
        self.assertLessEqual(
            max(float(row["position_weight_pct"]) for row in result["equity_curve"]),
            60.0,
        )
        self.assertEqual(result["metrics"]["turnover_pct"], 60.0)
        self.assertEqual(result["execution_adapter"]["entry_orders_submitted"], 3)
        self.assertEqual(result["execution_adapter"]["max_position_pct"], 60.0)

    def test_otc_staged_entry_rechecks_the_current_fund_gate_for_every_tranche(self) -> None:
        instrument = InstrumentResolver.resolve_fund(
            {"fund_type": "偏股混合型", "fund_name": "门禁复核基金"},
            code="110011",
        )
        bars = [
            {
                "date": (date(2026, 8, 3) + timedelta(days=index)).isoformat(),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "purchase_open": True,
                "redemption_open": True,
            }
            for index in range(10)
        ]
        result = run_otc_single_asset(
            instrument=instrument,
            bars=bars,
            events=[
                {
                    "date": "2026-08-03",
                    "side": "buy",
                    "signal_id": "allow-1",
                    "source_report_set_id": "report-1",
                    "technical_reason": "ma_cross",
                }
            ],
            fund_signals=[
                {
                    "id": "allow-1",
                    "state": "ALLOW_BUY",
                    "effective_from": "2026-08-03",
                    "source_report_set_id": "report-1",
                },
                {
                    "id": "watch-1",
                    "state": "WATCH",
                    "effective_from": "2026-08-05",
                    "source_report_set_id": "report-2",
                },
                {
                    "id": "allow-2",
                    "state": "ALLOW_BUY",
                    "effective_from": "2026-08-07",
                    "source_report_set_id": "report-3",
                },
            ],
            adapter=OTCExecutionAdapter(
                subscription_fee_rate=0.0,
                redemption_fee_rate=0.0,
                subscription_confirmation_days=1,
                redemption_confirmation_days=2,
                require_open_status=True,
                max_position_pct=60.0,
                entry_tranche_pct=20.0,
                max_entry_tranches=3,
                minimum_entry_interval_days=1,
            ),
            initial_cash=100_000,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 12),
        )

        self.assertEqual(
            [row["order_date"] for row in result["trades"]],
            ["2026-08-04", "2026-08-07", "2026-08-08"],
        )
        self.assertEqual(
            [row["signal_id"] for row in result["trades"]],
            ["allow-1", "allow-2", "allow-2"],
        )


if __name__ == "__main__":
    unittest.main()
