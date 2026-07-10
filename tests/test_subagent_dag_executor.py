from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.agents._subagent_dag import (
    SubagentDagCacheContext,
    SubagentDagExecutor,
    SubagentDagNodeSpec,
)
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation


class SubagentDagExecutorTests(unittest.TestCase):
    def test_executor_retries_node_before_running_downstream_dependency(self) -> None:
        attempts: dict[str, int] = {}
        event_types: list[str] = []
        call_order: list[str] = []

        def invoke(invocation: SubagentInvocation):
            subagent_type = invocation.subagent_type
            attempts[subagent_type] = attempts.get(subagent_type, 0) + 1
            call_order.append(subagent_type)
            if subagent_type == "collect_snapshot" and attempts[subagent_type] == 1:
                raise RuntimeError("transient collect failure")
            return (
                {"value": subagent_type, "degraded": False},
                SubagentExecutionRecord(
                    subagent_type=subagent_type,
                    objective=invocation.objective,
                    inputs=invocation.inputs,
                    output_keys=["value", "degraded"],
                ),
            )

        executor = SubagentDagExecutor(
            invoke_subagent=invoke,
            emit_event=lambda **kwargs: event_types.append(str(kwargs["event_type"])),
        )

        results = executor.execute(
            [
                SubagentDagNodeSpec(
                    node_id="collect",
                    max_attempts=2,
                    build_invocation=lambda _results: SubagentInvocation(
                        subagent_type="collect_snapshot",
                        objective="Collect snapshot",
                    ),
                ),
                SubagentDagNodeSpec(
                    node_id="retrieve",
                    depends_on=("collect",),
                    build_invocation=lambda results: SubagentInvocation(
                        subagent_type="retrieve_evidence",
                        objective=str(results["collect"].outputs["value"]),
                    ),
                ),
            ]
        )

        self.assertEqual(call_order, ["collect_snapshot", "collect_snapshot", "retrieve_evidence"])
        self.assertEqual(results["collect"].attempts, 2)
        self.assertEqual(results["collect"].status, "completed")
        self.assertEqual(results["retrieve"].status, "completed")
        self.assertIn("subagent_dag_retry_scheduled", event_types)
        self.assertIn("subagent_dag_node_completed", event_types)

    def test_executor_allows_partial_dependency_merger_after_isolated_failure(self) -> None:
        event_types: list[str] = []

        def invoke(invocation: SubagentInvocation):
            if invocation.subagent_type == "distill_chunk_fail":
                raise RuntimeError("chunk failed")
            if invocation.subagent_type == "merge_chunks":
                chunk_results = list(invocation.inputs.get("chunk_results") or [])
                return (
                    {"merged_chunk_count": len(chunk_results)},
                    SubagentExecutionRecord(
                        subagent_type="merge_chunks",
                        objective=invocation.objective,
                        inputs=invocation.inputs,
                        output_keys=["merged_chunk_count"],
                    ),
                )
            return (
                {"chunk_id": invocation.subagent_type},
                SubagentExecutionRecord(
                    subagent_type=invocation.subagent_type,
                    objective=invocation.objective,
                    inputs=invocation.inputs,
                    output_keys=["chunk_id"],
                ),
            )

        executor = SubagentDagExecutor(
            invoke_subagent=invoke,
            emit_event=lambda **kwargs: event_types.append(str(kwargs["event_type"])),
        )

        results = executor.execute(
            [
                SubagentDagNodeSpec(
                    node_id="chunk_1",
                    failure_mode="isolate",
                    build_invocation=lambda _results: SubagentInvocation(
                        subagent_type="distill_chunk_fail",
                        objective="chunk 1",
                    ),
                ),
                SubagentDagNodeSpec(
                    node_id="chunk_2",
                    build_invocation=lambda _results: SubagentInvocation(
                        subagent_type="distill_chunk_ok",
                        objective="chunk 2",
                    ),
                ),
                SubagentDagNodeSpec(
                    node_id="merge",
                    depends_on=("chunk_1", "chunk_2"),
                    allow_partial_dependencies=True,
                    build_invocation=lambda results: SubagentInvocation(
                        subagent_type="merge_chunks",
                        objective="merge",
                        inputs={
                            "chunk_results": [
                                result.outputs
                                for result in results.values()
                                if result.outputs is not None
                            ]
                        },
                    ),
                ),
            ]
        )

        self.assertEqual(results["chunk_1"].status, "failed")
        self.assertEqual(results["chunk_2"].status, "completed")
        self.assertEqual(results["merge"].status, "completed")
        self.assertEqual(results["merge"].outputs["merged_chunk_count"], 1)
        self.assertIn("subagent_dag_node_isolated_failure", event_types)
        self.assertIn("subagent_dag_node_completed", event_types)

    def test_executor_restores_replayable_nodes_from_cache_and_reruns_terminal_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir) / "runs"
            first_run_root = runs_root / "RUN1"
            first_run_root.mkdir(parents=True, exist_ok=True)
            second_run_root = runs_root / "RUN2"
            second_run_root.mkdir(parents=True, exist_ok=True)
            call_order: list[str] = []
            event_types: list[str] = []

            def invoke(invocation: SubagentInvocation):
                call_order.append(invocation.subagent_type)
                return (
                    {"value": invocation.subagent_type},
                    SubagentExecutionRecord(
                        subagent_type=invocation.subagent_type,
                        invocation_id=f"inv-{invocation.subagent_type}",
                        objective=invocation.objective,
                        inputs=invocation.inputs,
                        output_keys=["value"],
                    ),
                )

            executor = SubagentDagExecutor(
                invoke_subagent=invoke,
                emit_event=lambda **kwargs: event_types.append(str(kwargs["event_type"])),
            )
            nodes = [
                SubagentDagNodeSpec(
                    node_id="index",
                    replayable=True,
                    cache_scope="run",
                    cache_key="index",
                    build_invocation=lambda _results: SubagentInvocation(
                        subagent_type="index_inputs",
                        objective="index",
                    ),
                ),
                SubagentDagNodeSpec(
                    node_id="decide",
                    depends_on=("index",),
                    build_invocation=lambda results: SubagentInvocation(
                        subagent_type="decide_outputs",
                        objective=str((results["index"].outputs or {}).get("value")),
                    ),
                ),
            ]
            first_results = executor.execute(
                nodes,
                cache_context=SubagentDagCacheContext(
                    dag_id="retrieve:phase_one",
                    run_root=first_run_root,
                    run_id="RUN1",
                    thread_id="thread-1",
                    turn_id="turn-1",
                    request_id="req-1",
                ),
            )
            self.assertEqual(call_order, ["index_inputs", "decide_outputs"])
            self.assertTrue(first_results["index"].artifact_id)
            self.assertEqual(first_results["decide"].outputs["value"], "decide_outputs")
            artifacts = ArtifactBoard(first_run_root / "artifact_board.jsonl").list_artifacts(
                thread_id="thread-1",
                artifact_type="subagent_execution_output",
            )
            self.assertEqual(len(artifacts), 2)
            self.assertEqual(artifacts[1].parent_artifact_ids, [artifacts[0].artifact_id])

            call_order.clear()
            results = executor.execute(
                nodes,
                cache_context=SubagentDagCacheContext(
                    dag_id="retrieve:phase_one",
                    run_root=second_run_root,
                    run_id="RUN2",
                    thread_id="thread-2",
                    turn_id="turn-2",
                    request_id="req-2",
                    source_run_root=first_run_root,
                    source_thread_id="thread-1",
                ),
            )

        self.assertEqual(call_order, ["decide_outputs"])
        self.assertTrue(results["index"].cache_hit)
        self.assertEqual(results["index"].record.notes[-1], "cache_hit=RUN1:index")
        self.assertIn("subagent_dag_cache_hit", event_types)
        self.assertIn("subagent_dag_cache_restored", event_types)

    def test_executor_partial_rerun_restores_ancestors_and_skips_independent_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir) / "runs"
            first_run_root = runs_root / "RUN1"
            first_run_root.mkdir(parents=True, exist_ok=True)
            second_run_root = runs_root / "RUN2"
            second_run_root.mkdir(parents=True, exist_ok=True)
            call_order: list[str] = []
            event_types: list[str] = []

            def invoke(invocation: SubagentInvocation):
                call_order.append(invocation.subagent_type)
                return (
                    {"value": invocation.subagent_type},
                    SubagentExecutionRecord(
                        subagent_type=invocation.subagent_type,
                        invocation_id=f"inv-{invocation.subagent_type}",
                        objective=invocation.objective,
                        inputs=invocation.inputs,
                        output_keys=["value"],
                    ),
                )

            executor = SubagentDagExecutor(
                invoke_subagent=invoke,
                emit_event=lambda **kwargs: event_types.append(str(kwargs["event_type"])),
            )
            nodes = [
                SubagentDagNodeSpec(
                    node_id="prepare",
                    replayable=True,
                    cache_scope="run",
                    cache_key="prepare",
                    build_invocation=lambda _results: SubagentInvocation(
                        subagent_type="prepare_inputs",
                        objective="prepare",
                    ),
                ),
                SubagentDagNodeSpec(
                    node_id="branch_a",
                    depends_on=("prepare",),
                    replayable=True,
                    cache_scope="run",
                    cache_key="branch_a",
                    build_invocation=lambda _results: SubagentInvocation(
                        subagent_type="branch_a",
                        objective="branch a",
                    ),
                ),
                SubagentDagNodeSpec(
                    node_id="branch_b",
                    depends_on=("prepare",),
                    replayable=True,
                    cache_scope="run",
                    cache_key="branch_b",
                    build_invocation=lambda _results: SubagentInvocation(
                        subagent_type="branch_b",
                        objective="branch b",
                    ),
                ),
                SubagentDagNodeSpec(
                    node_id="merge",
                    depends_on=("branch_a", "branch_b"),
                    build_invocation=lambda results: SubagentInvocation(
                        subagent_type="merge",
                        objective="merge",
                        inputs={
                            "values": [
                                (results["branch_a"].outputs or {}).get("value"),
                                (results["branch_b"].outputs or {}).get("value"),
                            ]
                        },
                    ),
                ),
            ]
            executor.execute(
                nodes,
                cache_context=SubagentDagCacheContext(
                    dag_id="distill:chunks",
                    run_root=first_run_root,
                    run_id="RUN1",
                    thread_id="thread-1",
                    turn_id="turn-1",
                    request_id="req-1",
                ),
            )

            call_order.clear()
            results = executor.execute(
                nodes,
                cache_context=SubagentDagCacheContext(
                    dag_id="distill:chunks",
                    run_root=second_run_root,
                    run_id="RUN2",
                    thread_id="thread-2",
                    turn_id="turn-2",
                    request_id="req-2",
                    source_run_root=first_run_root,
                    source_thread_id="thread-1",
                ),
                start_from_node_id="branch_b",
            )

        self.assertEqual(call_order, ["branch_b", "merge"])
        self.assertTrue(results["prepare"].cache_hit)
        self.assertTrue(results["branch_a"].cache_hit)
        self.assertIn("subagent_dag_partial_rerun_requested", event_types)
        self.assertIn("subagent_dag_cache_hit", event_types)


if __name__ == "__main__":
    unittest.main()
