from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep

from research_system.runtime.storage.runs.run_store import RunStore
from research_system.runtime.turn_runner import (
    GraphExecutor,
    RegularExecutor,
    TurnContext,
    TurnRunner,
)
from research_system.schemas.core_runtime import MainAgentRunResult, MainAgentState, RequestIntakeDecision
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.execution_runtime import (
    ExecutionRunResult,
    ExecutionRunSnapshot,
    GraphStepOutcome,
)
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


class _FakeCoreExecutor:
    def execute(self, context: TurnContext) -> TaskOutcome:
        state = MainAgentState(
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            status="completed",
            mode="core",
        )
        return TaskOutcome(
            execution_mode="core",
            status="completed",
            result=MainAgentRunResult(
                run_id=state.run_id,
                thread_id=state.thread_id,
                request_id=state.request_id,
                status="completed",
                mode="core",
                message="done",
                state=state,
            ),
        )


class _UnusedGraphAdapter:
    def execute(self, _context: TurnContext) -> ExecutionRunResult:
        raise AssertionError("Graph adapter must not run in the core test")


class TurnRunnerTests(unittest.TestCase):
    def test_unbounded_deadline_policy_bypasses_the_runner_default(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(
                user_query="wait for upstream completion",
                constraints={"turn_config": {"deadline_policy": "unbounded"}},
            )
            run_id, _, _ = run_store.create_run(request, "core.conversation.answer")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=_UnusedGraphAdapter(),
                core_executor=_RecordingExecutor("completed"),
                timeout_seconds=60,
            )

            runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-unbounded",
                execution_mode="core",
            )

            turn = TurnSnapshot.model_validate(
                read_json(run_store.load_run_paths(run_id).root / "turn_state.json")
            )
            self.assertEqual(turn.config.deadline_policy, "unbounded")
            self.assertIsNone(turn.config.timeout_seconds)
            self.assertIsNone(turn.deadline_at)

    def test_terminal_outcome_is_not_rewritten_after_deadline(self) -> None:
        class LateTerminalExecutor:
            def __init__(self, status: str) -> None:
                self.status = status

            def execute(self, _context: TurnContext) -> TaskOutcome:
                sleep(0.01)
                return TaskOutcome(
                    execution_mode="core",
                    status=self.status,
                    result=self.status,
                )

        for terminal_status in ("completed", "completed_partial"):
            with (
                self.subTest(status=terminal_status),
                TemporaryDirectory() as temporary_directory,
            ):
                run_store = RunStore(Path(temporary_directory) / "runs")
                request = RequestEnvelope(user_query="finish at the deadline")
                run_id, paths, _ = run_store.create_run(
                    request,
                    "core.conversation.answer",
                )
                decision = RequestIntakeDecision(
                    status="accepted",
                    request_kind="conversation",
                    recommended_mode="core",
                )
                runner = TurnRunner(
                    run_store=run_store,
                    main_agent=object(),
                    graph_adapter=_UnusedGraphAdapter(),
                    core_executor=LateTerminalExecutor(terminal_status),
                    timeout_seconds=0.001,
                )

                outcome = runner.execute_task(
                    request=request,
                    decision=decision,
                    run_id=run_id,
                    thread_id="thread-late-terminal",
                    execution_mode="core",
                )
                write_json(
                    paths.root / "summary.json",
                    {"status": "received", "message": "pending"},
                )

                def response_factory(
                    _result,
                    *,
                    request_id=request.request_id,
                    current_run_id=run_id,
                    status=terminal_status,
                ) -> ResponseEnvelope:
                    return ResponseEnvelope(
                        request_id=request_id,
                        run_id=current_run_id,
                        status=status,
                        message="done",
                        progress=ProgressState(
                            current_phase="complete",
                            status_message="done",
                            percent=100,
                        ),
                    )

                response = runner.finalize(
                    outcome=outcome,
                    response_factory=response_factory,
                )
                turn = TurnSnapshot.model_validate(read_json(paths.turn_state))
                run_state = read_json(paths.run_state)
                summary = read_json(paths.root / "summary.json")
                persisted_response = read_json(paths.root / "response.json")
                events = read_jsonl(paths.root / "execution_events.jsonl")

                self.assertEqual(outcome.status, terminal_status)
                self.assertEqual(turn.status, terminal_status)
                self.assertEqual(response.status, terminal_status)
                self.assertEqual(run_state["status"], terminal_status)
                self.assertEqual(summary["status"], terminal_status)
                self.assertEqual(persisted_response["status"], terminal_status)
                self.assertTrue(any(event["event_type"] == "turn_deadline_exceeded_after_terminal" for event in events))
                self.assertFalse(any(event["event_type"] == "turn_timed_out" for event in events))

    def test_nonterminal_outcome_is_still_timed_out_after_deadline(self) -> None:
        class LatePausedExecutor:
            def execute(self, _context: TurnContext) -> TaskOutcome:
                sleep(0.01)
                return TaskOutcome(
                    execution_mode="core",
                    status="paused",
                    result="paused",
                )

        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="pause too late")
            run_id, paths, _ = run_store.create_run(request, "core.conversation.answer")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=_UnusedGraphAdapter(),
                core_executor=LatePausedExecutor(),
                timeout_seconds=0.001,
            )

            outcome = runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-late-paused",
                execution_mode="core",
            )
            response = runner.finalize(
                outcome=outcome,
                response_factory=lambda _result: ResponseEnvelope(
                    request_id=request.request_id,
                    run_id=run_id,
                    status="paused",
                    message="paused",
                    progress=ProgressState(
                        current_phase="paused",
                        latest_event="turn_paused",
                        status_message="paused",
                        percent=50,
                    ),
                    result={"delivery_status": "paused"},
                ),
            )
            turn = TurnSnapshot.model_validate(read_json(paths.turn_state))
            run_state = read_json(paths.run_state)
            summary = read_json(paths.root / "summary.json")

            self.assertEqual(outcome.status, "timed_out")
            self.assertEqual(turn.status, "timed_out")
            self.assertEqual(response.status, "timed_out")
            self.assertNotEqual(response.message, "paused")
            self.assertEqual(response.progress.latest_event, "turn_timed_out")
            self.assertEqual(response.result["delivery_status"], "timed_out")
            self.assertEqual(response.result["turn_status"], "timed_out")
            self.assertEqual(run_state["status"], "timed_out")
            self.assertEqual(summary["status"], "timed_out")
            self.assertEqual(summary["message"], response.message)

    def test_finalize_uses_paused_turn_status_over_completed_graph_payload(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="pause after graph completion")
            run_id, paths, _ = run_store.create_run(request, "stock_check")
            turn = TurnSnapshot(
                run_id=run_id,
                thread_id="thread-stop-hook",
                request_id=request.request_id,
                execution_mode="graph",
                status="paused",
            )
            write_json(paths.turn_state, turn.model_dump(mode="json"))
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=_UnusedGraphAdapter(),
                core_executor=_RecordingExecutor("completed"),
                timeout_seconds=60,
            )
            outcome = TaskOutcome(
                execution_mode="graph",
                status="paused",
                result="graph snapshot says completed",
            )

            response = runner.finalize(
                outcome=outcome,
                response_factory=lambda _result: ResponseEnvelope(
                    request_id=request.request_id,
                    run_id=run_id,
                    mode="graph",
                    status="completed",
                    message="graph completed",
                    progress=ProgressState(
                        current_phase="complete",
                        latest_event="graph_run_finished",
                        status_message="graph completed",
                        percent=100,
                    ),
                    result={
                        "status": "completed",
                        "delivery_status": "completed",
                    },
                ),
            )
            run_state = read_json(paths.run_state)
            summary = read_json(paths.root / "summary.json")

            self.assertEqual(response.status, "paused")
            self.assertNotEqual(response.message, "graph completed")
            self.assertEqual(response.progress.latest_event, "turn_paused")
            self.assertEqual(response.result["status"], "paused")
            self.assertEqual(response.result["delivery_status"], "paused")
            self.assertEqual(response.result["turn_status"], "paused")
            self.assertEqual(run_state["status"], "paused")
            self.assertEqual(summary["status"], "paused")

    def test_timeout_recovery_creates_a_same_run_continuation(self) -> None:
        class Executor:
            def __init__(self) -> None:
                self.calls = 0

            def execute(self, context: TurnContext) -> TaskOutcome:
                self.calls += 1
                status = "timed_out" if self.calls == 1 else "completed"
                return TaskOutcome(execution_mode="core", status=status, result=status)

        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="recover timeout")
            run_id, _, _ = run_store.create_run(request, "core.conversation.answer")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
            )
            executor = Executor()
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=_UnusedGraphAdapter(),
                core_executor=executor,
                timeout_seconds=60,
            )

            first = runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                turn_config=TurnConfig(execution_mode="core", timeout_seconds=60),
            )
            timed_out = TurnSnapshot.model_validate(
                read_json(run_store.load_run_paths(run_id).root / "turn_state.json")
            )
            self.assertEqual(first.status, "timed_out")
            self.assertEqual(timed_out.status, "timed_out")

            recovered = runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                turn_config=timed_out.config,
                resume=True,
                recover=True,
            )
            continuation = TurnSnapshot.model_validate(
                read_json(run_store.load_run_paths(run_id).root / "turn_state.json")
            )
            history = read_jsonl(run_store.load_run_paths(run_id).root / "turn_history.jsonl")
            self.assertEqual(recovered.status, "completed")
            self.assertEqual(continuation.status, "completed")
            self.assertEqual(continuation.parent_turn_id, timed_out.turn_id)
            self.assertEqual(continuation.relation, "continuation")
            self.assertEqual(history[0]["turn"]["turn_id"], timed_out.turn_id)
            self.assertEqual(history[0]["continuation_turn_id"], continuation.turn_id)
            self.assertEqual(executor.calls, 2)

    def test_root_deadline_is_persisted_and_reused_after_resume(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="resume me")
            run_id, _, _ = run_store.create_run(request, "core.conversation.answer")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
            )
            executor = _RecordingExecutor("paused")
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=_UnusedGraphAdapter(),
                core_executor=executor,
                timeout_seconds=60,
            )

            runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                turn_config=TurnConfig(execution_mode="core", timeout_seconds=60),
            )
            first = TurnSnapshot.model_validate(
                read_json(run_store.load_run_paths(run_id).root / "turn_state.json")
            )

            runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                resume=True,
            )
            second = TurnSnapshot.model_validate(
                read_json(run_store.load_run_paths(run_id).root / "turn_state.json")
            )

            self.assertIsNotNone(first.deadline_at)
            self.assertEqual(first.deadline_at, second.deadline_at)
            self.assertEqual(executor.calls, 2)

    def test_graph_safe_points_share_the_root_deadline(self) -> None:
        class Adapter:
            def __init__(self) -> None:
                self.calls = 0
                self.deadlines: list[float | None] = []

            def step(self, context: TurnContext) -> GraphStepOutcome:
                self.calls += 1
                self.deadlines.append(context.deadline_at)
                snapshot = ExecutionRunSnapshot(
                    run_id=context.run_id,
                    thread_id=context.thread_id,
                    request_id=context.request.request_id,
                    graph=ExecutionGraph(domain_id="test", objective="test"),
                    status="paused" if self.calls == 1 else "completed",
                )
                return GraphStepOutcome(
                    result=ExecutionRunResult(snapshot=snapshot),
                    progressed=True,
                    safe_point=True,
                    should_continue=self.calls == 1,
                )

        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="graph")
            run_id, _, _ = run_store.create_run(request, "domain:test")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="domain_task",
                recommended_mode="graph",
                intent="domain:test",
            )
            adapter = Adapter()
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=adapter,
                timeout_seconds=60,
            )

            outcome = runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="graph",
                turn_config=TurnConfig(execution_mode="graph", timeout_seconds=60),
            )

            self.assertEqual(outcome.status, "completed")
            self.assertEqual(adapter.calls, 2)
            self.assertIsNotNone(adapter.deadlines[0])
            self.assertEqual(adapter.deadlines[0], adapter.deadlines[1])

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
                core_executor=_FakeCoreExecutor(),
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
            self.assertEqual(turn["sequence"], 5)
            self.assertTrue(turn["last_event_id"])
            self.assertEqual(read_json(root / "response.json")["status"], "completed")
            event_types = [event["event_type"] for event in read_jsonl(root / "execution_events.jsonl")]
            self.assertEqual(
                event_types,
                [
                    "turn_received",
                    "turn_ready",
                    "task_selected",
                    "turn_started",
                    "turn_completed",
                ],
            )
            self.assertEqual(response.message, "done")

    def test_finalize_rejects_artifact_ids_not_owned_by_the_root_turn(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="artifact check")
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
                core_executor=_FakeCoreExecutor(),
            )
            with self.assertRaisesRegex(ValueError, "outside the current Run"):
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
                        artifact_ids=["artifact-from-another-run"],
                        progress=ProgressState(
                            current_phase="core",
                            active_agent="main_agent",
                            latest_event="completed",
                            status_message="done",
                            percent=100,
                        ),
                    ),
                )

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
                core_executor=_FakeCoreExecutor(),
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
                core_executor=_FakeCoreExecutor(),
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

    def test_resume_uses_the_persisted_turn_config(self) -> None:
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
                core_executor=_FakeCoreExecutor(),
            )
            original_config = TurnConfig(task_policy_ref="persisted-v1")
            runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                turn_config=original_config,
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
            changed_request = request.model_copy(
                update={"constraints": {"turn_config": {"task_policy_ref": "changed-v2"}}}
            )

            outcome = runner.execute_task(
                request=changed_request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                resume=True,
            )

            self.assertEqual(outcome.status, "completed")
            self.assertEqual(
                read_json(root / "turn_state.json")["config"]["task_policy_ref"],
                "persisted-v1",
            )

    def test_turn_worker_lease_rejects_a_second_executor(self) -> None:
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
                core_executor=_FakeCoreExecutor(),
            )
            write_json(
                run_store.load_run_paths(run_id).root / "turn_worker_lease.json",
                {
                    "owner": "other-worker",
                    "run_id": run_id,
                    "expires_at": "2999-01-01T00:00:00+00:00",
                },
            )

            with self.assertRaisesRegex(ValueError, "already being executed"):
                runner.execute_task(
                    request=request,
                    decision=decision,
                    run_id=run_id,
                    thread_id="thread-1",
                    execution_mode="core",
                )

    def test_graph_resume_consumes_the_previous_pause_request(self) -> None:
        class Adapter:
            def __init__(self) -> None:
                self.runner = None
                self.calls = 0

            def step(self, context: TurnContext) -> ExecutionRunResult:
                self.calls += 1
                if self.calls == 1:
                    assert self.runner is not None
                    self.runner.pause(context.run_id, reason="safe point pause")
                    status = "paused"
                else:
                    status = "completed"
                return ExecutionRunResult(
                    snapshot=ExecutionRunSnapshot(
                        run_id=context.run_id,
                        thread_id=context.thread_id,
                        request_id=context.request.request_id,
                        graph=ExecutionGraph(domain_id="test", objective="test"),
                        status=status,
                    )
                )

        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="pause graph")
            run_id, _, _ = run_store.create_run(request, "domain:test")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="domain_task",
                recommended_mode="graph",
                intent="domain:test",
            )
            adapter = Adapter()
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=adapter,
            )
            adapter.runner = runner

            first = runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="graph",
            )
            second = runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="graph",
                resume=True,
            )

            control = read_json(run_store.load_run_paths(run_id).root / "execution_control.json")
            self.assertEqual(first.status, "paused")
            self.assertEqual(second.status, "completed")
            self.assertEqual(adapter.calls, 2)
            self.assertFalse(control["pause_requested"])
            self.assertFalse(control["cancel_requested"])

    def test_resume_preserves_a_cancel_request_recorded_while_paused(self) -> None:
        class ControlAwareExecutor:
            def execute(self, context: TurnContext) -> TaskOutcome:
                control = read_json(context.root / "execution_control.json") or {}
                status = "cancelled" if control.get("cancel_requested") else "paused"
                return TaskOutcome(execution_mode="core", status=status, result=status)

        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="cancel me")
            run_id, _, _ = run_store.create_run(request, "core.conversation.answer")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=_UnusedGraphAdapter(),
                core_executor=ControlAwareExecutor(),
            )

            first = runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
            )
            runner.cancel(run_id, reason="cancel while paused")
            second = runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                resume=True,
            )

            self.assertEqual(first.status, "paused")
            self.assertEqual(second.status, "cancelled")
            events = read_jsonl(run_store.load_run_paths(run_id).root / "execution_events.jsonl")
            self.assertTrue(
                any(
                    event["event_type"] == "turn_control_requested"
                    and event["payload"]["action"] == "cancel"
                    for event in events
                )
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
