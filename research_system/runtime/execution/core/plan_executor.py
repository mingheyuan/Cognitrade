from __future__ import annotations

from time import perf_counter
from typing import Any

from research_system.runtime.execution.core.dag_scheduler import ActionDagScheduler
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope


class OrchestratorPlanExecutor:
    def __init__(self, host: Any) -> None:
        self.host = host
        self.dag_scheduler = ActionDagScheduler()

    def execute_plan(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
    ) -> None:
        validation = self.dag_scheduler.validate(plan)
        if not validation.valid:
            raise self.host.orchestrator_error_cls(
                f"invalid_action_dag:{';'.join(validation.errors)}"
            )
        pending_actions: list[ActionSpec] = [action.model_copy(deep=True) for action in plan.actions]
        completed_node_ids: list[str] = list(dict.fromkeys(list(context.state.completed_nodes or [])))
        failed_node_ids: list[str] = []
        skipped_node_ids: list[str] = []
        fanout_depth = self.dag_scheduler.fanout_depth(plan)

        if completed_node_ids:
            completed_set = set(completed_node_ids)
            cached_actions = [action for action in pending_actions if action.node_id in completed_set]
            pending_actions = [action for action in pending_actions if action.node_id not in completed_set]
            if cached_actions:
                self._emit_cache_restored(
                    context=context,
                    request=request,
                    plan=plan,
                    cached_actions=cached_actions,
                )

        self.host._update_state(
            context,
            pending_nodes=[action.node_id for action in pending_actions],
            completed_nodes=completed_node_ids[:],
        )
        self._emit_dag_initialized(
            context=context,
            request=request,
            plan=plan,
            fanout_depth=fanout_depth,
        )

        while True:
            self.host._enforce_runtime_controls(context, request_id=request.request_id, intent=plan.intent)
            blocked_actions = self.dag_scheduler.dependency_blocked_actions(
                pending_actions=pending_actions,
                failed_node_ids=failed_node_ids,
            )
            if blocked_actions:
                blocked_ids = {action.node_id for action in blocked_actions}
                pending_actions = [action for action in pending_actions if action.node_id not in blocked_ids]
                skipped_node_ids.extend(action.node_id for action in blocked_actions if action.node_id not in skipped_node_ids)
                self._emit_blocked_actions(
                    context=context,
                    request=request,
                    plan=plan,
                    blocked_actions=blocked_actions,
                    failed_node_ids=failed_node_ids,
                )

            ready_actions = self.dag_scheduler.ready_actions(
                pending_actions=pending_actions,
                completed_node_ids=completed_node_ids,
                skipped_node_ids=skipped_node_ids,
            )
            decision = self.host.supervisor.decide(
                plan=plan,
                completed_actions=completed_node_ids[:] + skipped_node_ids[:],
                pending_actions=ready_actions[:],
                blackboard_entries=context.blackboard.list_entries(thread_id=context.thread.thread_id),
            )
            self._emit_supervisor_decision(
                context=context,
                request=request,
                plan=plan,
                executed_node_ids=completed_node_ids + skipped_node_ids,
                next_actions=decision.next_actions,
                notes=decision.notes,
            )
            if not pending_actions:
                self.host._update_state(
                    context,
                    pending_nodes=[],
                    completed_nodes=completed_node_ids[:],
                )
                context.checkpoint_store.save(context.state)
                return
            if not ready_actions:
                raise self.host.orchestrator_error_cls(
                    f"dag_stalled:pending={','.join(action.node_id for action in pending_actions)}"
                )
            if decision.decision_type == "stop":
                self.host._update_state(
                    context,
                    pending_nodes=[action.node_id for action in pending_actions],
                    completed_nodes=completed_node_ids[:],
                )
                context.checkpoint_store.save(context.state)
                return
            next_actions = decision.next_actions[:] or ready_actions[:1]

            self._emit_ready_frontier(
                context=context,
                request=request,
                plan=plan,
                ready_actions=ready_actions,
                failed_node_ids=failed_node_ids,
                skipped_node_ids=skipped_node_ids,
                fanout_depth=fanout_depth,
            )

            for action in next_actions:
                self._insert_dynamic_action_if_missing(
                    context=context,
                    request=request,
                    plan=plan,
                    pending_actions=pending_actions,
                    action=action,
                )
                ready_actions = self.dag_scheduler.ready_actions(
                    pending_actions=pending_actions,
                    completed_node_ids=completed_node_ids,
                    skipped_node_ids=skipped_node_ids,
                )
                if action.node_id not in {candidate.node_id for candidate in ready_actions}:
                    continue
                current_action = self._pop_pending_action(pending_actions, action)
                outcome = self._run_action_with_retries(
                    context=context,
                    request=request,
                    plan=plan,
                    action=current_action,
                    completed_node_ids=completed_node_ids[:],
                    pending_actions_after_current=pending_actions[:],
                )
                if outcome["status"] == "completed":
                    completed_node_ids.append(current_action.node_id)
                elif outcome["status"] == "skipped":
                    skipped_node_ids.append(current_action.node_id)
                else:
                    failed_node_ids.append(current_action.node_id)
                    if current_action.failure_mode != "isolate":
                        raise outcome["error"]

    def _run_action_with_retries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        action: ActionSpec,
        completed_node_ids: list[str],
        pending_actions_after_current: list[ActionSpec],
    ) -> dict[str, Any]:
        max_attempts = max(int(action.max_retries or 0), 0) + 1
        for attempt in range(1, max_attempts + 1):
            started = perf_counter()
            try:
                self.host._run_action(
                    context=context,
                    request=request,
                    plan=plan,
                    intent=plan.intent,
                    action=action,
                    completed_node_ids=completed_node_ids[:],
                    pending_actions_after_current=pending_actions_after_current[:],
                )
            except Exception as exc:
                duration_ms = int((perf_counter() - started) * 1000)
                if attempt < max_attempts:
                    self._emit_retry_scheduled(
                        context=context,
                        request=request,
                        plan=plan,
                        action=action,
                        attempt=attempt,
                        duration_ms=duration_ms,
                        error=exc,
                    )
                    continue
                if action.failure_mode == "isolate":
                    self._emit_isolated_failure(
                        context=context,
                        request=request,
                        plan=plan,
                        action=action,
                        attempt=attempt,
                        duration_ms=duration_ms,
                        error=exc,
                    )
                    return {"status": "skipped", "error": exc, "attempt": attempt, "duration_ms": duration_ms}
                raise
            duration_ms = int((perf_counter() - started) * 1000)
            self._emit_action_succeeded(
                context=context,
                request=request,
                plan=plan,
                action=action,
                attempt=attempt,
                duration_ms=duration_ms,
            )
            return {"status": "completed", "attempt": attempt, "duration_ms": duration_ms}
        raise self.host.orchestrator_error_cls(f"unreachable_action_retry_state:{action.node_id}")

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

    def _emit_dag_initialized(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        fanout_depth: dict[str, int],
    ) -> None:
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="dag_initialized",
            message="Initialized explicit action DAG",
            payload={
                "nodes": [
                    {
                        "node_id": action.node_id,
                        "depends_on": list(action.depends_on),
                        "phase": action.phase,
                        "agent": action.agent,
                        "max_retries": action.max_retries,
                        "failure_mode": action.failure_mode,
                        "cache_key": action.cache_key,
                    }
                    for action in plan.actions
                ],
                "fanout_depth": fanout_depth,
            },
        )

    def _emit_ready_frontier(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        ready_actions: list[ActionSpec],
        failed_node_ids: list[str],
        skipped_node_ids: list[str],
        fanout_depth: dict[str, int],
    ) -> None:
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="dag_frontier_updated",
            message="Updated DAG ready frontier",
            payload={
                "ready_nodes": [action.node_id for action in ready_actions],
                "failed_nodes": failed_node_ids[:],
                "skipped_nodes": skipped_node_ids[:],
                "fanout_depth": fanout_depth,
            },
        )

    def _emit_retry_scheduled(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        action: ActionSpec,
        attempt: int,
        duration_ms: int,
        error: Exception,
    ) -> None:
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="dag_retry_scheduled",
            phase=action.phase,
            agent=action.agent,
            message=f"Retry scheduled for {action.node_id}",
            payload={
                "node_id": action.node_id,
                "attempt": attempt,
                "max_retries": action.max_retries,
                "duration_ms": duration_ms,
                "error_summary": f"{error.__class__.__name__}: {error}",
            },
        )

    def _emit_action_succeeded(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        action: ActionSpec,
        attempt: int,
        duration_ms: int,
    ) -> None:
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="dag_node_completed",
            phase=action.phase,
            agent=action.agent,
            message=f"DAG node completed: {action.node_id}",
            payload={
                "node_id": action.node_id,
                "attempt": attempt,
                "duration_ms": duration_ms,
                "depends_on": list(action.depends_on),
                "cache_key": action.cache_key,
            },
        )

    def _emit_isolated_failure(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        action: ActionSpec,
        attempt: int,
        duration_ms: int,
        error: Exception,
    ) -> None:
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="dag_node_isolated_failure",
            phase=action.phase,
            agent=action.agent,
            message=f"Isolated DAG node failure: {action.node_id}",
            payload={
                "node_id": action.node_id,
                "attempt": attempt,
                "duration_ms": duration_ms,
                "error_summary": f"{error.__class__.__name__}: {error}",
                "failure_mode": action.failure_mode,
            },
        )

    def _emit_blocked_actions(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        blocked_actions: list[ActionSpec],
        failed_node_ids: list[str],
    ) -> None:
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="dag_dependency_blocked",
            message="Skipped DAG nodes blocked by failed dependencies",
            payload={
                "blocked_nodes": [action.node_id for action in blocked_actions],
                "failed_nodes": failed_node_ids[:],
            },
        )

    def _emit_cache_restored(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        cached_actions: list[ActionSpec],
    ) -> None:
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="dag_cache_hit",
            message="Restored previously completed DAG nodes",
            payload={
                "cached_nodes": [action.node_id for action in cached_actions],
                "cache_keys": [action.cache_key for action in cached_actions if action.cache_key],
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
