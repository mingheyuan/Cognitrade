from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.local_runtime import inject_runtime_degradation, start_local_runtime
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
                    item_type="subagent",
                )
                runtime.context.agent_control.send(item, payload={"source": "test"})
                runtime.context.agent_control.wait(
                    item,
                    status="degraded",
                    payload={"source": "test"},
                    degrade_reason="runtime_fallback",
                )
                runtime.context.agent_control.spawn_child_thread(
                    parent_thread_id=runtime.context.thread.thread_id,
                    parent_turn_id=runtime.context.turn.turn_id,
                    request_id=runtime.request.request_id,
                    intent="stock_check",
                    objective="child collection",
                    agent="collector",
                    phase="collect_child",
                    execution_policy=runtime.context.execution_policy,
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

                self.assertTrue(injected["llm_budget_exhausted"])
                self.assertTrue(injected["subagent_budget_exhausted"])
                self.assertTrue(injected["child_thread_budget_exhausted"])
                self.assertEqual(replay_payload["run_id"], runtime.run_root.name)
                self.assertEqual(trace_payload["run_id"], runtime.run_root.name)
                self.assertEqual(events_payload["run_id"], runtime.run_root.name)
                self.assertEqual(metrics_payload["run_id"], runtime.run_root.name)
                self.assertEqual(metrics_payload["counts"]["subagents"], 1)
                self.assertEqual(metrics_payload["counts"]["child_threads"], 1)
                self.assertEqual(metrics_payload["rates"]["fallback_rate"], 1.0)
                self.assertGreaterEqual(metrics_payload["fanout"]["max_depth"], 1)
                self.assertTrue(
                    any(
                        str(event.get("event_type") or "") == "runtime_degradation_injected"
                        for event in list(events_payload.get("events") or [])
                    )
                )
                self.assertTrue(replay_payload["thread_tree"])
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
