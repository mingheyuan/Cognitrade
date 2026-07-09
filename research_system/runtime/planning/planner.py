from __future__ import annotations

from typing import Any

from research_system.runtime.local_runtime import bind_local_runtime
from research_system.runtime.platform.settings import Settings
from research_system.runtime.planning.action_factory import PlannerActionFactory
from research_system.runtime.planning.context_hints import PlannerContextHints
from research_system.runtime.planning.dynamic_plan_support import PlannerDynamicPlanSupport
from research_system.runtime.planning.entry_decision_support import PlannerEntryDecisionSupport
from research_system.runtime.planning.static_plan_support import PlannerStaticPlanSupport
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.runtime.supervision.blackboard_view import SupervisorBlackboardView
from research_system.schemas.planning import ActionPlan, ActionPlanPatch, ActionSpec
from research_system.schemas.protocol import RequestEnvelope


class RuntimePlanner:
    def __init__(self, llm_provider: OpenAIProvider | None = None, settings: Settings | None = None) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
        self.context_hints = PlannerContextHints()
        self.action_factory = PlannerActionFactory()
        self.entry_decision = PlannerEntryDecisionSupport(self)
        self.dynamic_support = PlannerDynamicPlanSupport(self)
        self.static_support = PlannerStaticPlanSupport(self)
        self.blackboard_view = SupervisorBlackboardView()
        self._runtime_binding: dict[str, Any] | None = None

    def build_initial_plan(self, *, intent: str, request: RequestEnvelope) -> ActionPlan:
        if not self._runtime_binding:
            with bind_local_runtime(
                self,
                request=request,
                intent=intent,
                phase="planning",
                runs_root=self._runs_root(),
                run_timeout_seconds=self._run_timeout_seconds(),
                max_llm_calls_per_turn=self._max_llm_calls_per_turn(),
                max_subagent_calls_per_turn=self._max_subagent_calls_per_turn(),
                max_child_threads_per_turn=self._max_child_threads_per_turn(),
            ):
                return self.build_initial_plan(intent=intent, request=request)
        context_hints = self.context_hints.extract(request)
        planning_context_hints = self.context_hints.planning_inputs(context_hints)
        if intent in {"stock_check", "stock_compare", "stock_screen", "sector_research"}:
            return self.dynamic_support.build_dynamic_research_plan(
                intent=intent,
                request=request,
                context_hints=context_hints,
                planning_context_hints=planning_context_hints,
            )
        plan = self.static_support.build_static_plan(
            intent=intent,
            request=request,
            context_hints=context_hints,
            planning_context_hints=planning_context_hints,
        )
        if plan is not None:
            return plan
        raise ValueError(f"unsupported intent for planner: {intent}")

    def replan_action_plan(
        self,
        *,
        intent: str,
        request: RequestEnvelope,
        current_plan: ActionPlan,
        plan_patch: ActionPlanPatch,
    ) -> ActionPlan:
        if not self._runtime_binding:
            with bind_local_runtime(
                self,
                request=request,
                intent=intent,
                phase="planning",
                runs_root=self._runs_root(),
                run_timeout_seconds=self._run_timeout_seconds(),
                max_llm_calls_per_turn=self._max_llm_calls_per_turn(),
                max_subagent_calls_per_turn=self._max_subagent_calls_per_turn(),
                max_child_threads_per_turn=self._max_child_threads_per_turn(),
            ):
                return self.replan_action_plan(
                    intent=intent,
                    request=request,
                    current_plan=current_plan,
                    plan_patch=plan_patch,
                )
        context_hints = self.context_hints.extract(request)
        planning_context_hints = self.context_hints.planning_inputs(context_hints)
        runtime_blackboard_entries = self._runtime_blackboard_entries()
        replanned = None
        if intent == "stock_screen" and runtime_blackboard_entries:
            replanned = self._replan_stock_screen_action_plan(
                current_plan=current_plan,
                plan_patch=plan_patch,
                context_hints=context_hints,
                planning_context_hints=planning_context_hints,
                blackboard_entries=runtime_blackboard_entries,
            )
        elif intent == "stock_check" and runtime_blackboard_entries:
            replanned = self._replan_stock_check_action_plan(
                current_plan=current_plan,
                plan_patch=plan_patch,
                context_hints=context_hints,
                planning_context_hints=planning_context_hints,
                blackboard_entries=runtime_blackboard_entries,
            )
        elif intent == "sector_research" and runtime_blackboard_entries:
            replanned = self._replan_sector_research_action_plan(
                current_plan=current_plan,
                plan_patch=plan_patch,
                context_hints=context_hints,
                planning_context_hints=planning_context_hints,
                blackboard_entries=runtime_blackboard_entries,
            )
        elif intent == "review_thesis" and runtime_blackboard_entries:
            replanned = self._replan_review_thesis_action_plan(
                current_plan=current_plan,
                plan_patch=plan_patch,
                context_hints=context_hints,
                planning_context_hints=planning_context_hints,
                blackboard_entries=runtime_blackboard_entries,
            )
        if replanned is None:
            replanned = self.build_initial_plan(intent=intent, request=request)
        dispatch_trace = dict(replanned.dispatch_trace or {})
        dispatch_trace["replanned_from_plan_id"] = current_plan.plan_id
        dispatch_trace["replan_patch_id"] = plan_patch.patch_id
        dispatch_trace["replan_policy_source"] = plan_patch.policy_source
        if plan_patch.termination_reason:
            dispatch_trace["replan_reason"] = plan_patch.termination_reason
        notes = self._unique_notes(
            [
                *list(replanned.notes or []),
                *list(plan_patch.notes or []),
                f"replanned_from:{current_plan.plan_id}",
            ]
        )
        return replanned.model_copy(
            update={
                "notes": notes,
                "dispatch_trace": dispatch_trace,
            },
            deep=True,
        )

    def _decide_research_entry(self, *, intent: str, request: RequestEnvelope):
        return self.entry_decision.decide_research_entry(intent=intent, request=request)

    def bind_runtime(
        self,
        *,
        context,
        support,
        request_id: str,
        intent: str,
    ) -> None:
        self._runtime_binding = {
            "context": context,
            "support": support,
            "request_id": request_id,
            "intent": intent,
        }

    def clear_runtime(self) -> None:
        self._runtime_binding = None

    def _run_timeout_seconds(self) -> int:
        if self.settings is None:
            return 120
        return int(self.settings.run_timeout_seconds)

    def _runs_root(self):
        if self.settings is None:
            return None
        return self.settings.runs_root

    def _max_llm_calls_per_turn(self) -> int:
        if self.settings is None:
            return 1
        return int(self.settings.max_llm_calls_per_turn)

    def _max_subagent_calls_per_turn(self) -> int:
        if self.settings is None:
            return 32
        return int(self.settings.max_subagent_calls_per_turn)

    def _max_child_threads_per_turn(self) -> int:
        if self.settings is None:
            return 8
        return int(self.settings.max_child_threads_per_turn)

    def _runtime_blackboard_entries(self) -> list[Any]:
        binding = self._runtime_binding or {}
        context = binding.get("context")
        if context is None:
            return []
        return list(context.blackboard.list_entries(thread_id=context.thread.thread_id))

    def _replan_stock_screen_action_plan(
        self,
        *,
        current_plan: ActionPlan,
        plan_patch: ActionPlanPatch,
        context_hints: dict[str, str],
        planning_context_hints: dict[str, str],
        blackboard_entries: list[Any],
    ) -> ActionPlan:
        view = self.blackboard_view.stock_screen_view(blackboard_entries)
        gap_metadata = self._subagent_capability_gap_metadata(plan_patch)
        review_capability_gap = gap_metadata.get("stage") == "review"
        action_ids = ["collect", "screen"]
        if view.enrichment_pending and view.enrichment_target_count > 0:
            action_ids.append("screen_enrich")
        if (
            not review_capability_gap
            and (
                not view.review_summary_present
                and (view.top_candidate_count > 0 or view.watchlist_candidate_count > 0)
            )
        ):
            action_ids.append("screen_review")
        action_ids.extend(["retrieve", "report"])
        actions = self.action_factory.build_entry_actions(
            intent="stock_screen",
            action_ids=action_ids,
            planning_context_hints=planning_context_hints,
        )
        reason = str(
            plan_patch.termination_reason
            or current_plan.entry_reason
            or "stock_screen action graph replanned from runtime evidence"
        )
        notes = ["stock_screen action graph replanned from runtime evidence"]
        if review_capability_gap:
            notes.append("stock_screen_review_skipped_for_capability_gap")
        return self.action_factory.build_research_plan(
            intent="stock_screen",
            actions=actions,
            notes=notes,
            entry_strategy=current_plan.entry_strategy,
            entry_reason=self.context_hints.merge_entry_reason(reason, context_hints),
            subagent_required=current_plan.subagent_required,
            context_hints=context_hints,
            initial_actions=action_ids,
            reason=reason,
            strategy="dynamic",
        )

    def _replan_stock_check_action_plan(
        self,
        *,
        current_plan: ActionPlan,
        plan_patch: ActionPlanPatch,
        context_hints: dict[str, str],
        planning_context_hints: dict[str, str],
        blackboard_entries: list[Any],
    ) -> ActionPlan:
        view = self.blackboard_view.stock_check_view(blackboard_entries)
        base_actions = self.action_factory.build_entry_actions(
            intent="stock_check",
            action_ids=["collect", "retrieve", "report"],
            planning_context_hints=planning_context_hints,
        )
        actions = [action.model_copy(deep=True) for action in base_actions]
        if view.missing_snapshot_types:
            replenish_node_id = f"collect_replenish_{'_'.join(view.missing_snapshot_types)}"
            replenish_action = ActionSpec(
                node_id=replenish_node_id,
                phase="collect",
                agent="collector",
                objective="Replenish missing stock snapshots before retrieval.",
                inputs={
                    "query": view.request_query,
                    "symbol": view.symbol,
                    "snapshot_types": list(view.missing_snapshot_types),
                    "source_type": "stock_check_replenish",
                },
                tags=["dynamic_inserted", "replenish"],
                depends_on=["collect"],
                max_retries=1,
                failure_mode="isolate",
                cache_scope="run",
                cache_key=f"stock_check:replenish:{'_'.join(view.missing_snapshot_types)}",
            )
            actions = [
                actions[0],
                replenish_action,
                actions[1].model_copy(update={"depends_on": [replenish_node_id]}, deep=True),
                actions[2].model_copy(update={"depends_on": ["retrieve"]}, deep=True),
            ]
        reason = str(
            plan_patch.termination_reason
            or current_plan.entry_reason
            or "stock_check action graph replanned from runtime evidence"
        )
        return self.action_factory.build_research_plan(
            intent="stock_check",
            actions=actions,
            notes=["stock_check action graph replanned from runtime evidence"],
            entry_strategy=current_plan.entry_strategy,
            entry_reason=self.context_hints.merge_entry_reason(reason, context_hints),
            subagent_required=current_plan.subagent_required,
            context_hints=context_hints,
            initial_actions=[action.node_id for action in actions],
            reason=reason,
            strategy="dynamic",
        )

    def _replan_sector_research_action_plan(
        self,
        *,
        current_plan: ActionPlan,
        plan_patch: ActionPlanPatch,
        context_hints: dict[str, str],
        planning_context_hints: dict[str, str],
        blackboard_entries: list[Any],
    ) -> ActionPlan:
        view = self.blackboard_view.sector_research_view(blackboard_entries)
        base_actions = self.action_factory.build_entry_actions(
            intent="sector_research",
            action_ids=["collect", "retrieve", "report"],
            planning_context_hints=planning_context_hints,
        )
        actions = [action.model_copy(deep=True) for action in base_actions]
        if view.missing_sector_evidence and view.request_query:
            replenish_node_id = f"collect_replenish_sector_{'_'.join(view.missing_sector_evidence)}"
            replenish_action = ActionSpec(
                node_id=replenish_node_id,
                phase="collect",
                agent="collector",
                objective="Replenish missing sector evidence before retrieval.",
                inputs={
                    "query": view.request_query,
                    "source_type": "sector_research_replenish",
                    "replenish_sector_snapshot": True,
                    "missing_evidence": list(view.missing_sector_evidence),
                },
                tags=["dynamic_inserted", "replenish"],
                depends_on=["collect"],
                max_retries=1,
                failure_mode="isolate",
                cache_scope="run",
                cache_key=f"sector_research:replenish:{'_'.join(view.missing_sector_evidence)}",
            )
            actions = [
                actions[0],
                replenish_action,
                actions[1].model_copy(update={"depends_on": [replenish_node_id]}, deep=True),
                actions[2].model_copy(update={"depends_on": ["retrieve"]}, deep=True),
            ]
        reason = str(
            plan_patch.termination_reason
            or current_plan.entry_reason
            or "sector_research action graph replanned from runtime evidence"
        )
        return self.action_factory.build_research_plan(
            intent="sector_research",
            actions=actions,
            notes=["sector_research action graph replanned from runtime evidence"],
            entry_strategy=current_plan.entry_strategy,
            entry_reason=self.context_hints.merge_entry_reason(reason, context_hints),
            subagent_required=current_plan.subagent_required,
            context_hints=context_hints,
            initial_actions=[action.node_id for action in actions],
            reason=reason,
            strategy="dynamic",
        )

    def _replan_review_thesis_action_plan(
        self,
        *,
        current_plan: ActionPlan,
        plan_patch: ActionPlanPatch,
        context_hints: dict[str, str],
        planning_context_hints: dict[str, str],
        blackboard_entries: list[Any],
    ) -> ActionPlan:
        view = self.blackboard_view.review_thesis_view(blackboard_entries)
        gap_metadata = self._subagent_capability_gap_metadata(plan_patch)
        review_capability_gap = gap_metadata.get("stage") == "review"
        action_ids = ["collect"]
        if view.needs_review and not view.review_summary_present and not review_capability_gap:
            action_ids.append("review")
        action_ids.extend(["retrieve", "report"])
        actions = self.action_factory.build_entry_actions(
            intent="review_thesis",
            action_ids=action_ids,
            planning_context_hints=planning_context_hints,
        )
        reason = str(
            plan_patch.termination_reason
            or current_plan.entry_reason
            or "review_thesis action graph replanned from runtime evidence"
        )
        notes = ["review_thesis action graph replanned from runtime evidence"]
        if review_capability_gap:
            notes.append("review_phase_skipped_for_capability_gap")
        return self.action_factory.build_research_plan(
            intent="review_thesis",
            actions=actions,
            notes=notes,
            entry_strategy=current_plan.entry_strategy,
            entry_reason=self.context_hints.merge_entry_reason(reason, context_hints),
            subagent_required=current_plan.subagent_required,
            context_hints=context_hints,
            initial_actions=action_ids,
            reason=reason,
            strategy="dynamic",
        )

    @staticmethod
    def _unique_notes(notes: list[str]) -> list[str]:
        unique: list[str] = []
        for note in notes:
            normalized = str(note or "").strip()
            if not normalized or normalized in unique:
                continue
            unique.append(normalized)
        return unique

    @staticmethod
    def _subagent_capability_gap_metadata(plan_patch: ActionPlanPatch) -> dict[str, str]:
        for operation in list(plan_patch.operations or []):
            if operation.op_type != "request_replan":
                continue
            metadata = {
                str(key): str(value)
                for key, value in dict(operation.metadata or {}).items()
                if str(key).strip() and str(value).strip()
            }
            if metadata.get("control_source") == "subagent_capability_gap":
                return metadata
        return {}
