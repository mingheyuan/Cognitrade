from __future__ import annotations

import unittest
import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.strategies.contracts import BacktestMode
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict, StrategyDomainError
from research_system.strategies.models import (
    BacktestRun,
    DailyReportNode,
    DailyReportSet,
    PaperStrategyBinding,
    StrategyVerification,
    StrategyVersion,
    TradeSignal,
)
from research_system.strategies.paper_execution import StrategyPaperExecutionService
from research_system.strategies.service import OwnerScope, StrategyService
from research_system.strategies.verification import StrategyVerificationService


class _MarketData:
    def get_quote(self, symbol: str, *, refresh: bool) -> dict:
        return {
            "symbol": symbol,
            "name": "测试股票",
            "latest_price": 10.0,
            "refresh": refresh,
        }


class _PaperTrading:
    def __init__(self) -> None:
        self.market_data = _MarketData()
        self.calls: list[dict] = []

    def get_account(self, *, tenant_id: str, user_id: str) -> dict:
        return {"account_id": "paper-account", "tenant_id": tenant_id, "user_id": user_id}

    def get_portfolio(self, *, tenant_id: str, user_id: str) -> dict:
        return {
            "account": self.get_account(tenant_id=tenant_id, user_id=user_id),
            "summary": {"equity": 1_000_000.0},
            "positions": [],
        }

    def place_order(self, **payload) -> dict:
        self.calls.append(dict(payload))
        return {
            "order_id": f"paper-order-{len(self.calls)}",
            "status": "filled",
            "symbol": payload["symbol"],
            "side": payload["side"],
            "quantity": payload["quantity"],
            "order_type": payload["order_type"],
            "limit_price": payload["limit_price"],
        }


class StrategyPaperExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.database = StrategyDatabase("sqlite:///:memory:")
        self.strategies = StrategyService(
            self.database,
            workspace_root=Path(self.temporary.name),
            auto_bootstrap=True,
        )
        self.owner = OwnerScope("tenant-a", "user-a", "actor-a")
        created = self.strategies.create_strategy(self.owner, name="模拟执行策略")
        self.strategy_id = created["id"]
        self.version = self.strategies.publish(self.owner, self.strategy_id)
        self.assignment = self.strategies.create_assignment(
            self.owner,
            strategy_id=self.strategy_id,
            fund_code="510300",
            fund_name="测试ETF",
            mode=BacktestMode.HOLDINGS_PORTFOLIO,
        )
        self.report_id, self.fund_signal_id, self.stock_signal_id = self._seed_report_and_signals()
        self.paper = _PaperTrading()
        self.execution = StrategyPaperExecutionService(self.database, paper_trading=self.paper)

    def tearDown(self) -> None:
        self.strategies.close()
        self.temporary.cleanup()

    def _seed_report_and_signals(self) -> tuple[str, str, str]:
        with self.database.session() as session:
            report = DailyReportSet(
                tenant_id=self.owner.tenant_id,
                user_id=self.owner.user_id,
                assignment_id=self.assignment["id"],
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
                    node_type="holding_uzi",
                    subject_code="600000",
                    subject_name="测试股票",
                    rank=1,
                    disclosed_weight_pct=10.0,
                    effective_weight_pct=10.0,
                    canonical_key="uzi:600000:2026-08-03",
                    status="completed",
                    confidence=1.0,
                    data_gaps_json=[],
                    source_json={"uzi_overall_score": 90.0, "supported": True},
                )
            )
            fund_signal = TradeSignal(
                tenant_id=self.owner.tenant_id,
                user_id=self.owner.user_id,
                kind="fund",
                subject_code="510300",
                strategy_version_id=self.version["id"],
                assignment_id=self.assignment["id"],
                as_of_date=date(2026, 8, 3),
                revision=1,
                state="ALLOW_BUY",
                score_lower_bound=80.0,
                score_upper_bound=90.0,
                confidence=0.9,
                coverage=1.0,
                reasons_json=[],
                source_report_set_id=report.id,
                effective_from=date(2026, 8, 4),
                expires_after=date(2026, 8, 10),
                content_hash="c" * 64,
            )
            stock_signal = TradeSignal(
                tenant_id=self.owner.tenant_id,
                user_id=self.owner.user_id,
                kind="holding_stock",
                subject_code="600000",
                strategy_version_id=self.version["id"],
                assignment_id=self.assignment["id"],
                as_of_date=date(2026, 8, 3),
                revision=1,
                state="ALLOW_BUY",
                score_lower_bound=85.0,
                score_upper_bound=95.0,
                confidence=0.9,
                coverage=1.0,
                reasons_json=[],
                source_report_set_id=report.id,
                effective_from=date(2026, 8, 4),
                expires_after=date(2026, 8, 10),
                content_hash="d" * 64,
            )
            session.add_all([fund_signal, stock_signal])
            session.flush()
            return report.id, fund_signal.id, stock_signal.id

    def _verify(self, version_id: str | None = None) -> None:
        selected_version = version_id or self.version["id"]
        with self.database.session() as session:
            version = session.get(StrategyVersion, selected_version)
            version.governance_state = "verified"
            backtest = BacktestRun(
                tenant_id=self.owner.tenant_id,
                user_id=self.owner.user_id,
                strategy_id=self.strategy_id,
                strategy_version_id=selected_version,
                strategy_content_hash=version.content_hash,
                fund_code="510300",
                fund_name="测试ETF",
                mode=BacktestMode.HOLDINGS_PORTFOLIO.value,
                start_date=date(2026, 8, 4),
                end_date=date(2026, 8, 10),
                initial_cash=1_000_000,
                benchmark_json={},
                request_json={},
                input_snapshot_hash=hashlib.sha256(selected_version.encode("utf-8")).hexdigest(),
                engine_version="fixture-v1",
                status="completed",
                metrics_json={},
                completed_at=datetime.now(UTC),
            )
            session.add(backtest)
            session.flush()
            session.add(
                StrategyVerification(
                    tenant_id=self.owner.tenant_id,
                    user_id=self.owner.user_id,
                    strategy_version_id=selected_version,
                    backtest_run_id=backtest.id,
                    status="verified",
                    checks_json={"fixture": {"passed": True}},
                    thresholds_json={},
                    machine_passed=True,
                    confirmed_by=self.owner.actor_id,
                    confirmed_at=datetime.now(UTC),
                )
            )

    def _promote(self) -> dict:
        self._verify()
        return self.strategies.promote(
            self.owner,
            strategy_id=self.strategy_id,
            version_id=self.version["id"],
            account_id="paper-account",
            fund_code="510300",
            mode=BacktestMode.HOLDINGS_PORTFOLIO,
        )

    def test_published_or_merely_verified_version_cannot_place_strategy_order(self) -> None:
        with self.database.session() as session:
            binding = PaperStrategyBinding(
                tenant_id=self.owner.tenant_id,
                user_id=self.owner.user_id,
                account_id="paper-account",
                fund_code="510300",
                mode=BacktestMode.HOLDINGS_PORTFOLIO.value,
                strategy_version_id=self.version["id"],
                promoted_by=self.owner.actor_id,
            )
            session.add(binding)
            session.flush()
            binding_id = binding.id

        with self.assertRaises(StrategyConflict) as published:
            self.execution.submit(
                self.owner,
                binding_id=binding_id,
                signal_id=self.stock_signal_id,
                side="buy",
                quantity=100,
                technical_reason="ma5_cross_above_ma20",
                client_order_id="published-order",
                trading_date=date(2026, 8, 4),
            )
        self.assertEqual(published.exception.code, "strategy_version_not_promoted")

        self._verify()
        with self.assertRaises(StrategyConflict) as verified:
            self.execution.submit(
                self.owner,
                binding_id=binding_id,
                signal_id=self.stock_signal_id,
                side="buy",
                quantity=100,
                technical_reason="ma5_cross_above_ma20",
                client_order_id="verified-order",
                trading_date=date(2026, 8, 4),
            )
        self.assertEqual(verified.exception.code, "strategy_version_not_promoted")
        self.assertEqual(self.paper.calls, [])

    def test_promoted_order_is_lineaged_idempotent_and_owner_scoped(self) -> None:
        binding = self._promote()
        request = {
            "binding_id": binding["binding_id"],
            "signal_id": self.stock_signal_id,
            "side": "buy",
            "quantity": 100,
            "technical_reason": "ma5_cross_above_ma20",
            "client_order_id": "strategy-order-1",
            "trading_date": date(2026, 8, 4),
        }
        created = self.execution.submit(self.owner, **request)
        replay = self.execution.submit(self.owner, **request)
        self.assertFalse(created["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(created["signal_id"], self.stock_signal_id)
        self.assertEqual(created["source_report_set_id"], self.report_id)
        self.assertEqual(created["strategy_version_id"], self.version["id"])
        self.assertEqual(len(self.paper.calls), 1)
        self.assertEqual(
            self.paper.calls[0]["execution_profile"]["model_id"],
            f"strategy-stock:{self.version['id']}",
        )
        self.assertEqual(self.paper.calls[0]["execution_profile"]["board_lot"], 100)

        with self.assertRaises(StrategyConflict) as reused:
            self.execution.submit(self.owner, **{**request, "quantity": 200})
        self.assertEqual(reused.exception.code, "idempotency_key_reused")

        stranger = OwnerScope("tenant-a", "user-b", "actor-b")
        self.assertEqual(self.execution.list(stranger)["orders"], [])
        with self.assertRaises(StrategyDomainError) as hidden:
            self.execution.submit(stranger, **{**request, "client_order_id": "stranger-order"})
        self.assertEqual(hidden.exception.code, "paper_strategy_binding_not_found")

    def test_blocked_signal_denies_buy_but_allows_independent_risk_exit(self) -> None:
        binding = self._promote()
        with self.database.session() as session:
            session.get(TradeSignal, self.stock_signal_id).state = "BLOCKED"

        with self.assertRaises(StrategyConflict) as blocked:
            self.execution.submit(
                self.owner,
                binding_id=binding["binding_id"],
                signal_id=self.stock_signal_id,
                side="buy",
                quantity=100,
                technical_reason="ma5_cross_above_ma20",
                client_order_id="blocked-buy",
                trading_date=date(2026, 8, 4),
            )
        self.assertEqual(blocked.exception.code, "paper_buy_not_allowed")

        sold = self.execution.submit(
            self.owner,
            binding_id=binding["binding_id"],
            signal_id=self.stock_signal_id,
            side="sell",
            quantity=100,
            technical_reason="risk_exit",
            client_order_id="risk-sell",
            trading_date=date(2026, 8, 4),
        )
        self.assertEqual(sold["side"], "sell")

    def test_only_latest_signal_and_frozen_technical_rule_can_enter(self) -> None:
        binding = self._promote()
        with self.assertRaises(StrategyConflict) as hidden_override:
            self.execution.submit(
                self.owner,
                binding_id=binding["binding_id"],
                signal_id=self.stock_signal_id,
                side="buy",
                quantity=100,
                technical_reason="ma10_cross_above_ma30",
                client_order_id="wrong-ma-periods",
                trading_date=date(2026, 8, 4),
            )
        self.assertEqual(hidden_override.exception.code, "paper_buy_reason_invalid")

        with self.database.session() as session:
            replacement = TradeSignal(
                tenant_id=self.owner.tenant_id,
                user_id=self.owner.user_id,
                kind="holding_stock",
                subject_code="600000",
                strategy_version_id=self.version["id"],
                assignment_id=self.assignment["id"],
                as_of_date=date(2026, 8, 3),
                revision=2,
                state="AVOID",
                score_lower_bound=20.0,
                score_upper_bound=30.0,
                confidence=0.9,
                coverage=1.0,
                reasons_json=["late_revision"],
                source_report_set_id=self.report_id,
                effective_from=date(2026, 8, 5),
                expires_after=date(2026, 8, 10),
                content_hash="e" * 64,
            )
            session.add(replacement)

        with self.assertRaises(StrategyConflict) as superseded:
            self.execution.submit(
                self.owner,
                binding_id=binding["binding_id"],
                signal_id=self.stock_signal_id,
                side="buy",
                quantity=100,
                technical_reason="ma5_cross_above_ma20",
                client_order_id="superseded-signal",
                trading_date=date(2026, 8, 5),
            )
        self.assertEqual(superseded.exception.code, "paper_signal_superseded")

    def test_paused_assignment_blocks_new_buy(self) -> None:
        binding = self._promote()
        self.strategies.pause_assignment(self.owner, self.assignment["id"])
        with self.assertRaises(StrategyConflict) as paused:
            self.execution.submit(
                self.owner,
                binding_id=binding["binding_id"],
                signal_id=self.stock_signal_id,
                side="buy",
                quantity=100,
                technical_reason="ma5_cross_above_ma20",
                client_order_id="paused-buy",
                trading_date=date(2026, 8, 4),
            )
        self.assertEqual(paused.exception.code, "paper_strategy_paused")

    def test_new_publish_does_not_replace_promoted_binding_and_promotion_is_idempotent(self) -> None:
        promoted = self._promote()
        replay = self.strategies.promote(
            self.owner,
            strategy_id=self.strategy_id,
            version_id=self.version["id"],
            account_id="paper-account",
            fund_code="510300",
            mode=BacktestMode.HOLDINGS_PORTFOLIO,
        )
        self.assertEqual(replay["binding_id"], promoted["binding_id"])
        self.assertTrue(replay["idempotent_replay"])

        detail = self.strategies.get_strategy(self.owner, self.strategy_id)
        document = self.strategies.get_version(
            self.owner,
            self.strategy_id,
            detail["latest_version"]["id"],
        )["document"]
        document["signal_thresholds"]["buy"] = 72.0
        self.strategies.update_draft(
            self.owner,
            self.strategy_id,
            expected_revision=0,
            document=document,
        )
        version_two = self.strategies.publish(self.owner, self.strategy_id)
        bindings = self.strategies.list_paper_bindings(self.owner)["bindings"]
        self.assertEqual(bindings[0]["strategy_version_id"], self.version["id"])
        self.assertEqual(version_two["governance_state"], "published")

    def test_promotion_requires_verification_for_the_exact_fund_and_mode(self) -> None:
        self._verify()
        self.strategies.create_assignment(
            self.owner,
            strategy_id=self.strategy_id,
            fund_code="510300",
            fund_name="测试ETF",
            mode=BacktestMode.FUND_ONLY,
        )
        with self.assertRaises(StrategyConflict) as mismatch:
            self.strategies.promote(
                self.owner,
                strategy_id=self.strategy_id,
                version_id=self.version["id"],
                account_id="paper-account",
                fund_code="510300",
                mode=BacktestMode.FUND_ONLY,
            )
        self.assertEqual(mismatch.exception.code, "strategy_version_not_verified")

    def test_confirming_another_verification_does_not_demote_promoted_version(self) -> None:
        self._promote()
        with self.database.session() as session:
            existing = session.query(StrategyVerification).first()
            another = StrategyVerification(
                tenant_id=self.owner.tenant_id,
                user_id=self.owner.user_id,
                strategy_version_id=self.version["id"],
                backtest_run_id=existing.backtest_run_id,
                status="machine_passed",
                checks_json={"fixture": {"passed": True}},
                thresholds_json={},
                machine_passed=True,
            )
            session.add(another)
            session.flush()
            verification_id = another.id
        StrategyVerificationService(self.database).confirm(self.owner, verification_id)
        with self.database.session() as session:
            self.assertEqual(
                session.get(StrategyVersion, self.version["id"]).governance_state,
                "promoted",
            )


if __name__ == "__main__":
    unittest.main()
