from __future__ import annotations

from research_system.schemas.planning import (
    ActionPlanPatch,
    ActionPlanPatchOperation,
    ActionSpec,
    SupervisorDecision,
)


class SupervisorDecisionFactory:
    def pending_by_id(self, pending_actions: list[ActionSpec]) -> dict[str, ActionSpec]:
        return {action.node_id: action for action in pending_actions}

    def planning_context_hints(self, plan) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in dict((plan.dispatch_trace or {}).get("context_hints") or {}).items()
            if str(key).strip() and str(value).strip()
        }

    def continue_with(
        self,
        *,
        action: ActionSpec,
        completed_actions: list[str],
        notes: list[str] | None = None,
        plan_patch: ActionPlanPatch | None = None,
    ) -> SupervisorDecision:
        return SupervisorDecision(
            decision_type="continue",
            next_actions=[action],
            completed_action_ids=completed_actions,
            notes=list(notes or []),
            plan_patch=plan_patch,
        )

    def stop_with(
        self,
        *,
        completed_actions: list[str],
        notes: list[str] | None = None,
        plan_patch: ActionPlanPatch | None = None,
    ) -> SupervisorDecision:
        return SupervisorDecision(
            decision_type="stop",
            completed_action_ids=completed_actions,
            notes=list(notes or []),
            plan_patch=plan_patch,
        )

    def request_replan(
        self,
        *,
        completed_actions: list[str],
        reason: str,
        notes: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> SupervisorDecision:
        patch = self.build_patch(
            notes=list(notes or ["request_action_plan_replan"]),
            operations=[
                ActionPlanPatchOperation(
                    op_type="request_replan",
                    reason=reason,
                    metadata={str(key): str(value) for key, value in dict(metadata or {}).items()},
                )
            ],
            policy_source="rule",
        )
        assert patch is not None
        patch.decision_type = "replan"
        patch.termination_reason = reason
        return SupervisorDecision(
            decision_type="replan",
            completed_action_ids=completed_actions,
            notes=list(notes or [reason]),
            plan_patch=patch,
        )

    def build_patch(
        self,
        *,
        notes: list[str],
        operations: list[ActionPlanPatchOperation],
        confidence: float = 1.0,
        policy_source: str = "rule",
    ) -> ActionPlanPatch | None:
        if not operations:
            return None
        return ActionPlanPatch(
            policy_source=policy_source,
            confidence=confidence,
            notes=list(notes),
            operations=list(operations),
        )

    @staticmethod
    def insert_action(*, action: ActionSpec, reason: str) -> ActionPlanPatchOperation:
        return ActionPlanPatchOperation(
            op_type="insert_action",
            action=action,
            reason=reason,
        )

    @staticmethod
    def rewire_dependency(
        *,
        target_node_id: str,
        new_depends_on: list[str] | None = None,
        remove_depends_on: list[str] | None = None,
        reason: str,
    ) -> ActionPlanPatchOperation:
        return ActionPlanPatchOperation(
            op_type="rewire_dependency",
            target_node_id=target_node_id,
            new_depends_on=list(new_depends_on or []),
            remove_depends_on=list(remove_depends_on or []),
            reason=reason,
        )

    def screen_review_action(self, *, plan, existing: ActionSpec | None) -> ActionSpec:
        if existing is not None:
            return existing
        planning_context_hints = self.planning_context_hints(plan)
        depends_on = ["screen_enrich"] if any(action.node_id == "screen_enrich" for action in plan.actions) else ["screen"]
        return ActionSpec(
            node_id="screen_review",
            phase="review",
            agent="screen_reviewer",
            objective="Review screened candidates before retrieval/reporting.",
            inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
            tags=["dynamic_inserted"],
            depends_on=depends_on,
            max_retries=1,
            cache_scope="run",
            cache_key="stock_screen:screen_review",
        )

    def screen_enrich_action(self, *, plan, existing: ActionSpec | None) -> ActionSpec:
        if existing is not None:
            return existing
        planning_context_hints = self.planning_context_hints(plan)
        return ActionSpec(
            node_id="screen_enrich",
            phase="screen",
            agent="screener",
            objective="Enrich top screening candidates and rerank screening outputs.",
            inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
            tags=["dynamic_inserted", "enrichment"],
            depends_on=["screen"],
            max_retries=1,
            failure_mode="isolate",
            cache_scope="run",
            cache_key="stock_screen:screen_enrich",
        )

    def review_action(self, *, plan, existing: ActionSpec | None) -> ActionSpec:
        if existing is not None:
            return existing
        planning_context_hints = self.planning_context_hints(plan)
        return ActionSpec(
            node_id="review",
            phase="review",
            agent="reviewer",
            objective="Run posterior review after verifying source context and snapshots.",
            inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
            tags=["dynamic_inserted"],
            depends_on=["collect"],
            max_retries=1,
            cache_scope="run",
            cache_key="review_thesis:review",
        )

    def stock_check_replenish_action(
        self,
        *,
        query: str,
        symbol: str,
        missing_snapshot_types: list[str],
    ) -> ActionSpec:
        return ActionSpec(
            node_id=f"collect_replenish_{'_'.join(missing_snapshot_types)}",
            phase="collect",
            agent="collector",
            objective="Replenish missing stock snapshots before retrieval.",
            inputs={
                "query": query,
                "symbol": symbol,
                "snapshot_types": missing_snapshot_types,
                "source_type": "stock_check_replenish",
            },
            tags=["dynamic_inserted", "replenish"],
            depends_on=["collect"],
            max_retries=1,
            failure_mode="isolate",
            cache_scope="run",
            cache_key=f"stock_check:replenish:{'_'.join(missing_snapshot_types)}",
        )

    def sector_replenish_action(
        self,
        *,
        query: str,
        missing_sector_evidence: list[str],
    ) -> ActionSpec:
        return ActionSpec(
            node_id=f"collect_replenish_sector_{'_'.join(missing_sector_evidence)}",
            phase="collect",
            agent="collector",
            objective="Replenish missing sector evidence before retrieval.",
            inputs={
                "query": query,
                "source_type": "sector_research_replenish",
                "replenish_sector_snapshot": True,
                "missing_evidence": missing_sector_evidence,
            },
            tags=["dynamic_inserted", "replenish"],
            depends_on=["collect"],
            max_retries=1,
            failure_mode="isolate",
            cache_scope="run",
            cache_key=f"sector_research:replenish:{'_'.join(missing_sector_evidence)}",
        )
