from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.id_utils import generate_ulid
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.strategies.contracts import (
    REPORT_CONDITIONS_TEMPLATE_KEY,
    REPORT_CONDITIONS_TEMPLATE_NAME,
    SYSTEM_TEMPLATE_KEY,
    SYSTEM_TEMPLATE_NAME,
    BacktestMode,
    GovernanceState,
    StrategyDocument,
    StrategyLifecycle,
    canonical_strategy_json,
    next_weekday,
    parse_strategy_document,
    report_conditions_template_document,
    strategy_content_hash,
    system_template_document,
)
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import (
    StrategyConflict,
    StrategyDomainError,
    StrategyNotFound,
)
from research_system.strategies.models import (
    BacktestRun,
    PaperStrategyBinding,
    Strategy,
    StrategyAssignment,
    StrategyDraft,
    StrategyVerification,
    StrategyVersion,
    TradeSignal,
)
from research_system.strategies.repository import StrategyRepository


@dataclass(frozen=True)
class OwnerScope:
    tenant_id: str
    user_id: str
    actor_id: str


SYSTEM_OWNER = OwnerScope("__system__", "__system__", "system_seed")


def owner_scope(*, tenant_id: str | None, user_id: str | None, actor_id: str | None) -> OwnerScope:
    return OwnerScope(
        tenant_id=str(tenant_id or "local").strip() or "local",
        user_id=str(user_id or "local_development").strip() or "local_development",
        actor_id=str(actor_id or user_id or "local_development").strip() or "local_development",
    )


