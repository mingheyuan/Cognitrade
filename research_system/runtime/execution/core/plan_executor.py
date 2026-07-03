from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope


class OrchestratorPlanExecutor:
    def __init__(self, host: Any) -> None:
        self.host = host

    def execute_plan(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
    ) -> None:
        pending_actions: list[ActionSpec] = [action.model_copy(deep=True) for action in plan.actions]
        executed_node_ids: list[str] = []

        while True:
            self.host._enforce_runtime_controls(context, request_id=request.request_id, intent=plan.intent)
            decision = self.host.supervisor.decide(
                plan=plan,
                completed_actions=executed_node_ids[:],
                pending_actions=pending_actions[:],
                blackboard_entries=context.blackboard.list_entries(),
            )
            self._emit_supervisor_decision(
                context=context,
                request=request,
                plan=plan,
                executed_node_ids=executed_node_ids,
                next_actions=decision.next_actions,
                notes=decision.notes,
            )
            if decision.decision_type == "stop" or not decision.next_actions:
                self.host._update_state(
                    context,
                    pending_nodes=[],
                    completed_nodes=executed_node_ids[:],
                )
                context.checkpoint_store.save(context.state)
                return

            for action in decision.next_actions:
                self._insert_dynamic_action_if_missing(
                    context=context,
                    request=request,
                    plan=plan,
                    pending_actions=pending_actions,
                    action=action,
                )
                current_action = self._pop_pending_action(pending_actions, action)
                self.host._run_action(
                    context=context,
                    request=request,
                    plan=plan,
                    intent=plan.intent,
                    action=current_action,
                    completed_node_ids=executed_node_ids[:],
                    pending_actions_after_current=pending_actions[:],
                )
                executed_node_ids.append(current_action.node_id)

    def _emit_supervisor_decision(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        executed_node_ids: list[str],
        next_actions: list[ActionSpec],
        notes: list[str],
    ) -> None:
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="supervisor_decision",
            message="Supervisor evaluated next actions",
            payload={
                "completed_actions": executed_node_ids[:],
                "next_actions": [
                    {"node_id": action.node_id, "phase": action.phase, "agent": action.agent}
                    for action in next_actions
                ],
                "notes": notes,
            },
        )

    def _insert_dynamic_action_if_missing(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        pending_actions: list[ActionSpec],
        action: ActionSpec,
    ) -> None:
        if action.node_id in [existing.node_id for existing in pending_actions]:
            return
        pending_actions.append(action)
        context.dynamic_trace.append(
            {
                "event": "dynamic_action_inserted",
                "action_id": action.action_id,
                "node_id": action.node_id,
                "phase": action.phase,
                "agent": action.agent,
                "objective": action.objective,
                "tags": action.tags,
            }
        )
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="dynamic_action_inserted",
            phase=action.phase,
            agent=action.agent,
            message=f"Dynamically inserted action: {action.node_id}",
            payload=action.model_dump(mode="json"),
        )

    @staticmethod
    def _pop_pending_action(
        pending_actions: list[ActionSpec],
        action: ActionSpec,
    ) -> ActionSpec:
        action_index = next(
            index for index, candidate in enumerate(pending_actions)
            if candidate.node_id == action.node_id
        )
        return pending_actions.pop(action_index)
