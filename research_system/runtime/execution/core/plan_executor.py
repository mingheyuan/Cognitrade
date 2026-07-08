from __future__ import annotations

from time import perf_counter
from typing import Any

from research_system.runtime.execution.core.dag_scheduler import ActionDagScheduler
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.runtime.storage.runs.action_cache_store import ActionCacheStore
from research_system.runtime.supervision.action_plan_patch import apply_action_plan_patch
from research_system.schemas.runtime import BlackboardEntry
from research_system.schemas.planning import ActionPlan, ActionPlanPatch, ActionSpec
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
        cache_store = ActionCacheStore(run_root=context.paths.root)

        if completed_node_ids:
            completed_set = set(completed_node_ids)
            cached_actions = [action for action in pending_actions if action.node_id in completed_set]
            restored_actions, missing_actions, restored_entry_count = self._restore_cached_actions(
                context=context,
                request=request,
                cached_actions=cached_actions,
                cache_store=cache_store,
            )
            if missing_actions:
                missing_ids = {action.node_id for action in missing_actions}
                completed_node_ids = [
                    node_id
                    for node_id in completed_node_ids
                    if node_id not in missing_ids
                ]
            restored_ids = {action.node_id for action in restored_actions}
            if getattr(context, "cache_restore_run_root", None) is None:
                restored_ids = restored_ids or completed_set
            pending_actions = [
                action
                for action in pending_actions
                if action.node_id not in restored_ids
            ]
            if restored_actions:
                self._emit_cache_restored(
                    context=context,
                    request=request,
                    plan=plan,
                    cached_actions=restored_actions,
                    restored_entry_count=restored_entry_count,
                    missing_actions=missing_actions,
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
            applied_plan_patch_rows = self._apply_plan_patch_if_needed(
                context=context,
                request=request,
                plan=plan,
                pending_actions=pending_actions,
                completed_node_ids=completed_node_ids,
                skipped_node_ids=skipped_node_ids,
                plan_patch=decision.plan_patch,
            )
            ready_actions = self.dag_scheduler.ready_actions(
                pending_actions=pending_actions,
                completed_node_ids=completed_node_ids,
                skipped_node_ids=skipped_node_ids,
            )
            self._emit_supervisor_decision(
                context=context,
                request=request,
                plan=plan,
                completed_node_ids=completed_node_ids,
                skipped_node_ids=skipped_node_ids,
                next_actions=decision.next_actions,
                notes=decision.notes,
                plan_patch_rows=applied_plan_patch_rows,
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
            before_entries = context.blackboard.list_entries(thread_id=context.thread.thread_id)
            before_entry_ids = {entry.entry_id for entry in before_entries}
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
            self._save_action_cache_snapshot(
                context=context,
                request=request,
                action=action,
                before_entry_ids=before_entry_ids,
            )
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
        completed_node_ids: list[str],
        skipped_node_ids: list[str],
        next_actions: list[ActionSpec],
        notes: list[str],
        plan_patch_rows: list[dict[str, Any]],
    ) -> None:
        action_id_by_node_id = {
            action.node_id: action.action_id
            for action in plan.actions
        }
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="supervisor_decision",
            message="Supervisor evaluated next actions",
            payload={
                "completed_actions": completed_node_ids[:] + skipped_node_ids[:],
                "completed_node_ids": completed_node_ids[:],
                "skipped_node_ids": skipped_node_ids[:],
                "completed_action_ids": [
                    action_id_by_node_id[node_id]
                    for node_id in completed_node_ids
                    if node_id in action_id_by_node_id
                ],
                "skipped_action_ids": [
                    action_id_by_node_id[node_id]
                    for node_id in skipped_node_ids
                    if node_id in action_id_by_node_id
                ],
                "next_actions": [
                    {"node_id": action.node_id, "phase": action.phase, "agent": action.agent}
                    for action in next_actions
                ],
                "notes": notes,
                "plan_patch": plan_patch_rows[0] if len(plan_patch_rows) == 1 else plan_patch_rows,
                "plan_patch_count": len(plan_patch_rows),
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
        restored_entry_count: int,
        missing_actions: list[ActionSpec],
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
                "restored_entry_count": restored_entry_count,
                "missing_nodes": [action.node_id for action in missing_actions],
            },
        )

    def _save_action_cache_snapshot(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        action: ActionSpec,
        before_entry_ids: set[str],
    ) -> None:
        cache_store = ActionCacheStore(run_root=context.paths.root)
        after_entries = context.blackboard.list_entries(thread_id=context.thread.thread_id)
        new_entries = [
            entry
            for entry in after_entries
            if entry.entry_id not in before_entry_ids
        ]
        result_payload = None
        if action.agent == "reporter" or action.phase == "report":
            payload = read_json(context.paths.result)
            if isinstance(payload, dict):
                result_payload = payload
        cache_store.save_snapshot(
            action=action,
            run_id=context.run_id,
            thread_id=context.thread.thread_id,
            request_id=request.request_id,
            blackboard_entries=new_entries,
            result_payload=result_payload,
        )

    def _restore_cached_actions(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        cached_actions: list[ActionSpec],
        cache_store: ActionCacheStore,
    ) -> tuple[list[ActionSpec], list[ActionSpec], int]:
        source_run_root = getattr(context, "cache_restore_run_root", None)
        if source_run_root is None:
            return cached_actions, [], 0
        existing_entries = context.blackboard.list_entries(thread_id=context.thread.thread_id)
        existing_entry_ids = {entry.entry_id for entry in existing_entries}
        restored_actions: list[ActionSpec] = []
        missing_actions: list[ActionSpec] = []
        restored_entry_count = 0
        for action in cached_actions:
            payload = cache_store.restore_snapshot(
                action=action,
                source_run_root=source_run_root,
                target_run_id=context.run_id,
                target_thread_id=context.thread.thread_id,
                target_request_id=request.request_id,
                thread_id_hint=(
                    str(getattr(context, "cache_restore_thread_id", None) or "").strip()
                    or context.thread.thread_id
                ),
            )
            if payload is None:
                missing_actions.append(action)
                continue
            for entry in list(payload.get("blackboard_entries") or []):
                if not isinstance(entry, BlackboardEntry):
                    continue
                if entry.entry_id in existing_entry_ids:
                    continue
                context.blackboard.add(entry)
                existing_entry_ids.add(entry.entry_id)
                restored_entry_count += 1
            result_payload = payload.get("result_payload")
            if isinstance(result_payload, dict):
                write_json(context.paths.result, result_payload)
            context.dynamic_trace.append(
                {
                    "event": "dag_cache_restored",
                    "node_id": action.node_id,
                    "cache_key": action.cache_key,
                    "source_run_id": payload.get("run_id"),
                    "restored_entry_count": len(list(payload.get("blackboard_entries") or [])),
                }
            )
            restored_actions.append(action)
        return restored_actions, missing_actions, restored_entry_count

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
        if action.node_id not in [existing.node_id for existing in plan.actions]:
            plan.actions.append(action)
            write_json(context.paths.task_plan, plan.model_dump(mode="json"))
        pending_actions.append(action)
        self._emit_dynamic_action_inserted(
            context=context,
            request=request,
            plan=plan,
            action=action,
        )

    def _apply_plan_patch_if_needed(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        pending_actions: list[ActionSpec],
        completed_node_ids: list[str],
        skipped_node_ids: list[str],
        plan_patch: ActionPlanPatch | None,
    ) -> list[dict[str, Any]]:
        if plan_patch is None:
            return []
        if self._plan_patch_requests_replan(plan_patch):
            return self._replan_action_plan(
                context=context,
                request=request,
                plan=plan,
                pending_actions=pending_actions,
                completed_node_ids=completed_node_ids,
                skipped_node_ids=skipped_node_ids,
                plan_patch=plan_patch,
            )
        updated_plan = apply_action_plan_patch(plan, plan_patch)
        inserted_node_ids = {
            operation.action.node_id
            for operation in plan_patch.operations
            if operation.op_type == "insert_action" and operation.action is not None
        }
        patch_record = plan_patch.model_dump(mode="json")
        patch_record.setdefault("plan_id", plan.plan_id)
        patch_record.setdefault("intent", plan.intent)
        patch_record.setdefault("thread_id", context.thread.thread_id)
        patch_record.setdefault("turn_id", context.turn.turn_id)
        context.action_plan_patches.append(dict(patch_record))
        plan.actions = [action.model_copy(deep=True) for action in updated_plan.actions]
        plan.notes = list(updated_plan.notes or [])
        plan.dispatch_trace = dict(updated_plan.dispatch_trace or {})
        pending_node_ids = {action.node_id for action in pending_actions}
        pending_actions[:] = [
            action.model_copy(deep=True)
            for action in plan.actions
            if action.node_id in pending_node_ids or action.node_id in inserted_node_ids
        ]
        write_json(context.paths.task_plan, plan.model_dump(mode="json"))
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="action_plan_patch_applied",
            phase="supervisor",
            agent="supervisor",
            message=f"Applied action plan patch: {plan_patch.patch_id}",
            payload=patch_record,
        )
        for action in plan.actions:
            if action.node_id not in inserted_node_ids:
                continue
            self._emit_dynamic_action_inserted(
                context=context,
                request=request,
                plan=plan,
                action=action,
            )
        return [patch_record]

    def _replan_action_plan(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        pending_actions: list[ActionSpec],
        completed_node_ids: list[str],
        skipped_node_ids: list[str],
        plan_patch: ActionPlanPatch,
    ) -> list[dict[str, Any]]:
        patch_record = plan_patch.model_dump(mode="json")
        patch_record.setdefault("plan_id", plan.plan_id)
        patch_record.setdefault("intent", plan.intent)
        patch_record.setdefault("thread_id", context.thread.thread_id)
        patch_record.setdefault("turn_id", context.turn.turn_id)
        context.action_plan_patches.append(dict(patch_record))
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="action_plan_replan_requested",
            phase="supervisor",
            agent="supervisor",
            message=f"Requested action plan replan: {plan_patch.patch_id}",
            payload=patch_record,
        )
        replanned_plan = self.host._replan_action_plan(
            context=context,
            request=request,
            current_plan=plan.model_copy(deep=True),
            plan_patch=plan_patch,
        )
        previous_plan_id = plan.plan_id
        self._replace_plan(plan=plan, replacement_plan=replanned_plan)
        pending_actions[:] = [
            action.model_copy(deep=True)
            for action in plan.actions
            if action.node_id not in set(completed_node_ids)
            and action.node_id not in set(skipped_node_ids)
        ]
        write_json(context.paths.task_plan, plan.model_dump(mode="json"))
        context.dynamic_trace.append(
            {
                "event": "action_plan_replanned",
                "previous_plan_id": previous_plan_id,
                "plan_id": plan.plan_id,
                "intent": plan.intent,
                "action_count": len(plan.actions),
            }
        )
        self.host._emit_event(
            context,
            request_id=request.request_id,
            intent=plan.intent,
            event_type="action_plan_replanned",
            phase="planning",
            agent="planner",
            message=f"Replanned action graph: {previous_plan_id} -> {plan.plan_id}",
            payload={
                "previous_plan_id": previous_plan_id,
                "plan_id": plan.plan_id,
                "intent": plan.intent,
                "nodes": [
                    {
                        "node_id": action.node_id,
                        "phase": action.phase,
                        "agent": action.agent,
                        "depends_on": list(action.depends_on),
                    }
                    for action in plan.actions
                ],
            },
        )
        return [patch_record]

    def _emit_dynamic_action_inserted(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        action: ActionSpec,
    ) -> None:
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
    def _plan_patch_requests_replan(plan_patch: ActionPlanPatch) -> bool:
        if plan_patch.decision_type == "replan":
            return True
        return any(operation.op_type == "request_replan" for operation in plan_patch.operations)

    @staticmethod
    def _replace_plan(*, plan: ActionPlan, replacement_plan: ActionPlan) -> None:
        plan.plan_id = replacement_plan.plan_id
        plan.intent = replacement_plan.intent
        plan.strategy = replacement_plan.strategy
        plan.actions = [action.model_copy(deep=True) for action in replacement_plan.actions]
        plan.notes = list(replacement_plan.notes or [])
        plan.entry_strategy = replacement_plan.entry_strategy
        plan.entry_reason = replacement_plan.entry_reason
        plan.subagent_required = replacement_plan.subagent_required
        plan.dispatch_trace = dict(replacement_plan.dispatch_trace or {})

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