class StrategyService:
    def __init__(
        self,
        database: StrategyDatabase,
        *,
        workspace_root: Path,
        auto_bootstrap: bool = True,
        runtime_settings=None,
    ) -> None:
        self.database = database
        self.workspace_root = Path(workspace_root)
        self.runtime_settings = runtime_settings
        self.repository = StrategyRepository()
        self.database.bootstrap_or_validate(allow_bootstrap=auto_bootstrap)
        self.seed_system_template()
        self._seed_template(
            REPORT_CONDITIONS_TEMPLATE_KEY,
            REPORT_CONDITIONS_TEMPLATE_NAME,
            report_conditions_template_document(),
        )

    def close(self) -> None:
        self.database.close()

    def health(self) -> dict[str, object]:
        return self.database.health()

    def seed_system_template(self) -> str:
        return self._seed_template(SYSTEM_TEMPLATE_KEY, SYSTEM_TEMPLATE_NAME, system_template_document())

    def _seed_template(self, key: str, name: str, document: StrategyDocument) -> str:
        with self.database.session() as session:
            existing = self.repository.get_system_template(session, key)
            if existing is not None:
                return existing.id
            from research_system.strategies.runtime_pins import resolve_publication_document
            document = resolve_publication_document(document, self.runtime_settings)
            strategy = Strategy(
                tenant_id=SYSTEM_OWNER.tenant_id,
                user_id=SYSTEM_OWNER.user_id,
                name=name,
                system_key=key,
                is_system_template=True,
                lifecycle=StrategyLifecycle.PUBLISHED.value,
            )
            session.add(strategy)
            session.flush()
            version = StrategyVersion(
                strategy_id=strategy.id,
                version_number=1,
                document_json=document.model_dump(mode="json"),
                content_hash=strategy_content_hash(document),
                governance_state=GovernanceState.PUBLISHED.value,
                published_by=SYSTEM_OWNER.actor_id,
            )
            session.add(version)
            session.flush()
            self._attach_publication_artifact(version, strategy, SYSTEM_OWNER)
            self.repository.append_event(
                session,
                strategy_id=strategy.id,
                tenant_id=SYSTEM_OWNER.tenant_id,
                user_id=SYSTEM_OWNER.user_id,
                actor_id=SYSTEM_OWNER.actor_id,
                event_type="system_template_seeded",
                payload={"system_key": key, "version_id": version.id},
            )
            return strategy.id

    def list_strategies(self, scope: OwnerScope, *, include_archived: bool = False) -> dict[str, Any]:
        with self.database.session() as session:
            rows = self.repository.list_strategies(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                include_archived=include_archived,
            )
            return {"strategies": [self._strategy_payload(session, row) for row in rows]}

    def list_catalog(self, scope: OwnerScope) -> dict[str, Any]:
        with self.database.session() as session:
            rows = self.repository.list_strategies(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                include_archived=False,
            )
            return {
                "strategies": [
                    self._strategy_payload(session, row, detail=True)
                    for row in rows
                    if row.is_system_template
                ]
            }

    def create_strategy(
        self,
        scope: OwnerScope,
        *,
        name: str,
        description: str = "",
        document: dict[str, Any] | None = None,
        from_strategy_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_name = self._name(name)
        with self.database.session() as session:
            base_version_id = None
            if from_strategy_id:
                source = self._require_strategy(session, from_strategy_id, scope)
                source_version = self.repository.latest_version(session, source.id)
                if source_version is None:
                    source_draft = self.repository.get_draft(session, source.id)
                    if source_draft is None:
                        raise StrategyConflict("strategy_source_empty", "源策略没有可复制的内容")
                    candidate = dict(source_draft.document_json)
                else:
                    candidate = dict(source_version.document_json)
                    base_version_id = source_version.id
            elif document is not None:
                candidate = dict(document)
            else:
                candidate = report_conditions_template_document().model_dump(mode="json")
                if description:
                    candidate["metadata"]["description"] = description
            validated = parse_strategy_document(candidate)
            if description and not from_strategy_id and document is not None:
                validated.metadata.description = description
            strategy = Strategy(
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                name=normalized_name,
                lifecycle=StrategyLifecycle.DRAFT.value,
            )
            session.add(strategy)
            session.flush()
            session.add(
                StrategyDraft(
                    strategy_id=strategy.id,
                    base_version_id=base_version_id,
                    draft_revision=1,
                    document_json=validated.model_dump(mode="json"),
                    validation_json={"valid": True, "errors": []},
                    updated_by=scope.actor_id,
                )
            )
            self.repository.append_event(
                session,
                strategy_id=strategy.id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="created",
                payload={"from_strategy_id": from_strategy_id},
            )
            session.flush()
            return self._strategy_payload(session, strategy, detail=True)

    def get_strategy(self, scope: OwnerScope, strategy_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = self._require_strategy(session, strategy_id, scope)
            return self._strategy_payload(session, row, detail=True)

    def update_draft(
        self,
        scope: OwnerScope,
        strategy_id: str,
        *,
        expected_revision: int,
        document: dict[str, Any],
        name: str | None = None,
    ) -> dict[str, Any]:
        validated = parse_strategy_document(document)
        with self.database.session() as session:
            strategy = self._require_mutable_strategy(session, strategy_id, scope)
            if strategy.lifecycle == StrategyLifecycle.ARCHIVED.value:
                raise StrategyConflict("strategy_archived", "归档策略恢复后才能编辑")
            draft = self.repository.get_draft(session, strategy.id)
            if draft is None:
                if expected_revision != 0:
                    raise StrategyConflict(
                        "draft_revision_conflict",
                        "工作草稿已发生变化",
                        details={"current_revision": 0, "current_document": None},
                    )
                latest = self.repository.latest_version(session, strategy.id)
                draft = StrategyDraft(
                    strategy_id=strategy.id,
                    base_version_id=latest.id if latest else None,
                    draft_revision=1,
                    document_json=validated.model_dump(mode="json"),
                    validation_json={"valid": True, "errors": []},
                    updated_by=scope.actor_id,
                )
                session.add(draft)
            else:
                if draft.draft_revision != expected_revision:
                    raise StrategyConflict(
                        "draft_revision_conflict",
                        "工作草稿已被其他保存更新",
                        details={
                            "current_revision": draft.draft_revision,
                            "current_document": draft.document_json,
                            "base_version_id": draft.base_version_id,
                        },
                    )
                draft.draft_revision += 1
                draft.document_json = validated.model_dump(mode="json")
                draft.validation_json = {"valid": True, "errors": []}
                draft.updated_by = scope.actor_id
            if name is not None:
                strategy.name = self._name(name)
            strategy.updated_at = datetime.now(UTC)
            self.repository.append_event(
                session,
                strategy_id=strategy.id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="draft_saved",
                payload={"draft_revision": draft.draft_revision},
            )
            session.flush()
            return self._strategy_payload(session, strategy, detail=True)

    def publish(self, scope: OwnerScope, strategy_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            strategy = self._require_mutable_strategy(session, strategy_id, scope)
            if strategy.lifecycle == StrategyLifecycle.ARCHIVED.value:
                raise StrategyConflict("strategy_archived", "归档策略不能发布")
            draft = self.repository.get_draft(session, strategy.id)
            if draft is None:
                raise StrategyConflict("strategy_draft_missing", "没有可发布的工作草稿")
            document = parse_strategy_document(draft.document_json)
            from research_system.strategies.runtime_pins import resolve_publication_document
            document = resolve_publication_document(document, self.runtime_settings)
            content_hash = strategy_content_hash(document)
            latest = self.repository.latest_version(session, strategy.id)
            if latest is not None and latest.content_hash == content_hash:
                session.delete(draft)
                return self._version_payload(latest)
            version = StrategyVersion(
                strategy_id=strategy.id,
                version_number=self.repository.next_version_number(session, strategy.id),
                document_json=document.model_dump(mode="json"),
                content_hash=content_hash,
                governance_state=GovernanceState.PUBLISHED.value,
                published_by=scope.actor_id,
            )
            session.add(version)
            session.flush()
            self._attach_publication_artifact(version, strategy, scope)
            session.delete(draft)
            strategy.lifecycle = StrategyLifecycle.PUBLISHED.value
            strategy.archived_at = None
            strategy.updated_at = datetime.now(UTC)
            effective = next_weekday(date.today())
            assignments = self.repository.list_assignments(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                strategy_id=strategy.id,
            )
            for assignment in assignments:
                assignment.pending_version_id = version.id
                assignment.effective_from = effective
            self.repository.append_event(
                session,
                strategy_id=strategy.id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="published",
                payload={
                    "version_id": version.id,
                    "version_number": version.version_number,
                    "content_hash": content_hash,
                    "assignment_effective_from": effective.isoformat(),
                },
            )
            session.flush()
            return self._version_payload(version)

    def clone_strategy(
        self,
        scope: OwnerScope,
        strategy_id: str,
        *,
        name: str | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            source = self._require_strategy(session, strategy_id, scope)
            source_name = source.name
        return self.create_strategy(
            scope,
            name=name or f"{source_name} 副本",
            from_strategy_id=strategy_id,
        )

    def hard_delete(self, scope: OwnerScope, strategy_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            strategy = self._require_mutable_strategy(session, strategy_id, scope)
            version_count = int(
                session.scalar(
                    select(func.count()).select_from(StrategyVersion).where(
                        StrategyVersion.strategy_id == strategy.id
                    )
                )
                or 0
            )
            if version_count:
                raise StrategyConflict("published_strategy_requires_archive", "已发布策略只能归档，不能物理删除")
            event_payload = {"name": strategy.name}
            session.execute(delete(StrategyDraft).where(StrategyDraft.strategy_id == strategy.id))
            session.delete(strategy)
            self.repository.append_event(
                session,
                strategy_id=None,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="draft_strategy_deleted",
                payload={"strategy_id": strategy_id, **event_payload},
            )
            return {"deleted": True, "strategy_id": strategy_id}

    def archive(self, scope: OwnerScope, strategy_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            strategy = self._require_mutable_strategy(session, strategy_id, scope)
            if strategy.lifecycle == StrategyLifecycle.ARCHIVED.value:
                return self._strategy_payload(session, strategy, detail=True)
            if self.repository.latest_version(session, strategy.id) is None:
                raise StrategyConflict("draft_strategy_requires_delete", "未发布草稿请直接删除")
            strategy.lifecycle = StrategyLifecycle.ARCHIVED.value
            strategy.archived_at = datetime.now(UTC)
            for assignment in self.repository.list_assignments(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                strategy_id=strategy.id,
            ):
                assignment.paused = True
            self.repository.append_event(
                session,
                strategy_id=strategy.id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="archived",
            )
            return self._strategy_payload(session, strategy, detail=True)

    def restore(self, scope: OwnerScope, strategy_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            strategy = self._require_mutable_strategy(session, strategy_id, scope)
            if strategy.lifecycle != StrategyLifecycle.ARCHIVED.value:
                return self._strategy_payload(session, strategy, detail=True)
            strategy.lifecycle = StrategyLifecycle.PUBLISHED.value
            strategy.archived_at = None
            for assignment in self.repository.list_assignments(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                strategy_id=strategy.id,
            ):
                assignment.paused = False
            self.repository.append_event(
                session,
                strategy_id=strategy.id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="restored",
            )
            return self._strategy_payload(session, strategy, detail=True)

    def list_versions(self, scope: OwnerScope, strategy_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            strategy = self._require_strategy(session, strategy_id, scope)
            return {
                "strategy_id": strategy.id,
                "versions": [
                    self._version_payload(row)
                    for row in self.repository.list_versions(session, strategy.id)
                ],
            }

    def list_events(self, scope: OwnerScope, strategy_id: str, *, limit: int = 100) -> dict[str, Any]:
        with self.database.session() as session:
            self._require_strategy(session, strategy_id, scope)
            if self.repository.get_strategy(
                session,
                strategy_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                allow_system=False,
            ) is None:
                return {"strategy_id": strategy_id, "events": []}
            rows = self.repository.list_events(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                strategy_id=strategy_id,
                limit=limit,
            )
            return {
                "strategy_id": strategy_id,
                "events": [
                    {
                        "id": row.id,
                        "strategy_id": row.strategy_id,
                        "actor_id": row.actor_id,
                        "event_type": row.event_type,
                        "payload": row.payload_json,
                        "created_at": row.created_at,
                    }
                    for row in rows
                ],
            }

    def get_version(self, scope: OwnerScope, strategy_id: str, version_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            self._require_strategy(session, strategy_id, scope)
            version = self.repository.get_version(session, strategy_id, version_id)
            if version is None:
                raise StrategyDomainError("strategy_version_not_found", "未找到指定策略版本", status_code=404)
            return self._version_payload(version, include_document=True)

    def version_diff(
        self,
        scope: OwnerScope,
        strategy_id: str,
        *,
        from_version_id: str,
        to_version_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            self._require_strategy(session, strategy_id, scope)
            before = self.repository.get_version(session, strategy_id, from_version_id)
            after = self.repository.get_version(session, strategy_id, to_version_id)
            if before is None or after is None:
                raise StrategyDomainError("strategy_version_not_found", "对比版本不存在", status_code=404)
            before_lines = canonical_strategy_json(before.document_json).splitlines()
            after_lines = canonical_strategy_json(after.document_json).splitlines()
            return {
                "strategy_id": strategy_id,
                "from": self._version_payload(before),
                "to": self._version_payload(after),
                "changes": self._deep_changes(before.document_json, after.document_json),
                "unified_diff": list(
                    difflib.unified_diff(before_lines, after_lines, fromfile=before.id, tofile=after.id)
                ),
            }

    def list_assignments(
        self,
        scope: OwnerScope,
        *,
        strategy_id: str | None = None,
        fund_code: str | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            rows = self.repository.list_assignments(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                strategy_id=strategy_id,
                fund_code=fund_code,
            )
            self._activate_pending_versions(rows)
            return {"assignments": [self._assignment_payload(row) for row in rows]}

    def create_assignment(
        self,
        scope: OwnerScope,
        *,
        strategy_id: str,
        fund_code: str,
        fund_name: str | None,
        mode: BacktestMode,
        enabled: bool = True,
        schedule: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            strategy = self._require_mutable_strategy(session, strategy_id, scope)
            if strategy.lifecycle != StrategyLifecycle.PUBLISHED.value:
                raise StrategyConflict("strategy_not_published", "只有已发布策略可以绑定基金")
            version = self.repository.latest_version(session, strategy.id)
            if version is None:
                raise StrategyConflict("strategy_not_published", "策略尚无已发布版本")
            document = parse_strategy_document(version.document_json)
            if mode not in document.report_inputs.enabled_backtest_modes:
                raise StrategyDomainError("backtest_mode_disabled", "策略版本未启用该回测模式")
            normalized_fund_code = str(fund_code or "").strip()
            if not normalized_fund_code:
                raise StrategyDomainError("fund_code_required", "基金代码不能为空")
            schedule_payload = document.schedule_defaults.model_dump(mode="json")
            if schedule is not None:
                allowed = {"enabled", "timezone", "local_time", "settlement_cutoff", "skip_non_trading_days"}
                if set(schedule) - allowed:
                    raise StrategyDomainError("assignment_override_forbidden", "Assignment 不能覆盖策略规则")
                schedule_payload.update(schedule)
            assignment = StrategyAssignment(
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                fund_code=normalized_fund_code,
                fund_name=str(fund_name or "").strip() or None,
                strategy_id=strategy.id,
                active_version_id=version.id,
                effective_from=next_weekday(date.today()),
                mode=mode.value,
                schedule_json=schedule_payload,
                enabled=enabled,
                paused=False,
            )
            session.add(assignment)
            try:
                session.flush()
            except IntegrityError as exc:
                raise StrategyConflict("assignment_exists", "该基金、策略和模式已存在 Assignment") from exc
            self.repository.append_event(
                session,
                strategy_id=strategy.id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="assignment_created",
                payload={"assignment_id": assignment.id, "fund_code": normalized_fund_code, "mode": mode.value},
            )
            return self._assignment_payload(assignment)

    def update_assignment(
        self,
        scope: OwnerScope,
        assignment_id: str,
        *,
        enabled: bool | None = None,
        paused: bool | None = None,
        schedule: dict[str, Any] | None = None,
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
            if enabled is not None:
                assignment.enabled = enabled
            if paused is not None:
                assignment.paused = paused
            if schedule is not None:
                allowed = {"enabled", "timezone", "local_time", "settlement_cutoff", "skip_non_trading_days"}
                if set(schedule) - allowed:
                    raise StrategyDomainError("assignment_override_forbidden", "Assignment 不能覆盖策略规则")
                assignment.schedule_json = {**assignment.schedule_json, **schedule}
            self.repository.append_event(
                session,
                strategy_id=assignment.strategy_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="assignment_updated",
                payload={"assignment_id": assignment.id},
            )
            return self._assignment_payload(assignment)

    def pause_assignment(self, scope: OwnerScope, assignment_id: str) -> dict[str, Any]:
        return self.update_assignment(scope, assignment_id, paused=True)

    def resume_assignment(self, scope: OwnerScope, assignment_id: str) -> dict[str, Any]:
        return self.update_assignment(scope, assignment_id, paused=False)

    def list_paper_bindings(self, scope: OwnerScope) -> dict[str, Any]:
        with self.database.session() as session:
            rows = list(
                session.scalars(
                    select(PaperStrategyBinding)
                    .where(
                        PaperStrategyBinding.tenant_id == scope.tenant_id,
                        PaperStrategyBinding.user_id == scope.user_id,
                    )
                    .order_by(PaperStrategyBinding.promoted_at.desc())
                )
            )
            return {
                "bindings": [
                    {
                        "id": row.id,
                        "account_id": row.account_id,
                        "fund_code": row.fund_code,
                        "mode": row.mode,
                        "strategy_version_id": row.strategy_version_id,
                        "promoted_by": row.promoted_by,
                        "promoted_at": row.promoted_at,
                    }
                    for row in rows
                ]
            }

    def delete_assignment(self, scope: OwnerScope, assignment_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            assignment = self.repository.get_assignment(
                session,
                assignment_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            if assignment is None:
                raise StrategyDomainError("assignment_not_found", "未找到 Assignment", status_code=404)
            signal_count = int(
                session.scalar(
                    select(func.count()).select_from(TradeSignal).where(
                        TradeSignal.assignment_id == assignment.id
                    )
                )
                or 0
            )
            if signal_count:
                assignment.enabled = False
                assignment.paused = True
                result = {"deleted": False, "disabled": True, "assignment_id": assignment.id}
            else:
                session.delete(assignment)
                result = {"deleted": True, "assignment_id": assignment.id}
            self.repository.append_event(
                session,
                strategy_id=assignment.strategy_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="assignment_deleted" if result["deleted"] else "assignment_disabled",
                payload=result,
            )
            return result

    def promote(
        self,
        scope: OwnerScope,
        *,
        strategy_id: str,
        version_id: str,
        account_id: str,
        fund_code: str,
        mode: BacktestMode,
    ) -> dict[str, Any]:
        normalized_account_id = str(account_id or "").strip()
        normalized_fund_code = str(fund_code or "").strip()
        if not normalized_account_id or len(normalized_account_id) > 128:
            raise StrategyDomainError("invalid_paper_account", "模拟账户 ID 不能为空且不能超过 128 字符")
        if not normalized_fund_code or len(normalized_fund_code) > 32:
            raise StrategyDomainError("invalid_fund_code", "基金代码不能为空且不能超过 32 字符")
        with self.database.session() as session:
            strategy = self._require_mutable_strategy(session, strategy_id, scope)
            if strategy.lifecycle == StrategyLifecycle.ARCHIVED.value:
                raise StrategyConflict("strategy_archived", "归档策略不能晋级模拟交易")
            version = self.repository.get_version(session, strategy_id, version_id)
            if version is None:
                raise StrategyDomainError("strategy_version_not_found", "策略版本不存在", status_code=404)
            assignment = session.scalar(
                select(StrategyAssignment).where(
                    StrategyAssignment.tenant_id == scope.tenant_id,
                    StrategyAssignment.user_id == scope.user_id,
                    StrategyAssignment.strategy_id == strategy_id,
                    StrategyAssignment.fund_code == normalized_fund_code,
                    StrategyAssignment.mode == mode.value,
                )
            )
            if assignment is None:
                raise StrategyConflict(
                    "strategy_assignment_missing",
                    "晋级前必须存在同基金、同模式的 Strategy Assignment",
                )
            verified = session.scalar(
                select(StrategyVerification)
                .join(
                    BacktestRun,
                    StrategyVerification.backtest_run_id == BacktestRun.id,
                )
                .where(
                    StrategyVerification.tenant_id == scope.tenant_id,
                    StrategyVerification.user_id == scope.user_id,
                    StrategyVerification.strategy_version_id == version.id,
                    StrategyVerification.status == GovernanceState.VERIFIED.value,
                    StrategyVerification.machine_passed.is_(True),
                    StrategyVerification.confirmed_at.is_not(None),
                    BacktestRun.tenant_id == scope.tenant_id,
                    BacktestRun.user_id == scope.user_id,
                    BacktestRun.strategy_version_id == version.id,
                    BacktestRun.fund_code == normalized_fund_code,
                    BacktestRun.mode == mode.value,
                    BacktestRun.status == "completed",
                )
                .order_by(StrategyVerification.confirmed_at.desc())
            )
            if (
                version.governance_state not in {
                    GovernanceState.VERIFIED.value,
                    GovernanceState.PROMOTED.value,
                }
                or verified is None
            ):
                raise StrategyConflict(
                    "strategy_version_not_verified",
                    "只有通过同基金、同模式回测验证的 Verified 版本可以晋级模拟交易",
                )
            existing = session.scalar(
                select(PaperStrategyBinding).where(
                    PaperStrategyBinding.tenant_id == scope.tenant_id,
                    PaperStrategyBinding.user_id == scope.user_id,
                    PaperStrategyBinding.account_id == normalized_account_id,
                    PaperStrategyBinding.fund_code == normalized_fund_code,
                    PaperStrategyBinding.mode == mode.value,
                )
            )
            if existing is not None and existing.strategy_version_id == version.id:
                return {
                    "binding_id": existing.id,
                    "strategy_version_id": version.id,
                    "governance_state": GovernanceState.PROMOTED.value,
                    "account_id": existing.account_id,
                    "fund_code": existing.fund_code,
                    "mode": existing.mode,
                    "idempotent_replay": True,
                }
            if existing is None:
                existing = PaperStrategyBinding(
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    account_id=normalized_account_id,
                    fund_code=normalized_fund_code,
                    mode=mode.value,
                    strategy_version_id=version.id,
                    promoted_by=scope.actor_id,
                )
                session.add(existing)
            else:
                existing.strategy_version_id = version.id
                existing.promoted_by = scope.actor_id
                existing.promoted_at = datetime.now(UTC)
            version.governance_state = GovernanceState.PROMOTED.value
            self.repository.append_event(
                session,
                strategy_id=strategy_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                actor_id=scope.actor_id,
                event_type="promoted",
                payload={
                    "version_id": version.id,
                    "account_id": normalized_account_id,
                    "fund_code": normalized_fund_code,
                    "mode": mode.value,
                },
            )
            session.flush()
            return {
                "binding_id": existing.id,
                "strategy_version_id": version.id,
                "governance_state": version.governance_state,
                "account_id": normalized_account_id,
                "fund_code": normalized_fund_code,
                "mode": mode.value,
                "idempotent_replay": False,
            }

    def _require_strategy(self, session, strategy_id: str, scope: OwnerScope) -> Strategy:
        row = self.repository.get_strategy(
            session,
            strategy_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        if row is None:
            raise StrategyNotFound()
        return row

    def _require_mutable_strategy(self, session, strategy_id: str, scope: OwnerScope) -> Strategy:
        row = self._require_strategy(session, strategy_id, scope)
        if row.is_system_template:
            raise StrategyDomainError(
                "system_template_read_only",
                "系统模板只读，请先复制为个人策略",
                status_code=403,
            )
        return row

    def _strategy_payload(self, session, row: Strategy, *, detail: bool = False) -> dict[str, Any]:
        draft = self.repository.get_draft(session, row.id)
        latest = self.repository.latest_version(session, row.id)
        version_count = int(
            session.scalar(
                select(func.count()).select_from(StrategyVersion).where(
                    StrategyVersion.strategy_id == row.id
                )
            )
            or 0
        )
        assignment_count = int(
            session.scalar(
                select(func.count()).select_from(StrategyAssignment).where(
                    StrategyAssignment.strategy_id == row.id
                )
            )
            or 0
        )
        payload: dict[str, Any] = {
            "id": row.id,
            "name": row.name,
            "kind": row.kind,
            "system_key": row.system_key,
            "is_system_template": row.is_system_template,
            "lifecycle": row.lifecycle,
            "has_draft": draft is not None,
            "draft_revision": draft.draft_revision if draft else 0,
            "version_count": version_count,
            "assignment_count": assignment_count,
            "latest_version": self._version_payload(latest) if latest else None,
            "archived_at": row.archived_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        if detail:
            payload["draft"] = self._draft_payload(draft) if draft else None
            payload["versions"] = [
                self._version_payload(version)
                for version in self.repository.list_versions(session, row.id)
            ]
            payload["assignments"] = [
                self._assignment_payload(assignment)
                for assignment in self.repository.list_assignments(
                    session,
                    tenant_id=row.tenant_id,
                    user_id=row.user_id,
                    strategy_id=row.id,
                )
            ] if not row.is_system_template else []
        return payload

    @staticmethod
    def _draft_payload(row: StrategyDraft) -> dict[str, Any]:
        return {
            "strategy_id": row.strategy_id,
            "base_version_id": row.base_version_id,
            "draft_revision": row.draft_revision,
            "document": row.document_json,
            "validation": row.validation_json,
            "updated_by": row.updated_by,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _version_payload(row: StrategyVersion | None, *, include_document: bool = False) -> dict[str, Any] | None:
        if row is None:
            return None
        payload: dict[str, Any] = {
            "id": row.id,
            "strategy_id": row.strategy_id,
            "version_number": row.version_number,
            "content_hash": row.content_hash,
            "governance_state": row.governance_state,
            "artifact_id": row.artifact_id,
            "artifact_run_id": row.artifact_run_id,
            "published_at": row.published_at,
            "published_by": row.published_by,
            "research_profile": str(
                (dict(row.document_json or {}).get("report_inputs") or {}).get(
                    "research_profile", "deep"
                )
            ),
            "schema_version": str((dict(row.document_json or {}).get("metadata") or {}).get("schema_version") or "1"),
            "signal_model": str((dict(row.document_json or {}).get("report_signal") or {}).get("model") or "legacy_fund_composite"),
        }
        if include_document:
            payload["document"] = row.document_json
        return payload

    @staticmethod
    def _assignment_payload(row: StrategyAssignment) -> dict[str, Any]:
        return {
            "id": row.id,
            "fund_code": row.fund_code,
            "fund_name": row.fund_name,
            "strategy_id": row.strategy_id,
            "active_version_id": row.active_version_id,
            "pending_version_id": row.pending_version_id,
            "effective_from": row.effective_from,
            "mode": row.mode,
            "schedule": row.schedule_json,
            "enabled": row.enabled,
            "paused": row.paused,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _activate_pending_versions(rows: list[StrategyAssignment]) -> None:
        today = date.today()
        for assignment in rows:
            if assignment.pending_version_id and assignment.effective_from <= today:
                assignment.active_version_id = assignment.pending_version_id
                assignment.pending_version_id = None

    def _attach_publication_artifact(
        self,
        version: StrategyVersion,
        strategy: Strategy,
        scope: OwnerScope,
    ) -> None:
        run_id = generate_ulid()
        request_id = generate_ulid()
        root = self.workspace_root / "strategy_publications" / run_id
        root.mkdir(parents=True, exist_ok=True)
        artifact = ArtifactBoard(root / "artifact_board.jsonl").add(
            ArtifactRecord(
                artifact_type="strategy_version_snapshot",
                schema_version="1",
                run_id=run_id,
                thread_id=f"strategy:{strategy.id}",
                request_id=request_id,
                user_id=scope.user_id,
                tenant_id=scope.tenant_id,
                producer=ArtifactProducer(
                    executor="strategy_publication_service",
                    capability="finance.strategy.publish",
                    node_id="strategy-version-snapshot",
                    domain_id="finance_ashare",
                ),
                content={
                    "strategy_id": strategy.id,
                    "strategy_name": strategy.name,
                    "version_id": version.id,
                    "version_number": version.version_number,
                    "content_hash": version.content_hash,
                    "document": version.document_json,
                },
                confidence=1.0,
                metadata={"immutable": True, "system_template": strategy.is_system_template},
            )
        )
        version.artifact_id = artifact.artifact_id
        version.artifact_run_id = run_id

    @staticmethod
    def _deep_changes(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
        if isinstance(before, dict) and isinstance(after, dict):
            changes: list[dict[str, Any]] = []
            for key in sorted(set(before) | set(after)):
                child = f"{path}.{key}" if path else key
                if key not in before:
                    changes.append({"path": child, "type": "added", "after": after[key]})
                elif key not in after:
                    changes.append({"path": child, "type": "removed", "before": before[key]})
                else:
                    changes.extend(StrategyService._deep_changes(before[key], after[key], child))
            return changes
        if before != after:
            return [{"path": path, "type": "changed", "before": before, "after": after}]
        return []

    @staticmethod
    def _name(value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise StrategyDomainError("strategy_name_required", "策略名称不能为空")
        if len(normalized) > 200:
            raise StrategyDomainError("strategy_name_too_long", "策略名称不能超过 200 个字符")
        return normalized


__all__ = ["OwnerScope", "StrategyService", "owner_scope"]
