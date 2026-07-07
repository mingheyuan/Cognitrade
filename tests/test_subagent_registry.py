from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.core.agent_control import AgentControl
from research_system.runtime.orchestration.orchestrator_context import build_runtime_context
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.subagents import SubagentInvocation
from research_system.subagents.registry import SubagentRegistry


class _SuccessSubagent:
    subagent_type = "success_subagent"

    def run(self, **kwargs):
        return {"result": "ok", "notes": ["done"]}


class _FailureSubagent:
    subagent_type = "failure_subagent"

    def run(self, **kwargs):
        raise RuntimeError("boom")


class SubagentRegistryTests(unittest.TestCase):
    def _build_runtime(self):
        temp_dir = tempfile.TemporaryDirectory()
        run_store = RunStore(Path(temp_dir.name) / "runs")
        request = RequestEnvelope(user_query="test subagent runtime")
        run_id, paths, snapshot = run_store.create_run(request, "test_intent")
        context = build_runtime_context(
            run_id=run_id,
            paths=paths,
            state=snapshot,
            run_timeout_seconds=120,
        )
        return temp_dir, context, request

    def test_agent_control_invokes_subagent_via_registry_success_case(self) -> None:
        temp_dir, context, request = self._build_runtime()
        try:
            registry = SubagentRegistry([_SuccessSubagent()])

            outputs, record = context.agent_control.invoke(
                registry=registry,
                invocation=SubagentInvocation(
                    subagent_type="success_subagent",
                    objective="Run success case.",
                    inputs={},
                ),
                thread_id=context.thread.thread_id,
                turn_id=context.turn.turn_id,
                request_id=request.request_id,
                agent="tester",
                phase="test",
                execution_policy=context.execution_policy,
            )

            self.assertEqual(outputs["result"], "ok")
            self.assertEqual(record.status, "completed")
        finally:
            temp_dir.cleanup()

    def test_agent_control_notifies_started_and_completed(self) -> None:
        temp_dir, context, request = self._build_runtime()
        try:
            registry = SubagentRegistry([_SuccessSubagent()])
            seen: list[tuple[str, str, str | None, str | None]] = []

            registry.add_observer(
                lambda *, stage, invocation, record, outputs, error: seen.append(
                    (stage, invocation.subagent_type, record.status if record is not None else None, invocation.invocation_id)
                )
            )

            outputs, record = context.agent_control.invoke(
                registry=registry,
                invocation=SubagentInvocation(
                    subagent_type="success_subagent",
                    objective="Run success case.",
                    inputs={},
                ),
                thread_id=context.thread.thread_id,
                turn_id=context.turn.turn_id,
                request_id=request.request_id,
                agent="tester",
                phase="test",
                execution_policy=context.execution_policy,
            )

            self.assertEqual(outputs["result"], "ok")
            self.assertEqual(record.status, "completed")
            self.assertEqual(
                [(stage, subagent_type, status) for stage, subagent_type, status, _ in seen],
                [
                    ("started", "success_subagent", None),
                    ("completed", "success_subagent", "completed"),
                ],
            )
            self.assertTrue(all(isinstance(item[3], str) and item[3] for item in seen))
        finally:
            temp_dir.cleanup()

    def test_agent_control_notifies_failed_before_reraising(self) -> None:
        temp_dir, context, request = self._build_runtime()
        try:
            registry = SubagentRegistry([_FailureSubagent()])
            seen: list[tuple[str, str, str | None, str | None]] = []

            registry.add_observer(
                lambda *, stage, invocation, record, outputs, error: seen.append(
                    (stage, invocation.subagent_type, record.status if record is not None else None, invocation.invocation_id)
                )
            )

            with self.assertRaisesRegex(RuntimeError, "boom"):
                context.agent_control.invoke(
                    registry=registry,
                    invocation=SubagentInvocation(
                        subagent_type="failure_subagent",
                        objective="Run failure case.",
                        inputs={},
                    ),
                    thread_id=context.thread.thread_id,
                    turn_id=context.turn.turn_id,
                    request_id=request.request_id,
                    agent="tester",
                    phase="test",
                    execution_policy=context.execution_policy,
                )

            self.assertEqual(
                [(stage, subagent_type, status) for stage, subagent_type, status, _ in seen],
                [
                    ("started", "failure_subagent", None),
                    ("failed", "failure_subagent", "failed"),
                ],
            )
            self.assertTrue(all(isinstance(item[3], str) and item[3] for item in seen))
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
