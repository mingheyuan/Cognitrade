from __future__ import annotations

import unittest

from research_system.agents._subagent_dag import (
    SubagentDagExecutor,
    SubagentDagNodeSpec,
)
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


if __name__ == "__main__":
    unittest.main()
