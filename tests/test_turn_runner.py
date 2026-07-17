from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.storage.runs.run_store import RunStore
from research_system.runtime.turn_runner import (
    GraphExecutor,
    RegularExecutor,
    TurnContext,
    TurnRunner,
)
from research_system.schemas.core_runtime import MainAgentRunResult, MainAgentState, RequestIntakeDecision
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.execution_runtime import ExecutionRunResult, ExecutionRunSnapshot
from research_system.schemas.protocol import ProgressState, RequestEnvelope, ResponseEnvelope
from research_system.schemas.turn_runtime import TaskOutcome, TurnConfig, TurnSnapshot
from research_system.runtime.storage.jsonl import read_json, read_jsonl, write_json


class _RecordingExecutor:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls = 0

    def execute(self, _context: TurnContext) -> TaskOutcome:
        self.calls += 1
        return TaskOutcome(execution_mode="core", status=self.status, result=self.status)


class _FakeMainAgent:
    def run(self, **kwargs):
        state = MainAgentState(
            run_id=kwargs["run_id"],
            thread_id=kwargs["thread_id"],
            request_id=kwargs["request"].request_id,
            status="completed",
            mode="core",
        )
        return MainAgentRunResult(
            run_id=state.run_id,
            thread_id=state.thread_id,
            request_id=state.request_id,
            status="completed",
            mode="core",
            message="done",
            state=state,
        )


class _UnusedGraphAdapter:
    def execute(self, _context: TurnContext) -> ExecutionRunResult:
        raise AssertionError("Graph adapter must not run in the core test")


class TurnRunnerTests(unittest.TestCase):
    def test_regular_executor_selects_only_the_configured_internal_executor(self) -> None:
        core = _RecordingExecutor("completed")
        graph = _RecordingExecutor("gated")
        executor = RegularExecutor(core_executor=core, graph_executor=graph)
        request = RequestEnvelope(user_query="test")
        decision = RequestIntakeDecision(
            status="accepted",
            request_kind="conversation",
            recommended_mode="core",
        )
        turn = TurnSnapshot(
            run_id="run-1",
            thread_id="thread-1",
            request_id=request.request_id,
        )
        context = TurnContext(
            request=request,
            decision=decision,
            run_id="run-1",
            thread_id="thread-1",
            turn=turn,
            config=TurnConfig(execution_mode="core"),
            run_store=RunStore(Path(".")),
            main_agent=None,
        )

        outcome = executor.execute(context)
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(core.calls, 1)
        self.assertEqual(graph.calls, 0)

        context.config = TurnConfig(execution_mode="graph")
        outcome = executor.execute(context)
        self.assertEqual(outcome.status, "gated")
        self.assertEqual(core.calls, 1)
        self.assertEqual(graph.calls, 1)

    def test_turn_runner_persists_one_root_turn_and_final_response(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="test")
            run_id, _, _ = run_store.create_run(request, "core.conversation.answer")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
                intent="core.conversation.answer",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=_FakeMainAgent(),
                graph_adapter=_UnusedGraphAdapter(),
            )

            response = runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                response_factory=lambda result: ResponseEnvelope(
                    request_id=result.request_id,
                    run_id=result.run_id,
                    mode="core",
                    intent=result.intent,
                    status="completed",
                    message=result.message,
                    progress=ProgressState(
                        current_phase="core",
                        active_agent="main_agent",
                        latest_event="completed",
                        status_message="done",
                        percent=100,
                    ),
                ),
            )

            root = run_store.load_run_paths(run_id).root
            turn = read_json(root / "turn_state.json")
            self.assertEqual(turn["status"], "completed")
            self.assertEqual(turn["execution_mode"], "core")
            self.assertTrue(turn["config_hash"])
            self.assertEqual(turn["sequence"], 4)
            self.assertTrue(turn["last_event_id"])
            self.assertEqual(read_json(root / "response.json")["status"], "completed")
            event_types = [event["event_type"] for event in read_jsonl(root / "execution_events.jsonl")]
            self.assertEqual(
                event_types,
                ["turn_received", "task_selected", "turn_started", "turn_completed"],
            )
            self.assertEqual(response.message, "done")

    def test_terminal_turn_cannot_be_restarted_as_a_resume(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="test")
            run_id, _, _ = run_store.create_run(request, "core.conversation.answer")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=_FakeMainAgent(),
                graph_adapter=_UnusedGraphAdapter(),
            )
            runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                response_factory=lambda result: ResponseEnvelope(
                    request_id=result.request_id,
                    run_id=result.run_id,
                    mode="core",
                    status="completed",
                    message=result.message,
                    progress=ProgressState(
                        current_phase="core",
                        active_agent="main_agent",
                        latest_event="completed",
                        status_message="done",
                        percent=100,
                    ),
                ),
            )
            with self.assertRaisesRegex(ValueError, "terminal"):
                runner.execute_task(
                    request=request,
                    decision=decision,
                    run_id=run_id,
                    thread_id="thread-1",
                    execution_mode="core",
                    resume=True,
                )

    def test_explicit_resume_recovers_a_persisted_running_turn(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="test")
            run_id, _, _ = run_store.create_run(request, "core.conversation.answer")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=_FakeMainAgent(),
                graph_adapter=_UnusedGraphAdapter(),
            )
            runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                response_factory=lambda result: ResponseEnvelope(
                    request_id=result.request_id,
                    run_id=result.run_id,
                    mode="core",
                    status="completed",
                    message=result.message,
                    progress=ProgressState(
                        current_phase="core",
                        active_agent="main_agent",
                        latest_event="completed",
                        status_message="done",
                        percent=100,
                    ),
                ),
            )
            root = run_store.load_run_paths(run_id).root
            turn = read_json(root / "turn_state.json")
            turn["status"] = "running"
            write_json(root / "turn_state.json", turn)

            outcome = runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                resume=True,
            )

            self.assertEqual(outcome.status, "completed")
            self.assertEqual(read_json(root / "turn_state.json")["status"], "completed")
            self.assertIn(
                "turn_recovered_after_worker_restart",
                [event["event_type"] for event in read_jsonl(root / "execution_events.jsonl")],
            )


