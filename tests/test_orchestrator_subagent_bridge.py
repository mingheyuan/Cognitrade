from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from research_system.runtime.orchestration.orchestrator_context import build_runtime_context
from research_system.runtime.orchestration.orchestrator_subagent_bridge import OrchestratorSubagentBridge
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation


class _BridgeHost:
    def __init__(self, runtime_context) -> None:
        self._active_runtime_context = runtime_context
        self.collector = SimpleNamespace(registry=None)
        self.distiller = SimpleNamespace(registry=None)
        self.critic = SimpleNamespace(registry=None)
        self.retriever = SimpleNamespace(registry=None)
        self.reporter = SimpleNamespace(registry=None)
        self.emitted_events: list[dict] = []

    def _emit_event(self, context, **kwargs) -> None:
        self.emitted_events.append(kwargs)

    def _execution_support(self):
        return SimpleNamespace(
            trace_task_name=lambda payload: payload.get("task_name") or "derived_task",
            trace_agent_path=lambda payload: payload.get("agent_path") or "derived/path",
            trace_agent_label=lambda payload: payload.get("agent_label") or "derived_label",
        )


class OrchestratorSubagentBridgeTests(unittest.TestCase):
    def test_handle_registry_event_includes_degrade_reason_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_store = RunStore(Path(temp_dir) / "runs")
            request = RequestEnvelope(user_query="trace degraded subagent")
            run_id, paths, snapshot = run_store.create_run(request, "stock_check")
            context = build_runtime_context(
                run_id=run_id,
                paths=paths,
                state=snapshot,
                run_timeout_seconds=60,
            )
            context.state.current_phase = "retrieve"
            context.state.active_agent = "retriever"

            host = _BridgeHost(context)
            bridge = OrchestratorSubagentBridge(host)

            invocation = SubagentInvocation(
                subagent_type="qdrant_evidence_searcher",
                invocation_id="invoke-1",
                objective="Search indexed evidence candidates via Qdrant.",
                context={
                    "task_name": "retrieve:search:qdrant_evidence_searcher",
                    "agent_path": "retriever/search/qdrant_evidence_searcher",
                },
            )
            record = SubagentExecutionRecord(
                subagent_type="qdrant_evidence_searcher",
                status="degraded",
                objective=invocation.objective,
                degrade_reason="qdrant_unavailable",
                degrade_reasons=["qdrant_unavailable", "runtime_fallback"],
                notes=["fallback to blackboard only"],
                output_keys=["evidences"],
            )

            bridge.handle_registry_event(
                stage="completed",
                invocation=invocation,
                record=record,
                outputs={"degraded": True},
                error=None,
            )

            self.assertEqual(len(host.emitted_events), 1)
            payload = host.emitted_events[0]["payload"]
            self.assertEqual(host.emitted_events[0]["event_type"], "subagent_completed")
            self.assertEqual(payload["status"], "degraded")
            self.assertEqual(payload["degrade_reason"], "qdrant_unavailable")
            self.assertEqual(payload["degrade_reasons"], ["qdrant_unavailable", "runtime_fallback"])
            self.assertEqual(payload["task_name"], "retrieve:search:qdrant_evidence_searcher")
            self.assertEqual(payload["agent_path"], "retriever/search/qdrant_evidence_searcher")


if __name__ == "__main__":
    unittest.main()
