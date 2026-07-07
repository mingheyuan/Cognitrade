from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.agents.retriever.service import RetrieverService
from research_system.runtime.local_runtime import (
    LocalRuntimeHarness,
    inject_runtime_degradation,
    start_local_runtime,
)
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.subagents import SubagentInvocation
from research_system.subagents.registry import SubagentRegistry


class _AvailableProvider:
    available = True


class _LLMPrimarySubagent:
    subagent_type = "llm_primary"

    def __init__(self) -> None:
        self.llm_provider = _AvailableProvider()

    def run(self, **kwargs):
        return {"path": "primary", **kwargs}


class _LLMFallbackSubagent:
    subagent_type = "llm_fallback"

    def run(self, **kwargs):
        return {"path": "fallback", **kwargs}


class _QdrantStub:
    def __init__(self) -> None:
        self.search_calls = 0
        self.upsert_calls = 0

    def search(self, **kwargs):
        self.search_calls += 1
        return []

    def upsert_entry(self, entry) -> None:
        self.upsert_calls += 1


class _MemoryStub:
    def __init__(self) -> None:
        self.search_calls = 0
        self.read_calls = 0

    def search_methodology_memory(self, **kwargs):
        self.search_calls += 1
        return [{"memory_type": "dimension_experience", "content": {"title": "stub"}}]

    def read_recent_methodology_memory(self, **kwargs):
        self.read_calls += 1
        return [{"memory_type": "dimension_experience", "content": {"title": "stub"}}]


