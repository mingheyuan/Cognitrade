from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select

from research_system.strategies.contracts import (
    BacktestMode,
    SignalState,
    StrategyDocumentV1,
    strategy_content_hash,
)
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict, StrategyDomainError
from research_system.strategies.factors import composite_fund_score, extract_uzi_score
from research_system.strategies.models import (
    DailyReportNode,
    FactorSnapshot,
    Strategy,
    StrategyAssignment,
    StrategyDraft,
    StrategyVersion,
    TradeSignal,
)
from research_system.strategies.repository import StrategyRepository
from research_system.strategies.service import OwnerScope
from research_system.strategies.trading_calendar import add_trading_days, trading_day_distance


_HARD_GAPS = {
    "point_in_time_unverified",
    "point_in_time_violation",
    "fund_symbol_mismatch",
    "report_symbol_mismatch",
    "report_schema_invalid",
    "report_not_allowed",
    "critical_issue",
    "critical_issues_present",
    "fund_report_missing",
}


@dataclass(frozen=True)
class _PreviewVersion:
    id: str
    content_hash: str


class SignalEngineService:
    def __init__(self, database: StrategyDatabase) -> None:
        self.database = database
        self.repository = StrategyRepository()

    def recompute(
        self,
        scope: OwnerScope,
        *,
        assignment_id: str,
        report_set_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            assignment = self.repository.get_assignment(
                session,
                assignment_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            if assignment is None:
                raise StrategyDomainError("assignment_not_found", "未找到 Assignment", status_code=404)
            report_set = self.repository.get_report_set(
                session,
                report_set_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            if report_set is None:
                raise StrategyDomainError("daily_report_set_not_found", "未找到日报版本", status_code=404)
            if report_set.fund_code != assignment.fund_code:
                raise StrategyConflict("assignment_report_fund_mismatch", "日报基金与 Assignment 不一致")
            if report_set.status not in {"final", "final_partial", "blocked"}:
                raise StrategyConflict("daily_report_not_final", "只有已结算日报可以生成交易信号")
            self._activate_pending(assignment, report_set.effective_date)
            version = session.get(StrategyVersion, assignment.active_version_id)
            if version is None:
                raise StrategyDomainError("strategy_version_not_found", "Assignment 的策略版本不存在", status_code=409)
            document = StrategyDocumentV1.model_validate(version.document_json)
            nodes = self.repository.list_report_nodes(session, report_set.id)
            result = self._calculate(
                report_set=report_set,
                nodes=nodes,
                document=document,
                strategy_version=version,
                assignment=assignment,
            )
            factor_snapshot = self._factor_snapshot(
                session,
                report_set=report_set,
                document=document,
                result=result,
            )
            fund_signal = self._persist_signal(
                session,
                scope=scope,
                assignment=assignment,
                version=version,
                report_set=report_set,
                kind="fund",
                subject_code=report_set.fund_code,
                calculation=result["fund_signal"],
                factor_snapshot_id=factor_snapshot.id,
            )
            stock_signals: list[TradeSignal] = []
            if (
                document.report_inputs.independent_holding_signals_enabled
                or BacktestMode.HOLDINGS_PORTFOLIO
                in document.report_inputs.enabled_backtest_modes
            ):
                for stock in result["stock_signals"]:
                    stock_signals.append(
                        self._persist_signal(
                            session,
                            scope=scope,
                            assignment=assignment,
                            version=version,
                            report_set=report_set,
                            kind="holding_stock",
                            subject_code=str(stock["subject_code"]),
                            calculation=stock,
                            factor_snapshot_id=None,
                        )
                    )
            session.flush()
            return {
                "assignment_id": assignment.id,
                "strategy_version_id": version.id,
                "strategy_content_hash": version.content_hash,
                "report_set_id": report_set.id,
                "fund_signal": self._signal_payload(fund_signal),
                "holding_signals": [self._signal_payload(signal) for signal in stock_signals],
                "factor_snapshot": self._factor_payload(factor_snapshot),
            }

    def preview(
        self,
        scope: OwnerScope,
        *,
        assignment_id: str,
        report_set_id: str,
        draft_strategy_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            assignment = self.repository.get_assignment(
                session,
                assignment_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            report_set = self.repository.get_report_set(
                session,
                report_set_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            if assignment is None or report_set is None:
                raise StrategyDomainError("signal_preview_source_not_found", "预演所需 Assignment 或日报不存在", status_code=404)
            version = session.get(StrategyVersion, assignment.active_version_id)
            if version is None:
                raise StrategyDomainError("strategy_version_not_found", "策略版本不存在", status_code=409)
            preview_version = version
            if draft_strategy_id is not None:
                strategy = session.scalar(
                    select(Strategy).where(
                        Strategy.id == draft_strategy_id,
                        Strategy.tenant_id == scope.tenant_id,
                        Strategy.user_id == scope.user_id,
                        Strategy.is_system_template.is_(False),
                    )
                )
                if strategy is None:
                    raise StrategyDomainError("strategy_not_found", "未找到待预演策略", status_code=404)
                if assignment.strategy_id != strategy.id:
                    raise StrategyConflict("assignment_strategy_mismatch", "Assignment 与待预演策略不一致")
                draft = session.get(StrategyDraft, strategy.id)
                if draft is None:
                    raise StrategyConflict("strategy_draft_missing", "策略当前没有可预演草稿")
                document = StrategyDocumentV1.model_validate(draft.document_json)
                preview_version = _PreviewVersion(
                    id=f"draft:{strategy.id}:{draft.draft_revision}",
                    content_hash=strategy_content_hash(document),
                )
            else:
                document = StrategyDocumentV1.model_validate(version.document_json)
            result = self._calculate(
                report_set=report_set,
                nodes=self.repository.list_report_nodes(session, report_set.id),
                document=document,
                strategy_version=preview_version,
                assignment=assignment,
            )
            return {
                "persisted": False,
                "assignment_id": assignment.id,
                "strategy_version_id": preview_version.id,
                "strategy_content_hash": preview_version.content_hash,
                "report_set_id": report_set.id,
                **result,
            }

    def list_signals(
        self,
        scope: OwnerScope,
        *,
        assignment_id: str | None = None,
        subject_code: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            rows = self.repository.list_signals(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                assignment_id=assignment_id,
                subject_code=subject_code,
                start_date=start_date,
                end_date=end_date,
            )
            return {"signals": [self._signal_payload(row) for row in rows]}

    def get_signal(self, scope: OwnerScope, signal_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.scalar(
                select(TradeSignal).where(
                    TradeSignal.id == signal_id,
                    TradeSignal.tenant_id == scope.tenant_id,
                    TradeSignal.user_id == scope.user_id,
                )
            )
            if row is None:
                raise StrategyDomainError("trade_signal_not_found", "未找到交易信号", status_code=404)
            return self._signal_payload(row)

    def carry_forward(
        self,
        scope: OwnerScope,
        *,
        assignment_id: str,
        as_of_date: date,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            assignment = self.repository.get_assignment(
                session,
                assignment_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            if assignment is None:
                raise StrategyDomainError("assignment_not_found", "未找到 Assignment", status_code=404)
            version = session.get(StrategyVersion, assignment.active_version_id)
            if version is None:
                raise StrategyDomainError("strategy_version_not_found", "策略版本不存在", status_code=409)
            document = StrategyDocumentV1.model_validate(version.document_json)
            previous = session.scalar(
                select(TradeSignal)
                .where(
                    TradeSignal.assignment_id == assignment.id,
                    TradeSignal.kind == "fund",
                    TradeSignal.subject_code == assignment.fund_code,
                    TradeSignal.as_of_date < as_of_date,
                )
                .order_by(TradeSignal.as_of_date.desc())
                .limit(1)
            )
            if previous is None:
                raise StrategyConflict("carry_source_missing", "没有可沿用的历史信号")
            root = previous
            visited: set[str] = set()
            while root.carried_from_signal_id and root.id not in visited:
                visited.add(root.id)
                parent = session.get(TradeSignal, root.carried_from_signal_id)
                if parent is None:
                    break
                root = parent
            age = _weekday_distance(root.as_of_date, as_of_date)
            allowed = age <= document.signal_thresholds.missing_report_carry_days
            state = root.state if allowed else SignalState.BLOCKED.value
            reasons = [
                f"missing_daily_report_carry:{age}/{document.signal_thresholds.missing_report_carry_days}"
            ] if allowed else ["missing_daily_report_carry_expired"]
            payload = {
                "tenant_id": scope.tenant_id,
                "user_id": scope.user_id,
                "assignment_id": assignment.id,
                "strategy_version_id": version.id,
                "as_of_date": as_of_date.isoformat(),
                "state": state,
                "source_signal_id": previous.id,
                "reasons": reasons,
            }
            content_hash = _hash(payload)
            existing = session.scalar(select(TradeSignal).where(TradeSignal.content_hash == content_hash))
            if existing is not None:
                return self._signal_payload(existing)
            signal = TradeSignal(
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                kind="fund",
                subject_code=assignment.fund_code,
                strategy_version_id=version.id,
                assignment_id=assignment.id,
                as_of_date=as_of_date,
                revision=1,
                state=state,
                score_lower_bound=root.score_lower_bound,
                score_upper_bound=root.score_upper_bound,
                confidence=root.confidence if allowed else 0.0,
                coverage=root.coverage if allowed else 0.0,
                reasons_json=reasons,
                factor_snapshot_id=root.factor_snapshot_id,
                source_report_set_id=root.source_report_set_id,
                effective_from=_add_weekdays(as_of_date, 1),
                expires_after=(
                    root.expires_after
                    or _add_weekdays(
                        root.effective_from,
                        document.signal_thresholds.missing_report_carry_days,
                    )
                )
                if allowed
                else None,
                carried_from_signal_id=previous.id,
                content_hash=content_hash,
            )
            session.add(signal)
            session.flush()
            return self._signal_payload(signal)

    def _calculate(
        self,
        *,
        report_set,
        nodes: list[DailyReportNode],
        document: StrategyDocumentV1,
        strategy_version: StrategyVersion | _PreviewVersion,
        assignment: StrategyAssignment,
    ) -> dict[str, Any]:
        fund_node = next((node for node in nodes if node.node_type == "fund_report"), None)
        holding_nodes = [node for node in nodes if node.node_type == "holding_uzi"]
        fund_payload = dict(fund_node.source_json) if fund_node is not None else {}
        holdings = [self._holding_input(node) for node in holding_nodes]
        expected_count = max(1, int(report_set.max_top_n))
        disclosure_age_days = _integer(fund_payload.get("disclosure_age_days"))
        is_lookthrough = bool(
            fund_payload.get("is_fund_of_funds")
            or fund_payload.get("holding_source") == "target_etf_lookthrough"
        )
        composite = composite_fund_score(
            fund_payload=fund_payload,
            holdings=holdings,
            expected_count=expected_count,
            disclosure_age_days=disclosure_age_days,
            is_lookthrough=is_lookthrough,
            document=document,
        )
        hard_reasons = self._hard_reasons(report_set, fund_node, fund_payload)
        coverage = min(float(report_set.coverage), composite.coverage)
        confidence = min(composite.confidence, coverage)
        fund_state, reasons = decide_signal_state(
            lower=composite.lower,
            coverage=coverage,
            confidence=confidence,
            disclosure_age_days=disclosure_age_days,
            hard_reasons=hard_reasons,
            document=document,
        )
        stock_signals = [
            self._stock_calculation(
                node,
                document=document,
                report_set=report_set,
                disclosure_age_days=disclosure_age_days,
                parent_hard_reasons=hard_reasons,
            )
            for node in holding_nodes[: document.report_inputs.top_n]
        ]
        return {
            "strategy_version_id": strategy_version.id,
            "strategy_content_hash": strategy_version.content_hash,
            "assignment_id": assignment.id,
            "report_set_id": report_set.id,
            "composite": composite.payload(),
            "fund_signal": {
                "state": fund_state.value,
                "score_lower_bound": composite.lower,
                "score_upper_bound": composite.upper,
                "coverage": coverage,
                "confidence": confidence,
                "reasons": reasons,
            },
            "stock_signals": stock_signals,
        }

    @staticmethod
    def _hard_reasons(report_set, fund_node, fund_payload: dict[str, Any]) -> list[str]:
        gaps = set(str(value) for value in (report_set.data_gaps_json or []))
        if fund_node is None:
            gaps.add("fund_report_missing")
        else:
            gaps.update(str(value) for value in (fund_node.data_gaps_json or []))
        if fund_payload.get("report_allowed") is False:
            gaps.add("report_not_allowed")
        if _integer(fund_payload.get("critical_count"), default=0) > 0:
            gaps.add("critical_issues_present")
        if report_set.status == "blocked":
            gaps.add("daily_report_blocked")
        return sorted(gap for gap in gaps if gap in _HARD_GAPS or gap == "daily_report_blocked")

    @staticmethod
    def _holding_input(node: DailyReportNode) -> dict[str, Any]:
        source = dict(node.source_json or {})
        return {
            **source,
            "subject_code": node.subject_code,
            "status": node.status,
            "confidence": node.confidence,
            "fund_weight_pct": node.disclosed_weight_pct,
            "effective_weight_pct": node.effective_weight_pct,
            "supported": source.get("supported", True),
            "source": source,
        }

    @staticmethod
    def _stock_calculation(
        node: DailyReportNode,
        *,
        document: StrategyDocumentV1,
        report_set,
        disclosure_age_days: int | None,
        parent_hard_reasons: list[str],
    ) -> dict[str, Any]:
        source = dict(node.source_json or {})
        score = extract_uzi_score({**source, "source": source})
        hard: list[str] = []
        if node.status not in {"completed", "available", "final"}:
            hard.append("holding_uzi_incomplete")
        if score is None:
            hard.append("uzi_overall_score_missing")
        if source.get("report_allowed") is False:
            hard.append("report_not_allowed")
        if _integer(source.get("critical_count"), default=0) > 0:
            hard.append("critical_issues_present")
        confidence = min(float(node.confidence or 0.0), 1.0 if score is not None else 0.0)
        state, reasons = decide_signal_state(
            lower=float(score or 0.0),
            coverage=1.0 if score is not None and not hard else 0.0,
            confidence=confidence,
            disclosure_age_days=disclosure_age_days,
            hard_reasons=[*parent_hard_reasons, *hard],
            document=document,
        )
        return {
            "subject_code": node.subject_code,
            "state": state.value,
            "score_lower_bound": float(score or 0.0),
            "score_upper_bound": float(score if score is not None else 100.0),
            "coverage": 1.0 if score is not None and not hard else 0.0,
            "confidence": confidence,
            "reasons": reasons,
            "source_node_id": node.id,
            "source_artifact_id": node.artifact_id,
            "report_set_id": report_set.id,
        }

    def _factor_snapshot(self, session, *, report_set, document, result: dict[str, Any]) -> FactorSnapshot:
        source_payload = {
            "report_source_hash": report_set.source_hash,
            "report_set_id": report_set.id,
            "report_revision": report_set.revision,
            "extractor_version": document.pins.factor_extractor_version,
            "composite": result["composite"],
        }
        source_hash = _hash(source_payload)
        existing = session.scalar(
            select(FactorSnapshot).where(
                FactorSnapshot.subject_type == "fund",
                FactorSnapshot.subject_code == report_set.fund_code,
                FactorSnapshot.as_of_date == report_set.as_of_date,
                FactorSnapshot.extractor_version == document.pins.factor_extractor_version,
                FactorSnapshot.source_hash == source_hash,
            )
        )
        if existing is not None:
            return existing
        composite = result["composite"]
        missing = sorted(
            {
                factor
                for module in composite["modules"].values()
                for factor in module.get("missing", [])
            }
        )
        row = FactorSnapshot(
            report_set_id=report_set.id,
            subject_type="fund",
            subject_code=report_set.fund_code,
            as_of_date=report_set.as_of_date,
            extractor_version=document.pins.factor_extractor_version,
            factors_json=composite,
            missing_factors_json=missing,
            score_lower_bound=float(composite["score_lower_bound"]),
            score_upper_bound=float(composite["score_upper_bound"]),
            confidence=float(composite["confidence"]),
            source_hash=source_hash,
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _persist_signal(
        session,
        *,
        scope: OwnerScope,
        assignment: StrategyAssignment,
        version: StrategyVersion,
        report_set,
        kind: str,
        subject_code: str,
        calculation: dict[str, Any],
        factor_snapshot_id: str | None,
    ) -> TradeSignal:
        content = {
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "kind": kind,
            "subject_code": subject_code,
            "strategy_version_id": version.id,
            "strategy_content_hash": version.content_hash,
            "assignment_id": assignment.id,
            "report_set_id": report_set.id,
            "report_revision": report_set.revision,
            "as_of_date": report_set.as_of_date.isoformat(),
            "calculation": calculation,
        }
        content_hash = _hash(content)
        existing = session.scalar(select(TradeSignal).where(TradeSignal.content_hash == content_hash))
        if existing is not None:
            return existing
        same_revision = session.scalar(
            select(TradeSignal).where(
                TradeSignal.tenant_id == scope.tenant_id,
                TradeSignal.user_id == scope.user_id,
                TradeSignal.assignment_id == assignment.id,
                TradeSignal.kind == kind,
                TradeSignal.subject_code == subject_code,
                TradeSignal.strategy_version_id == version.id,
                TradeSignal.as_of_date == report_set.as_of_date,
                TradeSignal.revision == report_set.revision,
            )
        )
        if same_revision is not None:
            raise StrategyConflict(
                "immutable_signal_revision_conflict",
                "同一日报修订版已生成不同信号；必须创建新的日报 revision",
            )
        document = StrategyDocumentV1.model_validate(version.document_json)
        row = TradeSignal(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            kind=kind,
            subject_code=subject_code,
            strategy_version_id=version.id,
            assignment_id=assignment.id,
            as_of_date=report_set.as_of_date,
            revision=report_set.revision,
            state=str(calculation["state"]),
            score_lower_bound=float(calculation["score_lower_bound"]),
            score_upper_bound=float(calculation["score_upper_bound"]),
            confidence=float(calculation["confidence"]),
            coverage=float(calculation["coverage"]),
            reasons_json=list(calculation.get("reasons") or []),
            factor_snapshot_id=factor_snapshot_id,
            source_report_set_id=report_set.id,
            effective_from=report_set.effective_date,
            expires_after=_add_weekdays(
                report_set.effective_date,
                document.signal_thresholds.missing_report_carry_days,
            ),
            content_hash=content_hash,
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _activate_pending(assignment: StrategyAssignment, effective_date: date) -> None:
        if assignment.pending_version_id and assignment.effective_from <= effective_date:
            assignment.active_version_id = assignment.pending_version_id
            assignment.pending_version_id = None

    @staticmethod
    def _signal_payload(row: TradeSignal) -> dict[str, Any]:
        return {
            "id": row.id,
            "kind": row.kind,
            "subject_code": row.subject_code,
            "strategy_version_id": row.strategy_version_id,
            "assignment_id": row.assignment_id,
            "as_of_date": row.as_of_date,
            "revision": row.revision,
            "state": row.state,
            "score_lower_bound": row.score_lower_bound,
            "score_upper_bound": row.score_upper_bound,
            "confidence": row.confidence,
            "coverage": row.coverage,
            "reasons": row.reasons_json,
            "factor_snapshot_id": row.factor_snapshot_id,
            "source_report_set_id": row.source_report_set_id,
            "effective_from": row.effective_from,
            "expires_after": row.expires_after,
            "carried_from_signal_id": row.carried_from_signal_id,
            "content_hash": row.content_hash,
            "created_at": row.created_at,
        }

    @staticmethod
    def _factor_payload(row: FactorSnapshot) -> dict[str, Any]:
        return {
            "id": row.id,
            "report_set_id": row.report_set_id,
            "subject_type": row.subject_type,
            "subject_code": row.subject_code,
            "as_of_date": row.as_of_date,
            "extractor_version": row.extractor_version,
            "factors": row.factors_json,
            "missing_factors": row.missing_factors_json,
            "score_lower_bound": row.score_lower_bound,
            "score_upper_bound": row.score_upper_bound,
            "confidence": row.confidence,
            "source_hash": row.source_hash,
        }


def decide_signal_state(
    *,
    lower: float,
    coverage: float,
    confidence: float,
    disclosure_age_days: int | None,
    hard_reasons: list[str],
    document: StrategyDocumentV1,
) -> tuple[SignalState, list[str]]:
    reasons = list(dict.fromkeys(str(reason) for reason in hard_reasons if str(reason)))
    gates = document.quality_gates
    thresholds = document.signal_thresholds
    if reasons:
        return SignalState.BLOCKED, reasons
    if disclosure_age_days is None:
        return SignalState.BLOCKED, ["holding_disclosure_age_unverified"]
    if disclosure_age_days > gates.max_holding_staleness_days:
        return SignalState.BLOCKED, [
            f"holding_disclosure_stale:{disclosure_age_days}>{gates.max_holding_staleness_days}"
        ]
    if coverage < gates.minimum_coverage:
        return SignalState.BLOCKED, [
            f"coverage_below_minimum:{coverage:.4f}<{gates.minimum_coverage:.4f}"
        ]
    if confidence < thresholds.minimum_confidence:
        return SignalState.BLOCKED, [
            f"confidence_below_minimum:{confidence:.4f}<{thresholds.minimum_confidence:.4f}"
        ]
    if lower >= thresholds.buy:
        state = SignalState.ALLOW_BUY
        reasons.append(f"score_at_or_above_buy:{lower:.4f}>={thresholds.buy:.4f}")
    elif lower >= thresholds.watch:
        state = SignalState.WATCH
        reasons.append(f"score_in_watch_range:{lower:.4f}")
    else:
        state = SignalState.AVOID
        reasons.append(f"score_below_watch:{lower:.4f}<{thresholds.watch:.4f}")
    if coverage < gates.normal_coverage and state == SignalState.ALLOW_BUY:
        state = SignalState.WATCH
        reasons.append(f"coverage_caps_state_to_watch:{coverage:.4f}<{gates.normal_coverage:.4f}")
    return state, reasons


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _add_weekdays(value: date, days: int) -> date:
    return add_trading_days(value, days)


def _weekday_distance(start: date, end: date) -> int:
    return trading_day_distance(start, end)


def _integer(value: Any, *, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


__all__ = ["SignalEngineService", "decide_signal_state"]
