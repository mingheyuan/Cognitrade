from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from research_system.strategies.contracts import (
    BacktestMode,
    GovernanceState,
    SignalState,
    StrategyDocumentV1,
    StrategyLifecycle,
    is_report_conditions,
    parse_strategy_document,
)
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict, StrategyDomainError
from research_system.strategies.models import (
    DailyReportNode,
    DailyReportSet,
    PaperStrategyBinding,
    Strategy,
    StrategyAssignment,
    StrategyPaperOrder,
    StrategyVerification,
    StrategyVersion,
    TradeSignal,
)
from research_system.strategies.repository import StrategyRepository
from research_system.strategies.service import OwnerScope
from research_system.strategies.trading_calendar import is_trading_day


class StrategyPaperExecutionService:
    """Authorize strategy-originated paper orders against an exact Promoted binding."""

    def __init__(self, database: StrategyDatabase, *, paper_trading: Any) -> None:
        self.database = database
        self.paper_trading = paper_trading
        self.repository = StrategyRepository()

    def submit(
        self,
        scope: OwnerScope,
        *,
        binding_id: str,
        signal_id: str,
        side: str,
        quantity: int,
        technical_reason: str,
        client_order_id: str,
        trading_date: date | None = None,
        order_type: str = "market",
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        day = trading_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        normalized_side = str(side or "").strip().lower()
        normalized_reason = str(technical_reason or "").strip()
        normalized_client_order_id = str(client_order_id or "").strip()
        if normalized_side not in {"buy", "sell"}:
            raise StrategyDomainError("invalid_order_side", "策略模拟委托方向只能是 buy 或 sell")
        try:
            normalized_quantity = int(quantity)
        except (TypeError, ValueError) as exc:
            raise StrategyDomainError("invalid_quantity", "策略模拟委托数量必须是正整数") from exc
        if isinstance(quantity, bool) or normalized_quantity <= 0:
            raise StrategyDomainError("invalid_quantity", "策略模拟委托数量必须是正整数")
        if not normalized_reason or len(normalized_reason) > 128:
            raise StrategyDomainError("technical_reason_required", "策略模拟委托必须提供可审计技术原因")
        if not normalized_client_order_id or len(normalized_client_order_id) > 128:
            raise StrategyDomainError("invalid_client_order_id", "client_order_id 不能为空且不能超过 128 字符")
        if not is_trading_day(day):
            raise StrategyConflict("paper_order_non_trading_day", "策略模拟委托日期必须是交易日")
        request_snapshot = {
            "binding_id": binding_id,
            "signal_id": signal_id,
            "side": normalized_side,
            "quantity": normalized_quantity,
            "technical_reason": normalized_reason,
            "trading_date": day.isoformat(),
            "order_type": str(order_type or "").strip().lower(),
            "limit_price": None if limit_price is None else float(limit_price),
        }

        with self.database.session() as session:
            existing = session.scalar(
                select(StrategyPaperOrder).where(
                    StrategyPaperOrder.tenant_id == scope.tenant_id,
                    StrategyPaperOrder.user_id == scope.user_id,
                    StrategyPaperOrder.client_order_id == normalized_client_order_id,
                )
            )
            if existing is not None:
                self._assert_idempotent_request(existing, request_snapshot)
                return self._payload(existing, idempotent_replay=True)
            context = self._authorize(
                session,
                scope,
                binding_id=binding_id,
                signal_id=signal_id,
                side=normalized_side,
                technical_reason=normalized_reason,
                trading_date=day,
            )

        account = self.paper_trading.get_account(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        if context["binding"].account_id not in {"paper-default", str(account["account_id"])}:
            raise StrategyConflict(
                "paper_account_binding_mismatch",
                "Promoted 绑定与当前模拟账户不一致",
                details={
                    "binding_account_id": context["binding"].account_id,
                    "active_account_id": account["account_id"],
                },
            )
        if normalized_side == "buy":
            self._enforce_position_cap(
                scope,
                symbol=context["signal"].subject_code,
                quantity=normalized_quantity,
                target_weight=float(context["target_weight"]),
            )

        paper_order = self.paper_trading.place_order(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            symbol=context["signal"].subject_code,
            side=normalized_side,
            quantity=normalized_quantity,
            client_order_id=normalized_client_order_id,
            order_type=order_type,
            limit_price=limit_price,
            execution_profile=context["execution_profile"],
        )

        try:
            with self.database.session() as session:
                replay = session.scalar(
                    select(StrategyPaperOrder).where(
                        StrategyPaperOrder.tenant_id == scope.tenant_id,
                        StrategyPaperOrder.user_id == scope.user_id,
                        StrategyPaperOrder.client_order_id == normalized_client_order_id,
                    )
                )
                if replay is not None:
                    self._assert_idempotent_request(replay, request_snapshot)
                    return self._payload(replay, idempotent_replay=True)
                row = StrategyPaperOrder(
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    binding_id=context["binding"].id,
                    strategy_version_id=context["version"].id,
                    signal_id=context["signal"].id,
                    source_report_set_id=context["signal"].source_report_set_id,
                    client_order_id=normalized_client_order_id,
                    paper_order_id=str(paper_order.get("order_id") or "") or None,
                    symbol=context["signal"].subject_code,
                    side=normalized_side,
                    quantity=normalized_quantity,
                    technical_reason=normalized_reason,
                    status=str(paper_order.get("status") or "submitted"),
                    order_payload_json={
                        "strategy_request": request_snapshot,
                        "paper_order": dict(paper_order),
                    },
                )
                session.add(row)
                session.flush()
                self.repository.append_event(
                    session,
                    strategy_id=context["version"].strategy_id,
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    actor_id=scope.actor_id,
                    event_type="paper_order_submitted",
                    payload={
                        "strategy_paper_order_id": row.id,
                        "paper_order_id": row.paper_order_id,
                        "binding_id": row.binding_id,
                        "strategy_version_id": row.strategy_version_id,
                        "signal_id": row.signal_id,
                        "source_report_set_id": row.source_report_set_id,
                        "side": row.side,
                        "quantity": row.quantity,
                        "technical_reason": row.technical_reason,
                    },
                )
                return self._payload(row, idempotent_replay=False)
        except IntegrityError:
            with self.database.session() as session:
                replay = session.scalar(
                    select(StrategyPaperOrder).where(
                        StrategyPaperOrder.tenant_id == scope.tenant_id,
                        StrategyPaperOrder.user_id == scope.user_id,
                        StrategyPaperOrder.client_order_id == normalized_client_order_id,
                    )
                )
                if replay is None:
                    raise
                self._assert_idempotent_request(replay, request_snapshot)
                return self._payload(replay, idempotent_replay=True)

    def list(self, scope: OwnerScope, *, binding_id: str | None = None) -> dict[str, Any]:
        with self.database.session() as session:
            query = select(StrategyPaperOrder).where(
                StrategyPaperOrder.tenant_id == scope.tenant_id,
                StrategyPaperOrder.user_id == scope.user_id,
            )
            if binding_id:
                query = query.where(StrategyPaperOrder.binding_id == binding_id)
            rows = list(session.scalars(query.order_by(StrategyPaperOrder.created_at.desc())))
            return {"orders": [self._payload(row, idempotent_replay=False) for row in rows]}

    def _authorize(
        self,
        session,
        scope: OwnerScope,
        *,
        binding_id: str,
        signal_id: str,
        side: str,
        technical_reason: str,
        trading_date: date,
    ) -> dict[str, Any]:
        binding = session.scalar(
            select(PaperStrategyBinding).where(
                PaperStrategyBinding.id == binding_id,
                PaperStrategyBinding.tenant_id == scope.tenant_id,
                PaperStrategyBinding.user_id == scope.user_id,
            )
        )
        if binding is None:
            raise StrategyDomainError("paper_strategy_binding_not_found", "Promoted 模拟盘绑定不存在", status_code=404)
        version = session.get(StrategyVersion, binding.strategy_version_id)
        if version is None or version.governance_state != GovernanceState.PROMOTED.value:
            raise StrategyConflict(
                "strategy_version_not_promoted",
                "只有当前绑定的 Promoted 版本可以提交策略模拟委托",
            )
        strategy = session.get(Strategy, version.strategy_id)
        if (
            strategy is None
            or strategy.tenant_id != scope.tenant_id
            or strategy.user_id != scope.user_id
        ):
            raise StrategyDomainError("strategy_version_not_found", "策略版本不存在", status_code=404)
        confirmed = session.scalar(
            select(StrategyVerification.id).where(
                StrategyVerification.tenant_id == scope.tenant_id,
                StrategyVerification.user_id == scope.user_id,
                StrategyVerification.strategy_version_id == version.id,
                StrategyVerification.status == GovernanceState.VERIFIED.value,
                StrategyVerification.machine_passed.is_(True),
                StrategyVerification.confirmed_at.is_not(None),
            )
        )
        if confirmed is None:
            raise StrategyConflict("strategy_version_not_verified", "Promoted 版本缺少有效人工确认记录")
        if binding.mode != BacktestMode.HOLDINGS_PORTFOLIO.value:
            raise StrategyConflict(
                "paper_mode_not_supported",
                "当前模拟盘执行适配器仅接收 holdings_portfolio 的 A 股持仓委托",
            )
        signal = session.scalar(
            select(TradeSignal).where(
                TradeSignal.id == signal_id,
                TradeSignal.tenant_id == scope.tenant_id,
                TradeSignal.user_id == scope.user_id,
                TradeSignal.strategy_version_id == version.id,
            )
        )
        if signal is None:
            raise StrategyDomainError("trade_signal_not_found", "交易信号不存在", status_code=404)
        assignment = session.scalar(
            select(StrategyAssignment).where(
                StrategyAssignment.id == signal.assignment_id,
                StrategyAssignment.tenant_id == scope.tenant_id,
                StrategyAssignment.user_id == scope.user_id,
                StrategyAssignment.strategy_id == version.strategy_id,
                StrategyAssignment.fund_code == binding.fund_code,
                StrategyAssignment.mode == binding.mode,
            )
        )
        if assignment is None or signal.kind != "holding_stock":
            raise StrategyConflict("paper_signal_binding_mismatch", "信号不属于当前 Promoted 基金持仓绑定")
        latest_signal = session.scalar(
            select(TradeSignal)
            .where(
                TradeSignal.assignment_id == assignment.id,
                TradeSignal.strategy_version_id == version.id,
                TradeSignal.kind == signal.kind,
                TradeSignal.subject_code == signal.subject_code,
                TradeSignal.effective_from <= trading_date,
            )
            .order_by(
                TradeSignal.effective_from.desc(),
                TradeSignal.as_of_date.desc(),
                TradeSignal.revision.desc(),
                TradeSignal.created_at.desc(),
            )
        )
        if latest_signal is None or latest_signal.id != signal.id:
            raise StrategyConflict("paper_signal_superseded", "只能使用交易日当时最新生效的个股信号")
        if signal.effective_from > trading_date or (
            signal.expires_after is not None and signal.expires_after < trading_date
        ):
            raise StrategyConflict("paper_signal_not_effective", "信号在委托交易日尚未生效或已经过期")
        report = session.get(DailyReportSet, signal.source_report_set_id)
        if (
            report is None
            or report.as_of_date >= trading_date
            or report.effective_date > trading_date
            or report.status not in {"final", "final_partial"}
        ):
            raise StrategyConflict("paper_signal_point_in_time_invalid", "信号来源日报不满足 point-in-time 交易边界")
        document = parse_strategy_document(version.document_json)
        if is_report_conditions(document):
            raise StrategyConflict(
                "report_condition_paper_execution_unsupported",
                "原生报告条件当前仅支持冻结回测；模拟委托不能使用旧均线和基金门禁执行该版本",
            )
        if side == "buy":
            if strategy.lifecycle == StrategyLifecycle.ARCHIVED.value or assignment.paused or not assignment.enabled:
                raise StrategyConflict("paper_strategy_paused", "归档、暂停或禁用的策略绑定不能新增买入")
            if not _is_technical_entry(technical_reason, document=document):
                raise StrategyConflict(
                    "paper_buy_reason_invalid",
                    "买入必须由已登记的技术入场或再平衡事件触发",
                )
            if signal.state != SignalState.ALLOW_BUY.value:
                raise StrategyConflict("paper_buy_not_allowed", "个股信号未授予新增买入许可")
            fund_gate = self._latest_fund_gate(
                session,
                assignment_id=assignment.id,
                strategy_version_id=version.id,
                trading_date=trading_date,
            )
            if fund_gate is None or fund_gate.state != SignalState.ALLOW_BUY.value:
                raise StrategyConflict("paper_fund_gate_not_allowed", "基金总门禁未授予新增买入许可")
        elif not _is_technical_exit(technical_reason, document=document):
            raise StrategyConflict("paper_sell_reason_invalid", "卖出必须由已登记的技术、风险或报告退出事件触发")
        elif signal.state != SignalState.AVOID.value and technical_reason == "report_gate_avoid":
            raise StrategyConflict("paper_sell_reason_invalid", "非 AVOID 信号不能使用报告门禁退出原因")
        node = session.scalar(
            select(DailyReportNode).where(
                DailyReportNode.report_set_id == signal.source_report_set_id,
                DailyReportNode.node_type == "holding_uzi",
                DailyReportNode.subject_code == signal.subject_code,
            )
        )
        if node is None:
            raise StrategyConflict("paper_holding_weight_missing", "信号来源日报缺少对应持仓权重")
        disclosed = max(0.0, float(node.effective_weight_pct or node.disclosed_weight_pct or 0.0)) / 100.0
        target_weight = min(disclosed, document.execution.max_single_stock_pct / 100.0)
        if target_weight <= 0:
            raise StrategyConflict("paper_holding_weight_missing", "对应持仓没有可执行目标权重")
        return {
            "binding": binding,
            "version": version,
            "signal": signal,
            "target_weight": target_weight,
            "execution_profile": {
                "model_id": f"strategy-stock:{version.id}",
                "asset_type": "stock",
                "board_lot": document.asset_execution.stock.board_lot,
                "commission_rate": document.asset_execution.stock.commission_rate,
                "minimum_commission": document.asset_execution.stock.minimum_commission,
                "transfer_fee_rate": document.asset_execution.stock.transfer_fee_rate,
                "stamp_duty_rate": document.asset_execution.stock.stamp_duty_rate,
                "settlement": document.asset_execution.stock.settlement,
            },
        }

    @staticmethod
    def _latest_fund_gate(
        session,
        *,
        assignment_id: str,
        strategy_version_id: str,
        trading_date: date,
    ) -> TradeSignal | None:
        row = session.scalar(
            select(TradeSignal)
            .where(
                TradeSignal.assignment_id == assignment_id,
                TradeSignal.strategy_version_id == strategy_version_id,
                TradeSignal.kind == "fund",
                TradeSignal.effective_from <= trading_date,
            )
            .order_by(TradeSignal.effective_from.desc(), TradeSignal.revision.desc())
        )
        if row is not None and row.expires_after is not None and row.expires_after < trading_date:
            return None
        if row is not None:
            report = session.get(DailyReportSet, row.source_report_set_id)
            if (
                report is None
                or report.status not in {"final", "final_partial"}
                or report.as_of_date >= trading_date
                or report.effective_date > trading_date
            ):
                return None
        return row

    def _enforce_position_cap(
        self,
        scope: OwnerScope,
        *,
        symbol: str,
        quantity: int,
        target_weight: float,
    ) -> None:
        portfolio = self.paper_trading.get_portfolio(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        equity = float(portfolio.get("summary", {}).get("equity") or 0.0)
        if equity <= 0:
            raise StrategyConflict("paper_account_equity_unavailable", "模拟账户净值不可用")
        position = next(
            (row for row in portfolio.get("positions") or [] if str(row.get("symbol")) == symbol),
            None,
        )
        current_value = float((position or {}).get("market_value") or 0.0)
        # Strategy submission is an explicit execution path; unlike public
        # quote GETs it deliberately acquires the current executable quote.
        quote = self.paper_trading.market_data.get_live_quote(symbol, refresh=True)
        requested_value = int(quantity) * float(quote["latest_price"])
        maximum_value = equity * target_weight
        if current_value + requested_value > maximum_value + 0.01:
            raise StrategyConflict(
                "paper_target_weight_exceeded",
                "策略模拟买入将超过披露权重或单票仓位上限",
                details={
                    "target_weight": target_weight,
                    "maximum_value": round(maximum_value, 2),
                    "current_value": round(current_value, 2),
                    "requested_value": round(requested_value, 2),
                },
            )

    @staticmethod
    def _assert_idempotent_request(
        row: StrategyPaperOrder,
        request_snapshot: dict[str, Any],
    ) -> None:
        stored = dict(row.order_payload_json or {}).get("strategy_request")
        if stored is None:
            stored = {
                "binding_id": row.binding_id,
                "signal_id": row.signal_id,
                "side": row.side,
                "quantity": row.quantity,
                "technical_reason": row.technical_reason,
            }
            comparable = {
                key: request_snapshot[key]
                for key in stored
            }
        else:
            comparable = request_snapshot
        if stored != comparable:
            raise StrategyConflict(
                "idempotency_key_reused",
                "同一 client_order_id 不能用于不同的策略模拟委托",
            )

    @staticmethod
    def _payload(row: StrategyPaperOrder, *, idempotent_replay: bool) -> dict[str, Any]:
        stored_payload = dict(row.order_payload_json or {})
        paper_order = stored_payload.get("paper_order", stored_payload)
        return {
            "id": row.id,
            "binding_id": row.binding_id,
            "strategy_version_id": row.strategy_version_id,
            "signal_id": row.signal_id,
            "source_report_set_id": row.source_report_set_id,
            "client_order_id": row.client_order_id,
            "paper_order_id": row.paper_order_id,
            "symbol": row.symbol,
            "side": row.side,
            "quantity": row.quantity,
            "technical_reason": row.technical_reason,
            "status": row.status,
            "paper_order": paper_order,
            "created_at": row.created_at,
            "idempotent_replay": idempotent_replay,
        }


def _is_technical_entry(reason: str, *, document: StrategyDocumentV1) -> bool:
    normalized = str(reason or "").strip().lower()
    expected = f"ma{document.execution.fast_period}_cross_above_ma{document.execution.slow_period}"
    return (
        normalized == expected
        or normalized in {
            "scheduled_rebalance",
            "report_rebalance",
            "target_weight_rebalance",
        }
    )


def _is_technical_exit(reason: str, *, document: StrategyDocumentV1) -> bool:
    normalized = str(reason or "").strip().lower()
    expected = f"ma{document.execution.fast_period}_cross_below_ma{document.execution.slow_period}"
    return (
        normalized == expected
        or normalized in {
            "risk_exit",
            "forced_risk_exit",
            "target_weight_rebalance",
            "report_gate_avoid",
        }
    )


__all__ = ["StrategyPaperExecutionService"]
