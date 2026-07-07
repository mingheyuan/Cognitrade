from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from research_system.runtime.execution.core.plan_executor import OrchestratorPlanExecutor
from research_system.runtime.orchestration.orchestrator_context import build_runtime_context
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.planning import ActionPlan, ActionSpec, SupervisorDecision
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class _Supervisor:
    def decide(self, *, plan, completed_actions, pending_actions, blackboard_entries):
        return SupervisorDecision(
            decision_type="continue" if pending_actions else "stop",
            next_actions=pending_actions[:1],
            completed_action_ids=completed_actions,
            notes=[],
        )


class _Host:
    orchestrator_error_cls = RuntimeError

    def __init__(self) -> None:
        self.supervisor = _Supervisor()
        self.events: list[dict] = []
        self.run_attempts: list[str] = []
        self.fail_once_nodes: set[str] = set()
        self.isolate_nodes: set[str] = set()
        self.cache_entry_nodes: set[str] = set()
        self.blackboard_counts_by_node: dict[str, int] = {}

    def _enforce_runtime_controls(self, context, *, request_id: str, intent: str) -> None:
        return None

    def _emit_event(self, context, **kwargs) -> None:
        self.events.append(kwargs)

    def _update_state(self, context, **kwargs) -> None:
        if "pending_nodes" in kwargs and kwargs["pending_nodes"] is not None:
            context.state.pending_nodes = list(kwargs["pending_nodes"])
        if "completed_nodes" in kwargs and kwargs["completed_nodes"] is not None:
            context.state.completed_nodes = list(kwargs["completed_nodes"])

    def _run_action(
        self,
        *,
        context,
        request,
        plan,
        intent,
        action,
        completed_node_ids,
        pending_actions_after_current,
    ) -> None:
        self.run_attempts.append(action.node_id)
        self.blackboard_counts_by_node[action.node_id] = len(
            context.blackboard.list_entries(thread_id=context.thread.thread_id)
        )
        if action.node_id in self.cache_entry_nodes:
            context.blackboard.add(
                BlackboardEntry(
                    run_id=context.run_id,
                    thread_id=context.thread.thread_id,
                    request_id=request.request_id,
                    kind="source_record",
                    agent=action.agent,
                    phase=action.phase,
                    payload={"title": f"cached:{action.node_id}"},
                    source_refs=[],
                )
            )
        if action.node_id in self.fail_once_nodes:
            self.fail_once_nodes.remove(action.node_id)
            raise RuntimeError(f"fail_once:{action.node_id}")
        if action.node_id in self.isolate_nodes:
            raise RuntimeError(f"isolate:{action.node_id}")


