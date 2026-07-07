from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.local_runtime import (
    LocalRuntimeHarness,
    inject_runtime_degradation,
    start_local_runtime,
)
from research_system.schemas.protocol import RequestEnvelope


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
                    payload={"llm_used": True},
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
                thread_payload = runtime.thread(child_thread.thread_id)

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
                self.assertTrue(
                    any(
                        str(event.get("event_type") or "") == "runtime_degradation_injected"
                        for event in list(events_payload.get("events") or [])
                    )
                )
                self.assertTrue(replay_payload["thread_tree"])
                self.assertEqual(thread_payload["thread"]["thread_id"], child_thread.thread_id)
                self.assertEqual(thread_payload["event_counts"]["items"], 1)
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
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
