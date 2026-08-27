from __future__ import annotations

from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from research_system.strategies.models import (
    BacktestRun,
    DailyReportNode,
    DailyReportSet,
    JobAttempt,
    OrchestrationJob,
    Strategy,
    StrategyAssignment,
    StrategyDraft,
    StrategyEvent,
    StrategyVersion,
    TradeSignal,
)


class StrategyRepository:
    @staticmethod
    def owner_filter(model, *, tenant_id: str, user_id: str):
        return and_(model.tenant_id == tenant_id, model.user_id == user_id)

    def list_strategies(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        include_archived: bool = False,
    ) -> list[Strategy]:
        predicate = or_(
            Strategy.is_system_template.is_(True),
            self.owner_filter(Strategy, tenant_id=tenant_id, user_id=user_id),
        )
        if not include_archived:
            predicate = and_(predicate, Strategy.lifecycle != "archived")
        return list(
            session.scalars(
                select(Strategy)
                .where(predicate)
                .order_by(Strategy.is_system_template.desc(), Strategy.updated_at.desc())
            )
        )

    def get_strategy(
        self,
        session: Session,
        strategy_id: str,
        *,
        tenant_id: str,
        user_id: str,
        allow_system: bool = True,
    ) -> Strategy | None:
        owner = self.owner_filter(Strategy, tenant_id=tenant_id, user_id=user_id)
        predicate = or_(owner, Strategy.is_system_template.is_(True)) if allow_system else owner
        return session.scalar(select(Strategy).where(Strategy.id == strategy_id, predicate))

    @staticmethod
    def get_system_template(session: Session, system_key: str) -> Strategy | None:
        return session.scalar(
            select(Strategy).where(
                Strategy.is_system_template.is_(True),
                Strategy.system_key == system_key,
            )
        )

    @staticmethod
    def get_draft(session: Session, strategy_id: str) -> StrategyDraft | None:
        return session.get(StrategyDraft, strategy_id)

    @staticmethod
    def latest_version(session: Session, strategy_id: str) -> StrategyVersion | None:
        return session.scalar(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy_id)
            .order_by(StrategyVersion.version_number.desc())
            .limit(1)
        )

    @staticmethod
    def get_version(session: Session, strategy_id: str, version_id: str) -> StrategyVersion | None:
        return session.scalar(
            select(StrategyVersion).where(
                StrategyVersion.id == version_id,
                StrategyVersion.strategy_id == strategy_id,
            )
        )

    @staticmethod
    def list_versions(session: Session, strategy_id: str) -> list[StrategyVersion]:
        return list(
            session.scalars(
                select(StrategyVersion)
                .where(StrategyVersion.strategy_id == strategy_id)
                .order_by(StrategyVersion.version_number.desc())
            )
        )

    @staticmethod
    def next_version_number(session: Session, strategy_id: str) -> int:
        current = session.scalar(
            select(func.max(StrategyVersion.version_number)).where(
                StrategyVersion.strategy_id == strategy_id
            )
        )
        return int(current or 0) + 1

    @staticmethod
    def append_event(
        session: Session,
        *,
        strategy_id: str | None,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        event_type: str,
        payload: dict | None = None,
    ) -> StrategyEvent:
        event = StrategyEvent(
            strategy_id=strategy_id,
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id=actor_id,
            event_type=event_type,
            payload_json=dict(payload or {}),
        )
        session.add(event)
        return event

    def list_events(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        strategy_id: str,
        limit: int = 100,
    ) -> list[StrategyEvent]:
        return list(
            session.scalars(
                select(StrategyEvent)
                .where(
                    StrategyEvent.strategy_id == strategy_id,
                    self.owner_filter(StrategyEvent, tenant_id=tenant_id, user_id=user_id),
                )
                .order_by(StrategyEvent.created_at.desc())
                .limit(limit)
            )
        )

    def list_assignments(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        strategy_id: str | None = None,
        fund_code: str | None = None,
    ) -> list[StrategyAssignment]:
        query = select(StrategyAssignment).where(
            self.owner_filter(StrategyAssignment, tenant_id=tenant_id, user_id=user_id)
        )
        if strategy_id:
            query = query.where(StrategyAssignment.strategy_id == strategy_id)
        if fund_code:
            query = query.where(StrategyAssignment.fund_code == fund_code)
        return list(session.scalars(query.order_by(StrategyAssignment.updated_at.desc())))

    def get_assignment(
        self,
        session: Session,
        assignment_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> StrategyAssignment | None:
        return session.scalar(
            select(StrategyAssignment).where(
                StrategyAssignment.id == assignment_id,
                self.owner_filter(StrategyAssignment, tenant_id=tenant_id, user_id=user_id),
            )
        )

    def get_job(
        self,
        session: Session,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> OrchestrationJob | None:
        return session.scalar(
            select(OrchestrationJob).where(
                OrchestrationJob.id == job_id,
                self.owner_filter(OrchestrationJob, tenant_id=tenant_id, user_id=user_id),
            )
        )

    @staticmethod
    def list_attempts(session: Session, job_id: str) -> list[JobAttempt]:
        return list(
            session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_id == job_id)
                .order_by(JobAttempt.attempt_number.asc())
            )
        )

    def get_report_set(
        self,
        session: Session,
        report_set_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> DailyReportSet | None:
        return session.scalar(
            select(DailyReportSet).where(
                DailyReportSet.id == report_set_id,
                self.owner_filter(DailyReportSet, tenant_id=tenant_id, user_id=user_id),
            )
        )

    def list_report_sets(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        fund_code: str | None = None,
        as_of_date: date | None = None,
    ) -> list[DailyReportSet]:
        query = select(DailyReportSet).where(
            self.owner_filter(DailyReportSet, tenant_id=tenant_id, user_id=user_id)
        )
        if fund_code:
            query = query.where(DailyReportSet.fund_code == fund_code)
        if as_of_date:
            query = query.where(DailyReportSet.as_of_date == as_of_date)
        return list(
            session.scalars(
                query.order_by(DailyReportSet.as_of_date.desc(), DailyReportSet.revision.desc())
            )
        )

    @staticmethod
    def list_report_nodes(session: Session, report_set_id: str) -> list[DailyReportNode]:
        return list(
            session.scalars(
                select(DailyReportNode)
                .where(DailyReportNode.report_set_id == report_set_id)
                .order_by(DailyReportNode.node_type.asc(), DailyReportNode.rank.asc())
            )
        )

    def list_signals(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        assignment_id: str | None = None,
        subject_code: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[TradeSignal]:
        query = select(TradeSignal).where(
            self.owner_filter(TradeSignal, tenant_id=tenant_id, user_id=user_id)
        )
        if assignment_id:
            query = query.where(TradeSignal.assignment_id == assignment_id)
        if subject_code:
            query = query.where(TradeSignal.subject_code == subject_code)
        if start_date:
            query = query.where(TradeSignal.as_of_date >= start_date)
        if end_date:
            query = query.where(TradeSignal.as_of_date <= end_date)
        return list(
            session.scalars(query.order_by(TradeSignal.as_of_date.desc(), TradeSignal.kind.asc()))
        )

    def list_backtests(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        strategy_id: str | None = None,
        strategy_version_id: str | None = None,
        fund_code: str | None = None,
        mode: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[BacktestRun]:
        query = select(BacktestRun).where(
            self.owner_filter(BacktestRun, tenant_id=tenant_id, user_id=user_id)
        )
        if strategy_id:
            query = query.where(BacktestRun.strategy_id == strategy_id)
        if strategy_version_id:
            query = query.where(BacktestRun.strategy_version_id == strategy_version_id)
        if fund_code:
            query = query.where(BacktestRun.fund_code == fund_code)
        if mode:
            query = query.where(BacktestRun.mode == mode)
        if status:
            query = query.where(BacktestRun.status == status)
        return list(
            session.scalars(
                query
                .order_by(BacktestRun.created_at.desc())
                .limit(min(max(int(limit), 1), 200))
            )
        )

__all__ = ["StrategyRepository"]
