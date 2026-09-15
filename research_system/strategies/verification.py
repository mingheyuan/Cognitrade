from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from research_system.strategies.backtesting.result_store import (
    ContentAddressedResultStore,
)
from research_system.strategies.contracts import (
    GovernanceState,
    parse_strategy_document,
)
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict, StrategyDomainError
from research_system.strategies.models import (
    BacktestRun,
    BacktestSignalLink,
    DailyReportSet,
    Strategy,
    StrategyVerification,
    StrategyVersion,
    TradeSignal,
)
from research_system.strategies.repository import StrategyRepository
from research_system.strategies.service import OwnerScope

DEFAULT_VERIFICATION_THRESHOLDS: dict[str, float | int | bool] = {
    "minimum_sample_days": 20,
    "minimum_signal_count": 1,
    "maximum_drawdown_pct": 40.0,
    "minimum_annualized_return_pct": -100.0,
    "minimum_sharpe": -10.0,
    "minimum_excess_return_pct": -100.0,
    "maximum_turnover_pct": 10_000.0,
    "maximum_fee_impact_pct": 10.0,
    "require_deterministic_replay": True,
}


class StrategyVerificationService:
    def __init__(self, database: StrategyDatabase) -> None:
        self.database = database
        self.repository = StrategyRepository()

    def create(
        self,
        scope: OwnerScope,
        *,
        strategy_version_id: str,
        backtest_run_id: str,
        thresholds: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = self._thresholds(thresholds)
        with self.database.session() as session:
            strategy, version = self._version(session, scope, strategy_version_id)
            backtest = session.scalar(
                select(BacktestRun).where(
                    BacktestRun.id == backtest_run_id,
                    BacktestRun.tenant_id == scope.tenant_id,
                    BacktestRun.user_id == scope.user_id,
                )
            )
            if backtest is None:
                raise StrategyDomainError("strategy_backtest_not_found", "回测不存在", status_code=404)
            if backtest.strategy_version_id != version.id:
                raise StrategyConflict("verification_backtest_version_mismatch", "回测未绑定该策略版本")
            checks = self._checks(session, backtest, snapshot, version=version)
            machine_passed = all(
                bool(value.get("passed"))
                for value in checks.values()
                if bool(value.get("required", True))
            )
            row = StrategyVerification(
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                strategy_version_id=version.id,
                backtest_run_id=backtest.id,
                status="machine_passed" if machine_passed else "failed",
                checks_json=checks,
                thresholds_json=snapshot,
                machine_passed=machine_passed,
            )
            session.add(row)
            session.flush()
            self.repository.append_event(
                session,
                strategy_id=strategy.id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="verification_created",
                payload={
                    "verification_id": row.id,
                    "backtest_run_id": backtest.id,
                    "machine_passed": machine_passed,
                },
            )
            return self._payload(row)

    def get(self, scope: OwnerScope, verification_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = self._require(session, scope, verification_id)
            return self._payload(row)

    def list(
        self,
        scope: OwnerScope,
        *,
        strategy_version_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            query = select(StrategyVerification).where(
                StrategyVerification.tenant_id == scope.tenant_id,
                StrategyVerification.user_id == scope.user_id,
            )
            if strategy_version_id:
                query = query.where(StrategyVerification.strategy_version_id == strategy_version_id)
            rows = list(session.scalars(query.order_by(StrategyVerification.created_at.desc())))
            return {"verifications": [self._payload(row) for row in rows]}

    def confirm(self, scope: OwnerScope, verification_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = self._require(session, scope, verification_id)
            if not row.machine_passed:
                raise StrategyConflict("verification_checks_failed", "机器检查未通过，不能人工确认")
            if row.confirmed_at is not None:
                return self._payload(row)
            version = session.get(StrategyVersion, row.strategy_version_id)
            if version is None:
                raise StrategyDomainError("strategy_version_not_found", "策略版本不存在", status_code=409)
            strategy = session.get(Strategy, version.strategy_id)
            if strategy is None or strategy.tenant_id != scope.tenant_id or strategy.user_id != scope.user_id:
                raise StrategyDomainError("strategy_version_not_found", "策略版本不存在", status_code=404)
            row.status = GovernanceState.VERIFIED.value
            row.confirmed_by = scope.actor_id
            row.confirmed_at = datetime.now(UTC)
            if version.governance_state != GovernanceState.PROMOTED.value:
                version.governance_state = GovernanceState.VERIFIED.value
            self.repository.append_event(
                session,
                strategy_id=strategy.id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="verified",
                payload={"verification_id": row.id, "version_id": version.id},
            )
            return self._payload(row)

    def _checks(
        self,
        session,
        backtest: BacktestRun,
        thresholds: dict[str, Any],
        *,
        version: StrategyVersion,
    ) -> dict[str, dict[str, Any]]:
        completed = backtest.status == "completed" and bool(backtest.result_object_uri)
        result: dict[str, Any] = {}
        if completed:
            try:
                result = ContentAddressedResultStore.get_json(str(backtest.result_object_uri))
            except (OSError, ValueError):
                completed = False
        metrics = dict(backtest.metrics_json or result.get("metrics") or {})
        trades = [dict(row) for row in result.get("trades") or [] if isinstance(row, dict)]
        curve = [dict(row) for row in result.get("equity_curve") or [] if isinstance(row, dict)]
        links = list(
            session.scalars(
                select(BacktestSignalLink).where(BacktestSignalLink.backtest_run_id == backtest.id)
            )
        )
        linked_signal_ids = {row.signal_id for row in links}
        trade_signal_ids = {
            str(signal_id)
            for trade in trades
            for signal_id in trade.get("signal_ids") or ([trade.get("signal_id")] if trade.get("signal_id") else [])
        }
        document = parse_strategy_document(version.document_json)
        lineage_complete = bool(trades) and trade_signal_ids.issubset(linked_signal_ids)
        trade_lineage_complete = bool(trades)
        for trade in trades:
            lineages = [dict(row) for row in trade.get("signal_lineage") or [] if isinstance(row, dict)]
            lineaged_ids = {str(row.get("signal_id") or "") for row in lineages}
            expected_ids = {
                str(value)
                for value in trade.get("signal_ids") or ([trade.get("signal_id")] if trade.get("signal_id") else [])
                if value
            }
            source_report_ids = {str(value) for value in trade.get("source_report_set_ids") or [] if value}
            if (
                trade.get("strategy_version_id") != backtest.strategy_version_id
                or trade.get("strategy_content_hash") != backtest.strategy_content_hash
                or not expected_ids
                or lineaged_ids != expected_ids
                or any(str(row.get("source_report_set_id") or "") not in source_report_ids for row in lineages)
            ):
                trade_lineage_complete = False
                break
        no_lookahead = True
        signal_versions_match = True
        report_lineage_complete = True
        coverage_values: list[float] = []
        for link in links:
            signal = session.get(TradeSignal, link.signal_id)
            if signal is None:
                no_lookahead = False
                signal_versions_match = False
                continue
            report = session.get(DailyReportSet, signal.source_report_set_id)
            if signal.effective_from > link.trading_date or report is None or report.as_of_date >= signal.effective_from:
                no_lookahead = False
            if (
                report is None
                or not report.source_hash
                or report.status not in {"final", "final_partial"}
            ):
                report_lineage_complete = False
            if signal.strategy_version_id != backtest.strategy_version_id:
                signal_versions_match = False
            coverage_values.append(float(signal.coverage))
        replay = session.scalar(
            select(BacktestRun).where(
                BacktestRun.id != backtest.id,
                BacktestRun.tenant_id == backtest.tenant_id,
                BacktestRun.user_id == backtest.user_id,
                BacktestRun.input_snapshot_hash == backtest.input_snapshot_hash,
                BacktestRun.status == "completed",
                BacktestRun.result_content_hash == backtest.result_content_hash,
            )
        )
        deterministic_required = bool(thresholds["require_deterministic_replay"])
        deterministic = replay is not None
        benchmark_return = _benchmark_return(result.get("benchmarks") or {})
        total_return = float(metrics.get("total_return_pct") or 0.0)
        excess_return = total_return - benchmark_return if benchmark_return is not None else None
        fee_impact = (
            float(metrics.get("total_fees") or 0.0) / max(float(metrics.get("initial_cash") or backtest.initial_cash), 1.0) * 100
        )
        fees_complete = all(
            isinstance(trade.get("fees"), dict)
            and all(
                key in trade["fees"]
                for key in (
                    "commission",
                    "transfer_fee",
                    "stamp_duty",
                    "subscription_fee",
                    "redemption_fee",
                    "total",
                )
            )
            for trade in trades
        )
        checks = {
            "completed_backtest": _check(completed, backtest.status),
            "no_lookahead": _check(no_lookahead and bool(links), {"link_count": len(links)}),
            "lineage_complete": _check(lineage_complete and trade_lineage_complete and signal_versions_match, {"trade_signal_count": len(trade_signal_ids), "link_count": len(linked_signal_ids), "trade_fields_complete": trade_lineage_complete}),
            "report_lineage_complete": _check(report_lineage_complete and bool(links), {"link_count": len(links)}),
            "deterministic_replay": _check(deterministic, {"matching_backtest_id": replay.id if replay else None}, required=deterministic_required),
            "sample_days": _check(len(curve) >= int(thresholds["minimum_sample_days"]), len(curve)),
            "signal_count": _check(len(linked_signal_ids) >= int(thresholds["minimum_signal_count"]), len(linked_signal_ids)),
            "fees_complete": _check(fees_complete and bool(trades), {"trade_count": len(trades), "fee_impact_pct": fee_impact}),
            "maximum_drawdown": _check(abs(float(metrics.get("max_drawdown_pct") or 0.0)) <= float(thresholds["maximum_drawdown_pct"]), metrics.get("max_drawdown_pct")),
            "annualized_return": _check(float(metrics.get("annualized_return_pct") or 0.0) >= float(thresholds["minimum_annualized_return_pct"]), metrics.get("annualized_return_pct")),
            "sharpe": _check(float(metrics.get("sharpe") or 0.0) >= float(thresholds["minimum_sharpe"]), metrics.get("sharpe")),
            "excess_return": _check(excess_return is not None and excess_return >= float(thresholds["minimum_excess_return_pct"]), excess_return),
            "turnover": _check(float(metrics.get("turnover_pct") or 0.0) <= float(thresholds["maximum_turnover_pct"]), metrics.get("turnover_pct")),
            "fee_impact": _check(fee_impact <= float(thresholds["maximum_fee_impact_pct"]), fee_impact),
            "coverage": _check(
                bool(coverage_values)
                and min(coverage_values) >= float(document.quality_gates.minimum_coverage),
                {
                    "minimum_observed": min(coverage_values) if coverage_values else None,
                    "required": float(document.quality_gates.minimum_coverage),
                },
            ),
        }
        return checks

    @staticmethod
    def _thresholds(overrides: dict[str, Any] | None) -> dict[str, Any]:
        unknown = set(overrides or {}) - set(DEFAULT_VERIFICATION_THRESHOLDS)
        if unknown:
            raise StrategyDomainError(
                "unknown_verification_threshold",
                f"包含未知验证门槛：{', '.join(sorted(unknown))}",
            )
        values = {**DEFAULT_VERIFICATION_THRESHOLDS, **dict(overrides or {})}
        for key, value in values.items():
            if key == "require_deterministic_replay":
                if not isinstance(value, bool):
                    raise StrategyDomainError("invalid_verification_threshold", f"{key} 必须是布尔值")
                continue
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise StrategyDomainError("invalid_verification_threshold", f"{key} 必须是数字") from exc
            if not math.isfinite(number):
                raise StrategyDomainError("invalid_verification_threshold", f"{key} 必须是有限数字")
            if key.startswith("minimum_sample") or key.startswith("minimum_signal"):
                if number < 1 or not number.is_integer():
                    raise StrategyDomainError("invalid_verification_threshold", f"{key} 必须是大于 0 的整数")
                values[key] = int(number)
            else:
                if key == "maximum_drawdown_pct" and not 0 <= number <= 100:
                    raise StrategyDomainError("invalid_verification_threshold", f"{key} 必须在 0 到 100 之间")
                if key == "maximum_turnover_pct" and number < 0:
                    raise StrategyDomainError("invalid_verification_threshold", f"{key} 不能小于 0")
                if key == "maximum_fee_impact_pct" and not 0 <= number <= 100:
                    raise StrategyDomainError("invalid_verification_threshold", f"{key} 必须在 0 到 100 之间")
                values[key] = number
        return values

    @staticmethod
    def _payload(row: StrategyVerification) -> dict[str, Any]:
        return {
            "id": row.id,
            "strategy_version_id": row.strategy_version_id,
            "backtest_run_id": row.backtest_run_id,
            "status": row.status,
            "checks": row.checks_json,
            "thresholds": row.thresholds_json,
            "machine_passed": row.machine_passed,
            "confirmed_by": row.confirmed_by,
            "confirmed_at": row.confirmed_at,
            "created_at": row.created_at,
        }

    @staticmethod
    def _require(session, scope: OwnerScope, verification_id: str) -> StrategyVerification:
        row = session.scalar(
            select(StrategyVerification).where(
                StrategyVerification.id == verification_id,
                StrategyVerification.tenant_id == scope.tenant_id,
                StrategyVerification.user_id == scope.user_id,
            )
        )
        if row is None:
            raise StrategyDomainError("strategy_verification_not_found", "验证记录不存在", status_code=404)
        return row

    @staticmethod
    def _version(session, scope: OwnerScope, version_id: str) -> tuple[Strategy, StrategyVersion]:
        version = session.get(StrategyVersion, version_id)
        if version is None:
            raise StrategyDomainError("strategy_version_not_found", "策略版本不存在", status_code=404)
        strategy = session.scalar(
            select(Strategy).where(
                Strategy.id == version.strategy_id,
                Strategy.tenant_id == scope.tenant_id,
                Strategy.user_id == scope.user_id,
                Strategy.is_system_template.is_(False),
            )
        )
        if strategy is None:
            raise StrategyDomainError("strategy_version_not_found", "策略版本不存在", status_code=404)
        return strategy, version


def _check(passed: bool, actual: Any, *, required: bool = True) -> dict[str, Any]:
    return {"passed": bool(passed), "required": required, "actual": actual}


def _benchmark_return(benchmarks: dict[str, Any]) -> float | None:
    for key in ("fund_buy_and_hold", "initial_disclosed_weights_static", "hs300"):
        row = benchmarks.get(key)
        if isinstance(row, dict) and row.get("return_pct") is not None:
            return float(row["return_pct"])
    return None


__all__ = ["DEFAULT_VERIFICATION_THRESHOLDS", "StrategyVerificationService"]