class PlanExecutorDagTests(unittest.TestCase):
    def _build_context(self):
        temp_dir = tempfile.TemporaryDirectory()
        run_store = RunStore(Path(temp_dir.name) / "runs")
        request = RequestEnvelope(user_query="test dag executor")
        run_id, paths, snapshot = run_store.create_run(request, "stock_check")
        context = build_runtime_context(
            run_id=run_id,
            paths=paths,
            state=snapshot,
            run_timeout_seconds=120,
        )
        return temp_dir, request, context

    def test_executor_retries_failed_node_before_continuing_downstream(self) -> None:
        temp_dir, request, context = self._build_context()
        try:
            host = _Host()
            host.fail_once_nodes.add("collect")
            executor = OrchestratorPlanExecutor(host)
            plan = ActionPlan(
                intent="stock_check",
                actions=[
                    ActionSpec(node_id="collect", phase="collect", agent="collector", max_retries=1),
                    ActionSpec(node_id="retrieve", phase="retrieve", agent="retriever", depends_on=["collect"]),
                    ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
                ],
            )

            executor.execute_plan(context=context, request=request, plan=plan)

            self.assertEqual(host.run_attempts, ["collect", "collect", "retrieve", "report"])
            self.assertEqual(context.state.completed_nodes, ["collect", "retrieve", "report"])
            self.assertTrue(any(event["event_type"] == "dag_retry_scheduled" for event in host.events))
        finally:
            temp_dir.cleanup()

    def test_executor_isolates_failure_and_allows_downstream_progress(self) -> None:
        temp_dir, request, context = self._build_context()
        try:
            host = _Host()
            host.isolate_nodes.add("retrieve")
            executor = OrchestratorPlanExecutor(host)
            plan = ActionPlan(
                intent="stock_check",
                actions=[
                    ActionSpec(node_id="collect", phase="collect", agent="collector"),
                    ActionSpec(
                        node_id="retrieve",
                        phase="retrieve",
                        agent="retriever",
                        depends_on=["collect"],
                        failure_mode="isolate",
                    ),
                    ActionSpec(node_id="report", phase="report", agent="reporter", depends_on=["retrieve"]),
                ],
            )

            executor.execute_plan(context=context, request=request, plan=plan)

            self.assertEqual(host.run_attempts, ["collect", "retrieve", "report"])
            self.assertEqual(context.state.completed_nodes, ["collect", "report"])
            self.assertTrue(any(event["event_type"] == "dag_node_isolated_failure" for event in host.events))
        finally:
            temp_dir.cleanup()

    def test_executor_restores_cached_entries_for_partial_rerun(self) -> None:
        temp_dir, request, source_context = self._build_context()
        try:
            source_host = _Host()
            source_host.cache_entry_nodes.add("collect")
            executor = OrchestratorPlanExecutor(source_host)
            plan = ActionPlan(
                intent="stock_check",
                actions=[
                    ActionSpec(
                        node_id="collect",
                        phase="collect",
                        agent="collector",
                        cache_scope="run",
                        cache_key="stock_check:collect",
                    ),
                    ActionSpec(
                        node_id="retrieve",
                        phase="retrieve",
                        agent="retriever",
                        depends_on=["collect"],
                        cache_scope="run",
                        cache_key="stock_check:retrieve",
                    ),
                ],
            )

            executor.execute_plan(context=source_context, request=request, plan=plan)

            run_store = RunStore(Path(temp_dir.name) / "runs")
            rerun_request = RequestEnvelope(user_query="test dag executor rerun")
            rerun_id, rerun_paths, rerun_snapshot = run_store.create_run(rerun_request, "stock_check")
            rerun_context = build_runtime_context(
                run_id=rerun_id,
                paths=rerun_paths,
                state=rerun_snapshot,
                run_timeout_seconds=120,
            )
            rerun_context.cache_restore_run_root = source_context.paths.root
            rerun_context.state.completed_nodes = ["collect"]

            rerun_host = _Host()
            rerun_executor = OrchestratorPlanExecutor(rerun_host)

            rerun_executor.execute_plan(context=rerun_context, request=rerun_request, plan=plan)

            self.assertEqual(rerun_host.run_attempts, ["retrieve"])
            self.assertEqual(rerun_host.blackboard_counts_by_node["retrieve"], 1)
            self.assertTrue(
                any(
                    event["event_type"] == "dag_cache_hit"
                    and event["payload"].get("restored_entry_count") == 1
                    for event in rerun_host.events
                )
            )
        finally:
            temp_dir.cleanup()

    def test_executor_restores_thread_scope_cache_using_source_thread_id(self) -> None:
        temp_dir, request, source_context = self._build_context()
        try:
            source_host = _Host()
            source_host.cache_entry_nodes.add("collect")
            executor = OrchestratorPlanExecutor(source_host)
            plan = ActionPlan(
                intent="stock_check",
                actions=[
                    ActionSpec(
                        node_id="collect",
                        phase="collect",
                        agent="collector",
                        cache_scope="thread",
                        cache_key="stock_check:collect:thread",
                    ),
                    ActionSpec(
                        node_id="retrieve",
                        phase="retrieve",
                        agent="retriever",
                        depends_on=["collect"],
                    ),
                ],
            )

            executor.execute_plan(context=source_context, request=request, plan=plan)

            run_store = RunStore(Path(temp_dir.name) / "runs")
            rerun_request = RequestEnvelope(user_query="test dag executor rerun thread scope")
            rerun_id, rerun_paths, rerun_snapshot = run_store.create_run(rerun_request, "stock_check")
            rerun_context = build_runtime_context(
                run_id=rerun_id,
                paths=rerun_paths,
                state=rerun_snapshot,
                run_timeout_seconds=120,
            )
            rerun_context.cache_restore_run_root = source_context.paths.root
            rerun_context.cache_restore_thread_id = source_context.thread.thread_id
            rerun_context.state.completed_nodes = ["collect"]

            rerun_host = _Host()
            rerun_executor = OrchestratorPlanExecutor(rerun_host)

            rerun_executor.execute_plan(context=rerun_context, request=rerun_request, plan=plan)

            self.assertEqual(rerun_host.run_attempts, ["retrieve"])
            self.assertEqual(rerun_host.blackboard_counts_by_node["retrieve"], 1)
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