class GraphExecutorTests(unittest.TestCase):
    def test_graph_executor_delegates_stateful_graph_work_to_adapter(self) -> None:
        class Adapter:
            def __init__(self) -> None:
                self.calls = 0

            def step(self, context: TurnContext) -> ExecutionRunResult:
                self.calls += 1
                return ExecutionRunResult(
                    snapshot=ExecutionRunSnapshot(
                        run_id=context.run_id,
                        thread_id=context.thread_id,
                        request_id=context.request.request_id,
                        graph=ExecutionGraph(domain_id="test", objective="test"),
                        status="gated",
                    )
                )

        adapter = Adapter()
        request = RequestEnvelope(user_query="graph")
        decision = RequestIntakeDecision(
            status="accepted",
            request_kind="domain_task",
            recommended_mode="graph",
        )
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            run_store.create_run(request, "domain:test", run_id="run-1")
            context = TurnContext(
                request=request,
                decision=decision,
                run_id="run-1",
                thread_id="thread-1",
                turn=TurnSnapshot(
                    run_id="run-1",
                    thread_id="thread-1",
                    request_id=request.request_id,
                ),
                config=TurnConfig(execution_mode="graph"),
                run_store=run_store,
                main_agent=None,
            )
            outcome = GraphExecutor(adapter).execute(context)

        self.assertEqual(outcome.status, "gated")
        self.assertEqual(outcome.execution_mode, "graph")
        self.assertTrue(outcome.graph_state_ref.endswith("execution_snapshot.json"))
        self.assertEqual(adapter.calls, 1)


if __name__ == "__main__":
    unittest.main()
