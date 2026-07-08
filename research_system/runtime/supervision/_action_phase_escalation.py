from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.runtime import BlackboardEntry


@dataclass(slots=True)
class ActionPhaseReplanRequest:
    reason: str
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ActionPhaseEscalationRequest:
    action: ActionSpec
    action_notes: list[str] = field(default_factory=list)
    replan: ActionPhaseReplanRequest | None = None


class ActionPhaseEscalationPolicy(ABC):
    intent: str | None = None

    def __init__(self, *, blackboard_view, decision_factory) -> None:
        self.blackboard_view = blackboard_view
        self.decision_factory = decision_factory

    @abstractmethod
    def maybe_escalate(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> ActionPhaseEscalationRequest | None:
        raise NotImplementedError

    def pending_by_id(self, pending_actions: list[ActionSpec]) -> dict[str, ActionSpec]:
        return self.decision_factory.pending_by_id(pending_actions)

    @staticmethod
    def plan_node_ids(plan: ActionPlan) -> set[str]:
        return {action.node_id for action in plan.actions}

    @staticmethod
    def replan_request(
        *,
        reason: str,
        notes: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ActionPhaseReplanRequest:
        return ActionPhaseReplanRequest(
            reason=reason,
            notes=list(notes or [reason]),
            metadata={str(key): str(value) for key, value in dict(metadata or {}).items()},
        )

    @staticmethod
    def escalation_request(
        *,
        action: ActionSpec,
        action_notes: list[str] | None = None,
        replan: ActionPhaseReplanRequest | None = None,
    ) -> ActionPhaseEscalationRequest:
        return ActionPhaseEscalationRequest(
            action=action,
            action_notes=list(action_notes or []),
            replan=replan,
        )


class StockScreenEscalationPolicy(ActionPhaseEscalationPolicy):
    intent = "stock_screen"

    def maybe_escalate(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> ActionPhaseEscalationRequest | None:
        pending_by_id = self.pending_by_id(pending_actions)
        view = self.blackboard_view.stock_screen_view(blackboard_entries)
        plan_node_ids = self.plan_node_ids(plan)

        if (
            "screen" in completed_actions
            and view.enrichment_pending
            and view.enrichment_target_count > 0
            and ("screen_enrich" not in completed_actions)
        ):
            enrich_action = self.decision_factory.screen_enrich_action(
                plan=plan,
                existing=pending_by_id.get("screen_enrich"),
            )
            include_screen_review = bool(
                not view.review_summary_present
                and (view.top_candidate_count > 0 or view.watchlist_candidate_count > 0)
            )
            needs_replan = (
                "screen_enrich" not in plan_node_ids
                or (include_screen_review and "screen_review" not in plan_node_ids)
            )
            return self.escalation_request(
                action=enrich_action,
                action_notes=["run screening enrichment before downstream review/retrieval"],
                replan=self.replan_request(
                    reason="stock_screen follow-up action graph should be rebuilt after screening",
                    notes=["replan_screen_follow_up_action_graph_after_screening"],
                    metadata={
                        "trigger": "screen_completed",
                        "include_screen_enrich": "true",
                        "include_screen_review": str(include_screen_review).lower(),
                    },
                )
                if needs_replan
                else None,
            )

        if (
            "screen" in completed_actions
            and not view.review_summary_present
            and not view.enrichment_pending
            and ("screen_review" not in completed_actions)
            and (view.top_candidate_count > 0 or view.watchlist_candidate_count > 0)
        ):
            review_action = self.decision_factory.screen_review_action(
                plan=plan,
                existing=pending_by_id.get("screen_review"),
            )
            needs_replan = "screen_review" not in plan_node_ids
            return self.escalation_request(
                action=review_action,
                action_notes=["run screen reviewer after screening"],
                replan=self.replan_request(
                    reason="stock_screen review action graph should be rebuilt after screening",
                    notes=["replan_screen_review_action_graph_after_screening"],
                    metadata={
                        "trigger": "screen_completed",
                        "include_screen_review": "true",
                    },
                )
                if needs_replan
                else None,
            )
        return None


class StockCheckEscalationPolicy(ActionPhaseEscalationPolicy):
    intent = "stock_check"

    def maybe_escalate(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> ActionPhaseEscalationRequest | None:
        pending_by_id = self.pending_by_id(pending_actions)
        view = self.blackboard_view.stock_check_view(blackboard_entries)
        plan_node_ids = self.plan_node_ids(plan)

        if "collect" not in completed_actions or not view.missing_snapshot_types:
            return None
        replenish_action = self.decision_factory.stock_check_replenish_action(
            query=view.request_query,
            symbol=view.symbol,
            missing_snapshot_types=view.missing_snapshot_types,
        )
        if replenish_action.node_id in completed_actions:
            return None
        needs_replan = replenish_action.node_id not in plan_node_ids
        return self.escalation_request(
            action=pending_by_id.get(replenish_action.node_id, replenish_action),
            action_notes=[f"replenish missing stock snapshots: {','.join(view.missing_snapshot_types)}"],
            replan=self.replan_request(
                reason="stock_check action graph should be rebuilt for missing stock snapshots",
                notes=["replan_stock_check_action_graph_for_missing_snapshots"],
                metadata={
                    "trigger": "collect_completed",
                    "missing_snapshot_types": ",".join(view.missing_snapshot_types),
                },
            )
            if needs_replan
            else None,
        )


class SectorResearchEscalationPolicy(ActionPhaseEscalationPolicy):
    intent = "sector_research"

    def maybe_escalate(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> ActionPhaseEscalationRequest | None:
        pending_by_id = self.pending_by_id(pending_actions)
        view = self.blackboard_view.sector_research_view(blackboard_entries)
        plan_node_ids = self.plan_node_ids(plan)

        if "collect" not in completed_actions or not view.missing_sector_evidence or not view.request_query:
            return None
        replenish_action = self.decision_factory.sector_replenish_action(
            query=view.request_query,
            missing_sector_evidence=view.missing_sector_evidence,
        )
        if replenish_action.node_id in completed_actions:
            return None
        needs_replan = replenish_action.node_id not in plan_node_ids
        return self.escalation_request(
            action=pending_by_id.get(replenish_action.node_id, replenish_action),
            action_notes=[f"replenish missing sector evidence: {','.join(view.missing_sector_evidence)}"],
            replan=self.replan_request(
                reason="sector_research action graph should be rebuilt for missing sector evidence",
                notes=["replan_sector_research_action_graph_for_missing_sector_evidence"],
                metadata={
                    "trigger": "collect_completed",
                    "missing_sector_evidence": ",".join(view.missing_sector_evidence),
                },
            )
            if needs_replan
            else None,
        )


class ReviewThesisEscalationPolicy(ActionPhaseEscalationPolicy):
    intent = "review_thesis"

    def maybe_escalate(
        self,
        *,
        plan: ActionPlan,
        completed_actions: list[str],
        pending_actions: list[ActionSpec],
        blackboard_entries: list[BlackboardEntry],
    ) -> ActionPhaseEscalationRequest | None:
        pending_by_id = self.pending_by_id(pending_actions)
        view = self.blackboard_view.review_thesis_view(blackboard_entries)
        plan_node_ids = self.plan_node_ids(plan)

        if (
            "collect" not in completed_actions
            or view.review_summary_present
            or ("review" in completed_actions)
            or not view.needs_review
        ):
            return None
        review_action = self.decision_factory.review_action(
            plan=plan,
            existing=pending_by_id.get("review"),
        )
        needs_replan = "review" not in plan_node_ids
        return self.escalation_request(
            action=review_action,
            action_notes=["run posterior review once review target and snapshots are ready"],
            replan=self.replan_request(
                reason="review_thesis action graph should be rebuilt once review context is ready",
                notes=["replan_review_action_graph_after_collection"],
                metadata={
                    "trigger": "collect_completed",
                    "include_review": "true",
                },
            )
            if needs_replan
            else None,
        )


__all__ = [
    "ActionPhaseEscalationPolicy",
    "ActionPhaseEscalationRequest",
    "ActionPhaseReplanRequest",
    "ReviewThesisEscalationPolicy",
    "SectorResearchEscalationPolicy",
    "StockCheckEscalationPolicy",
    "StockScreenEscalationPolicy",
]