class LocalRuntimeToolsTests(unittest.TestCase):
    def test_local_runtime_exposes_replay_trace_events_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = start_local_runtime(
                request=RequestEnvelope(user_query="local runtime tools"),
                intent="stock_check",
                phase="collect",
                runs_root=Path(temp_dir) / "runs",
            )
            try:
                item = runtime.context.agent_control.spawn(
                    thread_id=runtime.context.thread.thread_id,
                    turn_id=runtime.context.turn.turn_id,
                    request_id=runtime.request.request_id,
                    agent="collector",
                    phase="collect",
                    title="stock_snapshot_collector",
                    objective="collect snapshot",
                    payload={"llm_used": True, "invocation_id": "inv-local"},
                    item_type="subagent",
                )
                runtime.context.agent_control.send(item, payload={"source": "test"})
                runtime.context.agent_control.wait(
                    item,
                    status="degraded",
                    payload={"source": "test"},
                    degrade_reason="runtime_fallback",
                )
                child_thread, child_turn = runtime.context.agent_control.spawn_child_thread(
                    parent_thread_id=runtime.context.thread.thread_id,
                    parent_turn_id=runtime.context.turn.turn_id,
                    request_id=runtime.request.request_id,
                    intent="stock_check",
                    objective="child collection",
                    agent="collector",
                    phase="collect_child",
                    execution_policy=runtime.context.execution_policy,
                )
                assert child_thread is not None
                assert child_turn is not None
                child_item = runtime.context.agent_control.spawn(
                    thread_id=child_thread.thread_id,
                    turn_id=child_turn.turn_id,
                    request_id=runtime.request.request_id,
                    agent="collector",
                    phase="collect_child",
                    title="stock_snapshot_collector",
                    objective="collect child snapshot",
                    item_type="subagent",
                )
                runtime.context.agent_control.send(child_item, payload={"source": "child"})
                runtime.context.agent_control.wait(
                    child_item,
                    status="completed",
                    payload={"source": "child"},
                )

                injected = inject_runtime_degradation(
                    runtime,
                    exhaust_llm_budget=True,
                    exhaust_subagent_budget=True,
                    exhaust_child_thread_budget=True,
                )
                replay_payload = runtime.replay()
                trace_payload = runtime.trace()
                events_payload = runtime.events()
                metrics_payload = runtime.metrics()
                steps_payload = runtime.steps()
                subagent_payload = runtime.subagent("inv-local")
                thread_payload = runtime.thread(child_thread.thread_id)
                thread_replay_payload = runtime.thread_replay(child_thread.thread_id)
                thread_metrics_payload = runtime.thread_metrics(child_thread.thread_id)
                thread_tree = runtime.thread_tree()

                self.assertTrue(injected["llm_budget_exhausted"])
                self.assertTrue(injected["subagent_budget_exhausted"])
                self.assertTrue(injected["child_thread_budget_exhausted"])
                self.assertEqual(replay_payload["run_id"], runtime.run_root.name)
                self.assertEqual(trace_payload["run_id"], runtime.run_root.name)
                self.assertEqual(events_payload["run_id"], runtime.run_root.name)
                self.assertEqual(metrics_payload["run_id"], runtime.run_root.name)
                self.assertEqual(metrics_payload["counts"]["subagents"], 2)
                self.assertEqual(metrics_payload["counts"]["child_threads"], 1)
                self.assertEqual(metrics_payload["counts"]["llm_calls"], 1)
                self.assertEqual(metrics_payload["rates"]["fallback_rate"], 0.5)
                self.assertGreaterEqual(metrics_payload["fanout"]["max_depth"], 1)
                self.assertEqual(metrics_payload["turn_usage"][runtime.context.turn.turn_id]["llm_calls"], 1)
                self.assertEqual(metrics_payload["turn_usage"][child_turn.turn_id]["subagents"], 1)
                self.assertGreaterEqual(metrics_payload["turn_usage"][runtime.context.turn.turn_id]["degraded_subagents"], 1)
                self.assertGreaterEqual(metrics_payload["turn_usage"][runtime.context.turn.turn_id]["subagent_latency_ms"], 0)
                self.assertTrue(
                    any(
                        str(event.get("event_type") or "") == "runtime_degradation_injected"
                        for event in list(events_payload.get("events") or [])
                    )
                )
                self.assertTrue(replay_payload["thread_tree"])
                self.assertEqual(steps_payload["run_id"], runtime.run_root.name)
                self.assertEqual(subagent_payload["invocation_id"], "inv-local")
                self.assertEqual(subagent_payload["thread_id"], runtime.context.thread.thread_id)
                self.assertEqual(subagent_payload["phase"], "collect")
                self.assertEqual(subagent_payload["agent"], "collector")
                self.assertTrue(subagent_payload["llm_used"])
                self.assertEqual(subagent_payload["degrade_reason"], "runtime_fallback")
                self.assertGreaterEqual(int(subagent_payload["duration_ms"] or 0), 0)
                self.assertEqual(thread_payload["thread"]["thread_id"], child_thread.thread_id)
                self.assertEqual(thread_payload["event_counts"]["items"], 1)
                self.assertEqual(thread_replay_payload["thread"]["thread_id"], child_thread.thread_id)
                self.assertEqual(thread_replay_payload["scope"], "thread_subtree")
                self.assertEqual(thread_replay_payload["metrics"]["counts"]["threads"], 1)
                self.assertEqual(thread_metrics_payload["thread_id"], child_thread.thread_id)
                self.assertEqual(thread_metrics_payload["counts"]["subagents"], 1)
                self.assertGreaterEqual(thread_metrics_payload["latency_ms"]["subagents"]["count"], 1)
                self.assertTrue(thread_tree)
            finally:
                runtime.close()

    def test_local_runtime_harness_builds_deterministic_root_and_child_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = LocalRuntimeHarness(
                runs_root=Path(temp_dir) / "runs",
                run_id="RUNDET",
                request_id="REQDET",
                thread_id="THREADDET",
                turn_id="TURNDET",
                id_prefix="det",
            )
            runtime = harness.start(
                intent="stock_check",
                phase="collect",
                user_query="deterministic harness",
            )
            try:
                self.assertEqual(runtime.run_root.name, "RUNDET")
                self.assertEqual(runtime.request.request_id, "REQDET")
                self.assertEqual(runtime.context.thread.thread_id, "THREADDET")
                self.assertEqual(runtime.context.turn.turn_id, "TURNDET")

                child_thread, child_turn = runtime.context.agent_control.spawn_child_thread(
                    parent_thread_id=runtime.context.thread.thread_id,
                    parent_turn_id=runtime.context.turn.turn_id,
                    request_id=runtime.request.request_id,
                    intent="stock_check",
                    objective="child collection",
                    agent="collector",
                    phase="collect_child",
                    execution_policy=runtime.context.execution_policy,
                )

                assert child_thread is not None
                assert child_turn is not None
                self.assertEqual(child_thread.thread_id, "det-0001")
                self.assertEqual(child_turn.turn_id, "det-0002")
                self.assertEqual(harness.thread(child_thread.thread_id)["thread_id"], child_thread.thread_id)
                self.assertEqual(harness.thread_replay(child_thread.thread_id)["thread_id"], child_thread.thread_id)
                self.assertEqual(harness.thread_metrics(child_thread.thread_id)["thread_id"], child_thread.thread_id)
                self.assertTrue(harness.thread_tree())
                self.assertEqual(harness.steps()["run_id"], "RUNDET")
            finally:
                runtime.close()

    def test_local_runtime_degradation_injection_forces_llm_fallback_and_dependency_unavailability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = start_local_runtime(
                request=RequestEnvelope(user_query="inject degradation"),
                intent="stock_check",
                phase="retrieve",
                runs_root=Path(temp_dir) / "runs",
            )
            try:
                injected = runtime.inject_degradation(
                    llm_unavailable=True,
                    qdrant_unavailable=True,
                    memory_unavailable=True,
                )
                self.assertTrue(injected["llm_unavailable"])
                self.assertTrue(injected["qdrant_unavailable"])
                self.assertTrue(injected["memory_unavailable"])
                llm_decision = runtime.support.resolve_llm_execution(
                    runtime.context,
                    request_id=runtime.request.request_id,
                    agent="reporter",
                    phase="report",
                    strategy="fallback_report_composer",
                    provider=_AvailableProvider(),
                    reserve=False,
                )
                self.assertFalse(llm_decision.can_use_llm)
                self.assertEqual(llm_decision.degrade_reason, "llm_unavailable")

                registry = SubagentRegistry([_LLMPrimarySubagent(), _LLMFallbackSubagent()])
                outputs, record = runtime.context.agent_control.invoke(
                    registry=registry,
                    invocation=SubagentInvocation(
                        subagent_type="llm_primary",
                        objective="run llm path",
                        inputs={"query": "inject"},
                        requires_llm=True,
                        fallback_subagent_type="llm_fallback",
                        fallback_inputs={"query": "inject", "runtime_degraded_reason": ""},
                    ),
                    thread_id=runtime.context.thread.thread_id,
                    turn_id=runtime.context.turn.turn_id,
                    request_id=runtime.request.request_id,
                    agent="tester",
                    phase="retrieve",
                    execution_policy=runtime.context.execution_policy,
                )

                self.assertEqual(outputs["path"], "fallback")
                self.assertEqual(record.degrade_reason, "llm_unavailable")

                qdrant_stub = _QdrantStub()
                memory_stub = _MemoryStub()
                retriever = object.__new__(RetrieverService)
                retriever.memory_service = memory_stub
                retriever.retrieval_policy = {}
                retriever.qdrant = qdrant_stub
                retriever.qdrant_available = True
                retriever.qdrant_error = None
                retriever.indexer_qdrant = qdrant_stub
                retriever.searcher_qdrant = qdrant_stub
                retriever.bind_runtime(
                    agent_control=runtime.context.agent_control,
                    execution_policy=runtime.context.execution_policy,
                    context=runtime.context,
                    support=runtime.support,
                    intent="stock_check",
                    thread_id=runtime.context.thread.thread_id,
                    turn_id=runtime.context.turn.turn_id,
                    request_id=runtime.request.request_id,
                    agent="retriever",
                    phase="retrieve",
                )
                qdrant_payload = retriever._search_qdrant_evidences(
                    intent="stock_check",
                    query="check 000001",
                    top_k=3,
                    symbol="000001",
                    sector=None,
                    current_run_id=runtime.run_root.name,
                )
                memory_payload = retriever._recall_methodology_memory_rows(
                    intent="stock_check",
                    query="check 000001",
                    planning_context_hints={},
                    limit=3,
                )
                summary_rows = retriever._methodology_memory_rows(
                    limit=3,
                    query="check 000001",
                    planning_context_hints={},
                )

                self.assertTrue(qdrant_payload["degraded"])
                self.assertEqual(qdrant_payload["degrade_reason"], "qdrant_unavailable")
                self.assertTrue(memory_payload["degraded"])
                self.assertEqual(memory_payload["degrade_reason"], "memory_service_unavailable")
                self.assertEqual(summary_rows, [])
                self.assertEqual(qdrant_stub.search_calls, 0)
                self.assertEqual(memory_stub.search_calls, 0)
                self.assertEqual(memory_stub.read_calls, 0)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
