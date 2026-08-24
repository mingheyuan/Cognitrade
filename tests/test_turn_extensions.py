from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.agent_os import AgentOS
from research_system.api.agent_os_admin_service import AgentOSAdminService
from research_system.runtime.core.approvals import HumanApprovalStore
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.follow_up import FollowUpRuntime
from research_system.runtime.hooks import HookRuntime, StopHookInput
from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.jsonl import read_json, read_jsonl, write_json
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.runtime.turn_runner import GraphExecutor, TurnContext, TurnRunner
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.core_runtime import MainAgentRunResult, MainAgentState, RequestIntakeDecision
from research_system.schemas.subagents import RoleContract
from research_system.schemas.execution_graph import ExecutionGraph, ExecutionNode
from research_system.schemas.execution_runtime import (
    ExecutionRunResult,
    ExecutionRunSnapshot,
    ExecutionUsage,
    NodeExecutionState,
)
from research_system.schemas.protocol import ProgressState, RequestEnvelope, ResponseEnvelope
from research_system.schemas.turn_runtime import (
    CompactConfig,
    FollowupTaskRequest,
    HookConfig,
    HookPolicy,
    FollowUpConfig,
    TaskOutcome,
    TurnConfig,
    TurnSnapshot,
)


class TurnExtensionTests(unittest.TestCase):
    def test_next_turn_mailbox_is_promoted_before_delivery(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = FollowUpRuntime(
                root=root,
                run_id="run-1",
                turn_id="turn-1",
                config=FollowUpConfig(),
            )
            queued = runtime.enqueue(
                source="system",
                kind="context",
                text="deliver on the next turn",
                operation_id="next-turn-1",
                delivery_phase="next_turn",
            )

            self.assertEqual(runtime.deliver_current(), [])
            next_runtime = FollowUpRuntime(
                root=root,
                run_id="run-1",
                turn_id="turn-2",
                config=FollowUpConfig(),
            )
            delivered = next_runtime.deliver_current()

            self.assertEqual([item.input_id for item in delivered], [queued.input_id])
            self.assertEqual(delivered[0].target_turn_id, "turn-2")

    def test_followup_operation_receipt_survives_mailbox_delivery(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = FollowUpRuntime(
                root=root,
                run_id="run-1",
                turn_id="turn-1",
                config=FollowUpConfig(),
            )
            first = runtime.enqueue(
                source="user",
                kind="user_input",
                text="continue",
                operation_id="op-1",
            )
            delivered = runtime.deliver_current()
            duplicate = runtime.enqueue(
                source="user",
                kind="user_input",
                text="continue",
                operation_id="op-1",
            )
            state = read_json(root / "task_state.json")
            self.assertEqual([item.input_id for item in delivered], [first.input_id])
            self.assertEqual(duplicate.input_id, first.input_id)
            self.assertEqual(state["operation_receipts"]["op-1"]["status"], "delivered")

    def test_child_followup_receipt_remains_idempotent_after_delivery(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = FollowUpRuntime(
                root=root,
                run_id="run-1",
                turn_id="turn-1",
                config=FollowUpConfig(),
            )
            request = FollowupTaskRequest(
                parent_turn_id="turn-1",
                target_child_turn_id="child-1",
                target_agent_id="analyst",
                message="recheck",
                operation_id="child-op-1",
            )
            first = runtime.followup_task(request, known_child_turn_ids={"child-1"})
            runtime.deliver_for_turn("child-1")
            duplicate = runtime.followup_task(request, known_child_turn_ids={"child-1"})
            state = read_json(root / "task_state.json")
            self.assertTrue(first.accepted)
            self.assertTrue(duplicate.duplicate)
            self.assertTrue(duplicate.accepted)
            self.assertEqual(
                state["operation_receipts"]["child-op-1"]["kind"],
                "followup_task",
            )

    def test_graph_nodes_are_projected_as_child_turns_in_the_root_turn(self) -> None:
        class Adapter:
            def step(self, context):
                graph = ExecutionGraph(
                    domain_id="test",
                    objective="test",
                    nodes=[
                        ExecutionNode(
                            node_id="analyst",
                            node_kind="subagent",
                            executor="research.analyst",
                            capability="research.analysis",
                        )
                    ],
                )
                return ExecutionRunResult(
                    snapshot=ExecutionRunSnapshot(
                        run_id=context.run_id,
                        thread_id=context.thread_id,
                        request_id=context.request.request_id,
                        user_id=context.request.user_id,
                        tenant_id=context.request.tenant_id,
                        graph=graph,
                        status="completed",
                        node_states={
                            "analyst": NodeExecutionState(
                                node_id="analyst",
                                status="completed",
                                output_artifact_ids=[],
                                usage=ExecutionUsage(tokens=7, cost=0.2, duration_seconds=1.5),
                            )
                        },
                    )
                )

        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(
                user_query="graph",
                user_id="user-1",
                tenant_id="tenant-1",
            )
            run_id, _, _ = run_store.create_run(request, "domain:test")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="domain_task",
                recommended_mode="graph",
                intent="domain:test",
            )
            seen_stops: list[StopHookInput] = []
            hooks = HookRuntime()
            hooks.register(
                "subagent_stop",
                lambda value: seen_stops.append(value) or {"should_block": False},
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=Adapter(),
                hook_runtime=hooks,
            )
            response = runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="graph",
                response_factory=lambda result: ResponseEnvelope(
                    request_id=request.request_id,
                    run_id=run_id,
                    status="completed",
                    message="done",
                    progress=ProgressState(
                        current_phase="graph",
                        active_agent="main_agent",
                        latest_event="turn_completed",
                        status_message="done",
                        percent=100,
                    ),
                ),
            )
            root = run_store.load_run_paths(run_id).root
            turn = TurnSnapshot.model_validate(read_json(root / "turn_state.json"))
            self.assertEqual(len(turn.child_turns), 1)
            self.assertEqual(turn.child_turns[0].parent_node_id, "analyst")
            self.assertEqual(response.child_turn_ids, [turn.child_turns[0].child_turn_id])
            self.assertTrue(read_json(root / "child_turns.json"))
            self.assertEqual(
                turn.child_turns[0].usage,
                {"tokens": 7, "cost": 0.2, "duration_seconds": 1.5},
            )
            self.assertEqual(len(seen_stops), 1)
            transcript_path = Path(seen_stops[0].agent_transcript_ref or "")
            self.assertTrue(transcript_path.exists())
            self.assertNotEqual(transcript_path, root / "task_state.json")
            child_events = read_jsonl(
                root
                / "child_turns"
                / str(turn.child_turns[0].child_turn_id).replace(":", "__")
                / "execution_events.jsonl"
            )
            self.assertTrue(child_events)
            self.assertTrue(
                all(
                    event["user_id"] == "user-1" and event["tenant_id"] == "tenant-1"
                    for event in child_events
                )
            )

    def test_review_and_compact_tasks_execute_inside_the_same_turn_lifecycle(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="task", constraints={"task_type": "review"})
            run_id, _, _ = run_store.create_run(request, "review")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
                intent="review",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=object(),
            )
            response = runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                response_factory=lambda result: ResponseEnvelope(
                    request_id=request.request_id,
                    run_id=run_id,
                    status=result.status,
                    message=result.message,
                    progress=ProgressState(
                        current_phase="task",
                        active_agent="main_agent",
                        latest_event="turn_completed",
                        status_message="done",
                        percent=100,
                    ),
                ),
            )
            self.assertEqual(response.task_type, "review")
            self.assertEqual(response.status, "completed")

    def test_review_findings_gate_the_turn_without_final_artifact(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_store = RunStore(root / "runs")
            request = RequestEnvelope(
                user_query="review",
                constraints={"task_type": "review"},
                user_id="user-1",
                tenant_id="tenant-1",
            )
            run_id, paths, _ = run_store.create_run(request, "review")
            ArtifactBoard(paths.root / "artifact_board.jsonl").add(
                ArtifactRecord(
                    artifact_type="source_document",
                    run_id=run_id,
                    thread_id="thread-1",
                    request_id=request.request_id,
                    user_id=request.user_id,
                    tenant_id=request.tenant_id,
                    producer=ArtifactProducer(
                        executor="test",
                        capability="test.source",
                    ),
                    content={"text": "evidence"},
                )
            )
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
                intent="review",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=object(),
            )

            response = runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                response_factory=lambda result: ResponseEnvelope(
                    request_id=request.request_id,
                    run_id=run_id,
                    status=result.status,
                    message=result.message,
                    progress=ProgressState(
                        current_phase="review",
                        active_agent="main_agent",
                        latest_event="turn_gated",
                        status_message="review required",
                        percent=0,
                    ),
                ),
            )

            turn = TurnSnapshot.model_validate(read_json(paths.root / "turn_state.json"))
            self.assertEqual(response.status, "gated")
            self.assertEqual(turn.status, "gated")
            self.assertTrue(response.artifact_ids)
            self.assertEqual(response.final_artifact_ids, [])
            self.assertEqual(response.continuation["source"], "review")

    def test_stop_hook_blocks_with_a_persisted_continuation(self) -> None:
        class Adapter:
            def step(self, context):
                return ExecutionRunResult(
                    snapshot=ExecutionRunSnapshot(
                        run_id=context.run_id,
                        thread_id=context.thread_id,
                        request_id=context.request.request_id,
                        graph=ExecutionGraph(domain_id="test", objective="test"),
                        status="completed",
                    )
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
            hooks = HookRuntime()
            hooks.register(
                "stop",
                lambda _input: {
                    "should_block": True,
                    "block_reason": "source review is required",
                },
                name="source-review",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=Adapter(),
                hook_runtime=hooks,
            )
            response = runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="graph",
                response_factory=lambda result: ResponseEnvelope(
                    request_id=request.request_id,
                    run_id=run_id,
                    status="paused",
                    message="continuation required",
                    progress=ProgressState(
                        current_phase="graph",
                        active_agent="main_agent",
                        latest_event="turn_paused",
                        status_message="paused",
                        percent=0,
                    ),
                ),
            )
            root = run_store.load_run_paths(run_id).root
            turn = TurnSnapshot.model_validate(read_json(root / "turn_state.json"))
            task = read_json(root / "task_state.json")
            events = read_jsonl(root / "execution_events.jsonl")

            self.assertEqual(response.status, "paused")
            self.assertEqual(turn.status, "paused")
            self.assertEqual(task["continuation"]["source"], "stop_hook")
            self.assertTrue(any(event["event_type"] == "hook_started" for event in events))
            self.assertTrue(any(event["event_type"] == "hook_completed" for event in events))
            self.assertTrue(any(event["event_type"] == "stop_hook_blocked" for event in events))

    def test_graph_stop_hook_continuation_resumes_inside_the_same_turn(self) -> None:
        class Adapter:
            def __init__(self) -> None:
                self.calls = 0

            def step(self, context):
                self.calls += 1
                return ExecutionRunResult(
                    snapshot=ExecutionRunSnapshot(
                        run_id=context.run_id,
                        thread_id=context.thread_id,
                        request_id=context.request.request_id,
                        graph=ExecutionGraph(domain_id="test", objective="test"),
                        status="completed",
                    )
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
            hooks = HookRuntime()
            hooks.register(
                "stop",
                lambda _input: {
                    "should_block": True,
                    "block_reason": "review the completed graph",
                },
            )
            adapter = Adapter()
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=adapter,
                hook_runtime=hooks,
            )
            first = runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="graph",
                response_factory=lambda result: ResponseEnvelope(
                    request_id=request.request_id,
                    run_id=run_id,
                    mode="graph",
                    status="running",
                    message="continuation required",
                    progress=ProgressState(
                        current_phase="graph",
                        active_agent="main_agent",
                        latest_event="turn_paused",
                        status_message="paused",
                        percent=0,
                    ),
                ),
            )
            resumed = runner.resume(run_id)

            self.assertEqual(first.status, "paused")
            self.assertNotEqual(first.message, "continuation required")
            self.assertEqual(first.progress.current_phase, "paused")
            self.assertEqual(first.progress.latest_event, "turn_paused")
            self.assertEqual(resumed.status, "completed")
            self.assertEqual(adapter.calls, 1)
            self.assertEqual(
                read_json(run_store.load_run_paths(run_id).root / "task_state.json")["task_type"],
                "review",
            )
            self.assertTrue(
                any(
                    artifact.get("artifact_type") == "review_result"
                    for artifact in read_jsonl(
                        run_store.load_run_paths(run_id).root / "artifact_board.jsonl"
                    )
                )
            )

    def test_follow_up_mailbox_is_idempotent_and_rejects_root_child_followup(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = FollowUpRuntime(
                root=root,
                run_id="run-1",
                turn_id="turn-root",
            )
            item = runtime.enqueue(
                source="user",
                kind="user_input",
                text="continue",
                operation_id="op-1",
            )
            duplicate = runtime.enqueue(
                source="user",
                kind="user_input",
                text="continue",
                operation_id="op-1",
            )
            delivered = runtime.deliver_current()
            rejected = runtime.followup_task(
                FollowupTaskRequest(
                    parent_turn_id="turn-root",
                    target_child_turn_id="turn-root",
                    target_agent_id="root",
                    message="do more",
                )
            )

            self.assertEqual(item.input_id, duplicate.input_id)
            self.assertEqual([row.input_id for row in delivered], [item.input_id])
            self.assertFalse(rejected.accepted)
            self.assertEqual(rejected.delivery, "rejected")

    def test_mailbox_delivery_does_not_drop_an_external_enqueue(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            worker = FollowUpRuntime(root=root, run_id="run-1", turn_id="turn-root")
            external = FollowUpRuntime(root=root, run_id="run-1", turn_id="turn-root")

            worker.enqueue(
                source="user",
                kind="user_input",
                text="first",
                operation_id="op-first",
            )
            self.assertEqual([item.operation_id for item in worker.deliver_current()], ["op-first"])
            external.enqueue(
                source="user",
                kind="user_input",
                text="second",
                operation_id="op-second",
            )

            worker.update_transcript([{"role": "assistant", "content": "checkpoint"}])

            persisted = FollowUpRuntime(root=root, run_id="run-1", turn_id="turn-root")
            self.assertEqual(
                [item.operation_id for item in persisted.state.pending_inputs],
                ["op-second"],
            )

    def test_root_stop_hook_uses_stop_target_and_identity(self) -> None:
        seen = []
        runtime = HookRuntime()
        runtime.register("stop", lambda value: seen.append(value) or None)
        outcome = runtime.run_stop(
            StopHookInput(
                session_id="thread-1",
                run_id="run-1",
                thread_id="thread-1",
                turn_id="turn-1",
                task_type="regular",
                target="stop",
            )
        )
        self.assertTrue(outcome.should_stop)
        self.assertEqual(seen[0].run_id, "run-1")
        self.assertEqual(seen[0].target, "stop")

    def test_session_and_user_prompt_hooks_run_inside_the_turn(self) -> None:
        seen: list[tuple[str, str]] = []

        class MainAgent:
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
                    intent="core.conversation.answer",
                    message=kwargs["request"].user_query,
                    state=state,
                )

        class CoreExecutor:
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
                        intent="core.conversation.answer",
                        message=context.request.user_query,
                        state=state,
                    ),
                )

        hooks = HookRuntime()
        hooks.register("SessionStart", lambda value: seen.append((value.event, value.turn_id)))
        hooks.register(
            "UserPromptSubmit",
            lambda _value: {"user_query": "rewritten by hook"},
        )
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="original")
            run_id, _, _ = run_store.create_run(request, "core.conversation.answer")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
                intent="core.conversation.answer",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=MainAgent(),
                graph_adapter=object(),
                core_executor=CoreExecutor(),
                hook_runtime=hooks,
            )
            response = runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                turn_config=TurnConfig(
                    execution_mode="core",
                    hook_config=HookConfig(
                        policy=HookPolicy(allow_input_rewrite=True),
                    ),
                    follow_up_config=FollowUpConfig(),
                ),
                response_factory=lambda result: ResponseEnvelope(
                    request_id=request.request_id,
                    run_id=run_id,
                    status=result.status,
                    message=result.message,
                    progress=ProgressState(
                        current_phase="core",
                        active_agent="main_agent",
                        latest_event="turn_completed",
                        status_message="done",
                        percent=100,
                    ),
                ),
            )

            self.assertEqual(response.message, "rewritten by hook")
            self.assertEqual([event for event, _ in seen], ["SessionStart"])
            events = read_jsonl(run_store.load_run_paths(run_id).root / "execution_events.jsonl")
            self.assertTrue(
                any(event.get("payload", {}).get("hook_event") == "SessionStart" for event in events)
            )
            self.assertTrue(
                any(event.get("payload", {}).get("hook_event") == "UserPromptSubmit" for event in events)
            )
            self.assertTrue(any(event["event_type"] == "user_prompt_rewritten" for event in events))

    def test_unprojected_graph_node_never_runs_subagent_stop_as_root(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="graph")
            run_id, paths, _ = run_store.create_run(request, "domain:test")
            turn = TurnSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                execution_mode="graph",
                config=TurnConfig(
                    execution_mode="graph",
                    hook_config=HookConfig(),
                    follow_up_config=FollowUpConfig(),
                ),
            )
            hooks = HookRuntime()
            seen: list[StopHookInput] = []
            hooks.register(
                "subagent_stop",
                lambda value: seen.append(value)
                or {"should_block": True, "block_reason": "must not run"},
            )
            context = TurnContext(
                request=request,
                decision=RequestIntakeDecision(
                    status="accepted",
                    request_kind="domain_task",
                    recommended_mode="graph",
                ),
                run_id=run_id,
                thread_id="thread-1",
                turn=turn,
                config=turn.config,
                run_store=run_store,
                main_agent=object(),
                hook_runtime=hooks,
                follow_up_runtime=FollowUpRuntime(
                    root=paths.root,
                    run_id=run_id,
                    turn_id=turn.turn_id,
                    config=turn.config.follow_up_config,
                ),
            )
            graph = ExecutionGraph(
                domain_id="test",
                objective="test",
                nodes=[
                    ExecutionNode(
                        node_id="worker",
                        node_kind="subagent",
                        executor="worker",
                        capability="test.worker",
                    )
                ],
            )
            outcome = TaskOutcome(
                execution_mode="graph",
                status="completed",
                result=ExecutionRunResult(
                    snapshot=ExecutionRunSnapshot(
                        run_id=run_id,
                        thread_id="thread-1",
                        request_id=request.request_id,
                        graph=graph,
                        status="completed",
                        node_states={
                            "worker": NodeExecutionState(
                                node_id="worker",
                                status="completed",
                            )
                        },
                    )
                ),
            )

            result = GraphExecutor._apply_subagent_stop_hooks(context, outcome)

            self.assertEqual(result.status, "completed")
            self.assertEqual(seen, [])

    def test_child_followup_creates_a_continuation_child_without_a_new_root_run(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="graph")
            run_id, _, _ = run_store.create_run(request, "domain:test")
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=object(),
            )
            turn = TurnSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                execution_mode="graph",
                config=TurnConfig(
                    execution_mode="graph",
                    follow_up_config=FollowUpConfig(),
                ),
                child_turns=[
                    {
                        "child_turn_id": "child-1",
                        "child_thread_id": "child-thread-1",
                        "parent_turn_id": "turn-root",
                        "parent_run_id": run_id,
                        "parent_node_id": "analyst",
                        "role_id": "analyst",
                        "status": "completed",
                    }
                ],
            )
            runner._persist_turn(turn)

            request_payload = FollowupTaskRequest(
                parent_turn_id=turn.turn_id,
                target_child_turn_id="child-1",
                target_agent_id="analyst",
                message="recheck the evidence",
                operation_id="followup-1",
            )
            first = runner.followup_task(run_id, request_payload)
            second = runner.followup_task(run_id, request_payload)

            self.assertTrue(first.accepted)
            self.assertEqual(first.delivery, "triggered_new_turn")
            self.assertNotEqual(first.target_turn_id, "child-1")
            self.assertTrue(second.duplicate)
            persisted = TurnSnapshot.model_validate(
                read_json(run_store.load_run_paths(run_id).root / "turn_state.json")
            )
            self.assertEqual(len(persisted.child_turns), 2)
            self.assertEqual(persisted.child_turns[-1].relation, "continuation")
            self.assertEqual(persisted.child_turns[-1].status, "ready")
            self.assertTrue(
                any(
                    event["event_type"] == "child_turn_queued"
                    for event in read_jsonl(run_store.load_run_paths(run_id).root / "execution_events.jsonl")
                )
            )
            self.assertTrue(
                any(
                    event["event_type"] == "tool_call_completed"
                    and event.get("tool_name") == "core.followup.task"
                    for event in read_jsonl(run_store.load_run_paths(run_id).root / "execution_events.jsonl")
                )
            )

    def test_child_followup_executes_continuation_when_core_executor_is_available(self) -> None:
        class Provider:
            available = True

            def __init__(self) -> None:
                self.calls = 0

            def complete_with_tools(self, **_kwargs):
                self.calls += 1
                return {"kind": "message", "message": "child completed"}

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository_root = Path(__file__).resolve().parents[1]
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                    "workspace_root": root / "workspace",
                }
            )
            agent_os = AgentOS(settings)
            provider = Provider()
            agent_os.main_agent.llm_provider = provider
            child_stop_hooks: list[StopHookInput] = []
            agent_os.turn_runner.register_hook(
                "subagent_stop",
                lambda value: child_stop_hooks.append(value)
                or {"should_stop": False, "should_block": False},
            )
            role = RoleContract(
                role_id="analyst",
                role_version="1.0.0",
                display_name="Analyst",
                objective="Recheck the supplied evidence.",
                allowed_tools=["core.message.compose"],
                allowed_permissions=["conversation:write"],
                source="pack",
            )
            request = RequestEnvelope(user_query="graph")
            run_id, _, _ = agent_os.run_store.create_run(request, "domain:test")
            turn = TurnSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                execution_mode="graph",
                config=TurnConfig(
                    execution_mode="graph",
                    hook_config=HookConfig(),
                    follow_up_config=FollowUpConfig(),
                ),
                child_turns=[
                    {
                        "child_turn_id": "child-1",
                        "child_thread_id": "child-thread-1",
                        "parent_turn_id": "turn-root",
                        "parent_run_id": run_id,
                        "parent_node_id": "analyst",
                        "role_id": "analyst",
                        "role_contract": role.model_dump(mode="json"),
                        "status": "completed",
                    }
                ],
            )
            agent_os.turn_runner._persist_turn(turn)

            request_payload = FollowupTaskRequest(
                parent_turn_id=turn.turn_id,
                target_child_turn_id="child-1",
                target_agent_id="analyst",
                message="recheck the evidence",
                operation_id="followup-execute-1",
            )
            result = agent_os.turn_runner.followup_task(run_id, request_payload)

            self.assertTrue(result.accepted)
            self.assertEqual(result.delivery, "triggered_new_turn")
            continuation_root = root / "runs" / run_id / "child_turns" / str(result.target_turn_id)
            self.assertEqual(read_json(continuation_root / "main_agent_state.json")["status"], "completed")
            child_turn = TurnSnapshot.model_validate(read_json(continuation_root / "turn_state.json"))
            self.assertGreater(child_turn.sequence, 0)
            self.assertTrue(child_turn.last_event_id)
            child_events = read_jsonl(continuation_root / "execution_events.jsonl")
            self.assertTrue(child_events)
            self.assertTrue(all(event["turn_id"] == child_turn.turn_id for event in child_events))
            self.assertEqual(
                [event["sequence"] for event in child_events],
                list(range(1, len(child_events) + 1)),
            )
            self.assertEqual(provider.calls, 1)
            self.assertEqual(len(child_stop_hooks), 1)
            self.assertEqual(child_stop_hooks[0].target, "subagent_stop")
            self.assertEqual(child_stop_hooks[0].parent_turn_id, turn.turn_id)
            self.assertEqual(
                Path(child_stop_hooks[0].agent_transcript_ref or ""),
                continuation_root / "task_state.json",
            )
            persisted = TurnSnapshot.model_validate(read_json(root / "runs" / run_id / "turn_state.json"))
            self.assertEqual(persisted.child_turns[-1].status, "completed")
            self.assertGreater(persisted.child_turns[-1].sequence, 0)
            parent_board = ArtifactBoard(root / "runs" / run_id / "artifact_board.jsonl")
            projected = parent_board.list_artifacts(thread_id=None, run_id=run_id)
            self.assertTrue(projected)
            self.assertTrue(
                all(record.thread_id == persisted.thread_id for record in projected)
            )
            self.assertTrue(
                any(
                    record.metadata.get("projected_from_child_turn")
                    == persisted.child_turns[-1].child_turn_id
                    for record in projected
                )
            )
            events = read_jsonl(root / "runs" / run_id / "execution_events.jsonl")
            self.assertTrue(any(event["event_type"] == "child_turn_completed" for event in events))

    def test_child_core_executor_enforces_role_tools_permissions_and_turn_limit(self) -> None:
        class Provider:
            available = True

            def __init__(self) -> None:
                self.calls = 0

            def complete_with_tools(self, **_kwargs):
                self.calls += 1
                return {
                    "kind": "tool_calls",
                    "tool_calls": [
                        {
                            "tool_name": "core.file.read",
                            "arguments": {"path": "should-not-run.txt"},
                            "call_id": "child-denied-tool-1",
                        }
                    ],
                }

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository_root = Path(__file__).resolve().parents[1]
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                    "workspace_root": root / "workspace",
                }
            )
            agent_os = AgentOS(settings)
            provider = Provider()
            agent_os.main_agent.llm_provider = provider
            role = RoleContract(
                role_id="restricted-analyst",
                role_version="v1",
                display_name="Restricted analyst",
                objective="Only compose a bounded response.",
                allowed_tools=["core.message.compose"],
                denied_tools=["core.file.read"],
                allowed_permissions=["conversation:write"],
                max_model_turns=1,
                source="pack",
            )
            request = RequestEnvelope(user_query="graph")
            run_id, _, _ = agent_os.run_store.create_run(request, "domain:test")
            turn = TurnSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                execution_mode="graph",
                config=TurnConfig(
                    execution_mode="graph",
                    hook_config=HookConfig(),
                    follow_up_config=FollowUpConfig(),
                ),
                child_turns=[
                    {
                        "child_turn_id": "child-restricted",
                        "child_thread_id": "child-thread-restricted",
                        "parent_turn_id": "turn-root",
                        "parent_run_id": run_id,
                        "parent_node_id": "analyst",
                        "role_id": role.role_id,
                        "role_contract": role.model_dump(mode="json"),
                        "status": "completed",
                    }
                ],
            )
            agent_os.turn_runner._persist_turn(turn)
            result = agent_os.turn_runner.followup_task(
                run_id,
                FollowupTaskRequest(
                    parent_turn_id=turn.turn_id,
                    target_child_turn_id="child-restricted",
                    target_agent_id=role.role_id,
                    message="attempt a restricted continuation",
                    operation_id="restricted-followup-1",
                ),
            )

            self.assertTrue(result.accepted)
            continuation_root = root / "runs" / run_id / "child_turns" / str(result.target_turn_id)
            child_state = read_json(continuation_root / "main_agent_state.json")
            self.assertEqual(child_state["status"], "completed_partial")
            child_turn = TurnSnapshot.model_validate(read_json(continuation_root / "turn_state.json"))
            self.assertEqual(child_turn.usage["model_turns"], 1)
            self.assertGreaterEqual(child_turn.usage["tool_calls"], 2)
            events = read_jsonl(continuation_root / "execution_events.jsonl")
            self.assertTrue(
                any(
                    event["event_type"] == "tool_call_rejected"
                    and event.get("tool_name") == "core.file.read"
                    for event in events
                )
            )
            self.assertEqual(provider.calls, 1)

    def test_child_role_approval_uses_root_approval_ledger_and_can_resume(self) -> None:
        class Provider:
            available = True

            def __init__(self) -> None:
                self.calls = 0

            def complete_with_tools(self, **_kwargs):
                self.calls += 1
                return {"kind": "message", "message": "approved child"}

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repository_root = Path(__file__).resolve().parents[1]
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                    "workspace_root": root / "workspace",
                }
            )
            agent_os = AgentOS(settings)
            provider = Provider()
            agent_os.main_agent.llm_provider = provider
            role = RoleContract(
                role_id="approval-analyst",
                role_version="v1",
                display_name="Approval analyst",
                objective="Run only after approval.",
                allowed_tools=["core.message.compose"],
                allowed_permissions=["conversation:write"],
                requires_approval=True,
                source="pack",
            )
            request = RequestEnvelope(user_query="graph")
            run_id, _, _ = agent_os.run_store.create_run(request, "domain:test")
            turn = TurnSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                execution_mode="graph",
                config=TurnConfig(
                    execution_mode="graph",
                    hook_config=HookConfig(),
                    follow_up_config=FollowUpConfig(),
                ),
                child_turns=[
                    {
                        "child_turn_id": "child-approval",
                        "child_thread_id": "child-thread-approval",
                        "parent_turn_id": "turn-root",
                        "parent_run_id": run_id,
                        "parent_node_id": "analyst",
                        "role_id": role.role_id,
                        "role_contract": role.model_dump(mode="json"),
                        "status": "completed",
                    }
                ],
            )
            agent_os.turn_runner._persist_turn(turn)
            first = agent_os.turn_runner.followup_task(
                run_id,
                FollowupTaskRequest(
                    parent_turn_id=turn.turn_id,
                    target_child_turn_id="child-approval",
                    target_agent_id=role.role_id,
                    message="wait for approval",
                    operation_id="approval-followup-1",
                ),
            )
            self.assertTrue(first.accepted)
            first_child_root = root / "runs" / run_id / "child_turns" / str(first.target_turn_id)
            self.assertEqual(read_json(first_child_root / "turn_state.json")["status"], "waiting_approval")
            approvals = HumanApprovalStore(root / "runs" / run_id / "approvals.jsonl")
            pending = approvals.list(status="pending")
            self.assertEqual(len(pending), 1)
            self.assertTrue(pending[0].node_id.startswith("child_role:"))

            AgentOSAdminService(agent_os).approve(
                run_id,
                node_id=pending[0].node_id,
                capability=pending[0].capability,
                approval_id=pending[0].approval_id,
                approved_by="test-reviewer",
                reason="approved child role",
            )
            resumed = agent_os.turn_runner.followup_task(
                run_id,
                FollowupTaskRequest(
                    parent_turn_id=turn.turn_id,
                    target_child_turn_id=str(first.target_turn_id),
                    target_agent_id=role.role_id,
                    message="resume after approval",
                    operation_id="approval-followup-2",
                ),
            )
            self.assertTrue(resumed.accepted)
            resumed_root = root / "runs" / run_id / "child_turns" / str(resumed.target_turn_id)
            self.assertEqual(read_json(resumed_root / "turn_state.json")["status"], "completed")
            self.assertEqual(provider.calls, 1)

    def test_running_child_followup_is_injected_at_graph_safe_point(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_store = RunStore(root / "runs")
            request = RequestEnvelope(user_query="graph")
            run_id, _, _ = run_store.create_run(request, "domain:test")
            child_id = "child-running"
            turn = TurnSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                execution_mode="graph",
                config=TurnConfig(
                    execution_mode="graph",
                    hook_config=HookConfig(),
                    follow_up_config=FollowUpConfig(),
                ),
                child_turns=[
                    {
                        "child_turn_id": child_id,
                        "child_thread_id": "child-thread",
                        "parent_turn_id": "turn-root",
                        "parent_run_id": run_id,
                        "parent_node_id": "worker",
                        "role_id": "worker",
                        "status": "running",
                    }
                ],
            )
            runner_root = run_store.load_run_paths(run_id).root
            runner = TurnRunner(run_store=run_store, main_agent=object(), graph_adapter=object())
            runner._persist_turn(turn)
            graph = ExecutionGraph(
                domain_id="test",
                objective="test",
                nodes=[
                    ExecutionNode(
                        node_id="worker",
                        node_kind="subagent",
                        executor="worker",
                        capability="worker.run",
                        metadata={},
                    )
                ],
            )
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                graph=graph,
                status="running",
                node_states={"worker": NodeExecutionState(node_id="worker", status="running")},
            )
            write_json(runner_root / "execution_snapshot.json", snapshot.model_dump(mode="json"))

            context = TurnContext(
                request=request,
                decision=RequestIntakeDecision(
                    status="accepted",
                    request_kind="domain_task",
                    recommended_mode="graph",
                    intent="domain:test",
                ),
                run_id=run_id,
                thread_id="thread-1",
                turn=turn,
                config=turn.config,
                run_store=run_store,
                main_agent=object(),
            )
            seen: dict[str, object] = {}
            context.follow_up_runtime = FollowUpRuntime(
                root=runner_root,
                run_id=run_id,
                turn_id=turn.turn_id,
                config=turn.config.follow_up_config,
            )
            context.follow_up_runtime.enqueue(
                source="child",
                kind="inter_agent_communication",
                text="recheck this node",
                target_turn_id=child_id,
                operation_id="running-child-followup-1",
            )

            class Adapter:
                def step(self, step_context):
                    current = ExecutionRunSnapshot.model_validate(
                        read_json(step_context.root / "execution_snapshot.json")
                    )
                    seen["messages"] = current.graph.nodes[0].metadata.get("pending_followups")
                    return ExecutionRunResult(snapshot=current)

            outcome = GraphExecutor(Adapter()).execute(context)

            self.assertEqual(outcome.status, "running")
            self.assertEqual(seen["messages"][0]["text"], "recheck this node")
            persisted_task = read_json(runner_root / "task_state.json")
            self.assertEqual(persisted_task["pending_inputs"], [])
            persisted_graph = ExecutionRunSnapshot.model_validate(
                read_json(runner_root / "execution_snapshot.json")
            )
            self.assertEqual(persisted_graph.graph.nodes[0].metadata, {})

    def test_compact_task_runs_pre_and_post_compact_hooks(self) -> None:
        seen: list[str] = []
        hooks = HookRuntime()
        hooks.register("PreCompact", lambda _payload: seen.append("pre") or {"allowed": True})
        hooks.register("PostCompact", lambda _payload: seen.append("post") or {"allowed": True})
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="compact")
            run_id, _, _ = run_store.create_run(request, "compact")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
                intent="compact",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=object(),
                hook_runtime=hooks,
            )
            response = runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                turn_config=TurnConfig(
                    task_type="compact",
                    execution_mode="core",
                    hook_config=HookConfig(),
                    compact_config=CompactConfig(context_threshold=1),
                ),
                response_factory=lambda result: ResponseEnvelope(
                    request_id=request.request_id,
                    run_id=run_id,
                    status=result.status,
                    message=result.message,
                    progress=ProgressState(
                        current_phase="compact",
                        active_agent="main_agent",
                        latest_event="turn_completed",
                        status_message="done",
                        percent=100,
                    ),
                ),
            )

            self.assertEqual(response.status, "paused")
            task_state = read_json(run_store.load_run_paths(run_id).root / "task_state.json")
            self.assertEqual(task_state["task_type"], "regular")
            self.assertEqual(task_state["continuation"]["source"], "compact")
            self.assertEqual(seen, ["pre", "post"])
            events = read_jsonl(run_store.load_run_paths(run_id).root / "execution_events.jsonl")
            self.assertGreaterEqual(
                sum(event["event_type"] == "hook_started" for event in events),
                2,
            )

    def test_pre_compact_hook_can_gate_the_compact_task(self) -> None:
        hooks = HookRuntime()
        hooks.register(
            "PreCompact",
            lambda _payload: {"allowed": False},
            name="block-compaction",
        )
        with TemporaryDirectory() as temporary_directory:
            run_store = RunStore(Path(temporary_directory) / "runs")
            request = RequestEnvelope(user_query="compact")
            run_id, _, _ = run_store.create_run(request, "compact")
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
                intent="compact",
            )
            runner = TurnRunner(
                run_store=run_store,
                main_agent=object(),
                graph_adapter=object(),
                hook_runtime=hooks,
            )
            response = runner.run(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id="thread-1",
                execution_mode="core",
                turn_config=TurnConfig(
                    task_type="compact",
                    execution_mode="core",
                    hook_config=HookConfig(),
                    compact_config=CompactConfig(),
                ),
                response_factory=lambda result: ResponseEnvelope(
                    request_id=request.request_id,
                    run_id=run_id,
                    status=result.status,
                    message=result.message,
                    progress=ProgressState(
                        current_phase="compact",
                        active_agent="main_agent",
                        latest_event="turn_gated",
                        status_message="gated",
                        percent=0,
                    ),
                ),
            )

            self.assertEqual(response.status, "gated")
            self.assertEqual(
                TurnSnapshot.model_validate(
                    read_json(run_store.load_run_paths(run_id).root / "turn_state.json")
                ).status,
                "gated",
            )


if __name__ == "__main__":
    unittest.main()
