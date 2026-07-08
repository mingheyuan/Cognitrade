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
from research_system.schemas.planning import ActionPlan, ActionPlanPatch
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

    @staticmethod
    def _unique_notes(notes: list[str]) -> list[str]:
        unique: list[str] = []
        for note in notes:
            normalized = str(note or "").strip()
            if not normalized or normalized in unique:
                continue
            unique.append(normalized)
        return unique
