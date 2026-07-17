from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.agent_os import AgentOS
from research_system.runtime.platform.settings import Settings
from research_system.runtime.follow_up import FollowUpRuntime
from research_system.runtime.hooks import HookRuntime
from research_system.runtime.storage.jsonl import read_json, read_jsonl, write_json
from research_system.runtime.turn_runner import CoreLoopExecutor, TurnContext
from research_system.schemas.core_runtime import (
    AgentTurnResponse,
    CoreToolDefinition,
    MainAgentState,
    RequestIntakeDecision,
)
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.turn_runtime import FollowUpConfig, HookConfig, TurnConfig, TurnSnapshot


class _ScriptedProvider:
    available = True

    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls = 0

    def complete_with_tools(self, **_kwargs):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


class CoreCheckpointTests(unittest.TestCase):
    def _settings(self, root: Path) -> Settings:
        repository_root = Path(__file__).resolve().parents[1]
        return Settings.from_env(repository_root).model_copy(
            update={
                "runs_root": root / "runs",
                "memory_root": root / "memory",
                "domain_packs_root": root / "empty_packs",
                "workspace_root": root / "workspace",
            }
        )

    def _context(
        self,
        agent_os: AgentOS,
        request: RequestEnvelope,
        run_id: str,
        *,
        resume: bool = False,
    ) -> TurnContext:
        decision = RequestIntakeDecision(
            status="accepted",
            request_kind="conversation",
            recommended_mode="core",
            intent="core.conversation.answer",
        )
        config = TurnConfig(execution_mode="core")
        turn = TurnSnapshot(
            run_id=run_id,
            thread_id="thread-1",
            request_id=request.request_id,
            execution_mode="core",
            config=config,
        )
        return TurnContext(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id="thread-1",
            turn=turn,
            config=config,
            run_store=agent_os.run_store,
            main_agent=agent_os.main_agent,
            resume=resume,
        )

    def _new_run(self, agent_os: AgentOS, request: RequestEnvelope) -> str:
        run_id, _, _ = agent_os.run_store.create_run(request, "core.conversation.answer")
        return run_id

    def test_core_executor_returns_after_one_provider_safe_point(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            provider = _ScriptedProvider(
                [
                    {
                        "kind": "tool_calls",
                        "tool_calls": [
                            {
                                "tool_name": "core.message.compose",
                                "arguments": {"message": "tool result"},
                                "call_id": "model-tool-1",
                            }
                        ],
                    },
                    {"kind": "message", "message": "final answer"},
                ]
            )
            agent_os.main_agent.llm_provider = provider
            request = RequestEnvelope(user_query="test")
            run_id = self._new_run(agent_os, request)
            context = self._context(agent_os, request, run_id)
            executor = CoreLoopExecutor(agent_os.main_agent)

            first = executor.execute(context)

            self.assertEqual(first.status, "running")
            self.assertEqual(provider.calls, 1)
            self.assertEqual(read_json(context.root / "provider_checkpoint.json")["status"], "handled")

            second = executor.execute(context)

            self.assertEqual(second.status, "completed")
            self.assertEqual(provider.calls, 2)

    def test_running_checkpoint_retries_provider_without_advancing_turn(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            provider = _ScriptedProvider([{"kind": "message", "message": "recovered"}])
            agent_os.main_agent.llm_provider = provider
            request = RequestEnvelope(user_query="test")
            run_id = self._new_run(agent_os, request)
            run_root = agent_os.run_store.load_run_paths(run_id).root
            write_json(
                run_root / "main_agent_state.json",
                MainAgentState(
                    run_id=run_id,
                    thread_id="thread-1",
                    request_id=request.request_id,
                    status="running",
                    mode="core",
                    turn_count=1,
                    transcript=[{"role": "user", "content": "test"}],
                ).model_dump(mode="json"),
            )
            write_json(
                run_root / "provider_checkpoint.json",
                {
                    "status": "running",
                    "turn_index": 1,
                    "operation_id": f"model:{run_id}:1",
                    "transcript": [{"role": "user", "content": "test"}],
                },
            )

            outcome = CoreLoopExecutor(agent_os.main_agent).execute(
                self._context(agent_os, request, run_id, resume=True)
            )

            self.assertEqual(outcome.status, "completed")
            self.assertEqual(provider.calls, 1)
            self.assertEqual(read_json(run_root / "main_agent_state.json")["turn_count"], 1)

    def test_response_ready_checkpoint_does_not_repeat_tool_side_effect(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            provider = _ScriptedProvider([{"kind": "message", "message": "unused"}])
            agent_os.main_agent.llm_provider = provider
            calls: list[str] = []
            agent_os.main_agent.tools.register(
                CoreToolDefinition(
                    name="core.test.side_effect",
                    description="Test an idempotent side effect.",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    idempotency_required=True,
                    side_effect_level="write_external",
                ),
                lambda _arguments, _context: (calls.append("called") or {"ok": True}),
            )
            request = RequestEnvelope(user_query="test")
            run_id = self._new_run(agent_os, request)
            run_root = agent_os.run_store.load_run_paths(run_id).root
            response = AgentTurnResponse(
                kind="tool_calls",
                tool_calls=[
                    {
                        "tool_name": "core.test.side_effect",
                        "arguments": {},
                        "call_id": "side-effect-call-1",
                    }
                ],
            )
            write_json(
                run_root / "main_agent_state.json",
                MainAgentState(
                    run_id=run_id,
                    thread_id="thread-1",
                    request_id=request.request_id,
                    status="running",
                    mode="core",
                    turn_count=1,
                    transcript=[{"role": "user", "content": "test"}],
                ).model_dump(mode="json"),
            )
            write_json(
                run_root / "provider_checkpoint.json",
                {
                    "status": "response_ready",
                    "turn_index": 1,
                    "operation_id": f"model:{run_id}:1",
                    "transcript": [{"role": "user", "content": "test"}],
                    "response": response.model_dump(mode="json"),
                },
            )

            executor = CoreLoopExecutor(agent_os.main_agent)
            first = executor.execute(self._context(agent_os, request, run_id, resume=True))
            self.assertEqual(first.status, "running")
            self.assertEqual(provider.calls, 0)
            self.assertEqual(calls, ["called"])

            # Simulate a worker restart before the response-ready receipt was
            # acknowledged by the surrounding task loop.
            write_json(
                run_root / "provider_checkpoint.json",
                {
                    "status": "response_ready",
                    "turn_index": 1,
                    "operation_id": f"model:{run_id}:1",
                    "transcript": [{"role": "user", "content": "test"}],
                    "response": response.model_dump(mode="json"),
                },
            )
            second = CoreLoopExecutor(agent_os.main_agent).execute(
                self._context(agent_os, request, run_id, resume=True)
            )

            self.assertEqual(second.status, "running")
            self.assertEqual(provider.calls, 0)
            self.assertEqual(calls, ["called"])

    def test_model_follow_up_returns_to_the_same_turn_before_completion(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            provider = _ScriptedProvider(
                [
                    {
                        "kind": "message",
                        "message": "intermediate",
                        "model_needs_follow_up": True,
                    },
                    {"kind": "message", "message": "final"},
                ]
            )
            agent_os.main_agent.llm_provider = provider
            request = RequestEnvelope(user_query="test")
            run_id = self._new_run(agent_os, request)
            context = self._context(agent_os, request, run_id)
            context.config = TurnConfig(
                execution_mode="core",
                hook_config=HookConfig(),
                follow_up_config=FollowUpConfig(),
            )
            context.follow_up_runtime = FollowUpRuntime(
                root=context.root,
                run_id=run_id,
                turn_id=context.turn.turn_id,
                config=context.config.follow_up_config,
            )
            executor = CoreLoopExecutor(agent_os.main_agent)

            first = executor.execute(context)
            second = executor.execute(context)

            self.assertEqual(first.status, "running")
            self.assertEqual(second.status, "completed")
            self.assertEqual(provider.calls, 2)
            events = read_jsonl(context.root / "execution_events.jsonl")
            self.assertTrue(
                any(event["event_type"] == "main_agent_follow_up_requested" for event in events)
            )

    def test_pending_input_is_delivered_before_stop_hook(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            request = RequestEnvelope(user_query="test")
            run_id = self._new_run(agent_os, request)
            context = self._context(agent_os, request, run_id)
            context.config = TurnConfig(
                execution_mode="core",
                hook_config=HookConfig(),
                follow_up_config=FollowUpConfig(),
            )
            follow_up = FollowUpRuntime(
                root=context.root,
                run_id=run_id,
                turn_id=context.turn.turn_id,
                config=context.config.follow_up_config,
            )
            context.follow_up_runtime = follow_up
            stop_calls: list[str] = []
            hooks = HookRuntime()
            hooks.register("stop", lambda _input: stop_calls.append("stop") or None)
            context.hook_runtime = hooks

            class Provider(_ScriptedProvider):
                def complete_with_tools(self, **kwargs):
                    if self.calls == 0:
                        follow_up.enqueue(
                            source="user",
                            kind="user_input",
                            text="steer",
                            operation_id="steer-1",
                        )
                    return super().complete_with_tools(**kwargs)

            provider = Provider(
                [
                    {"kind": "message", "message": "first"},
                    {"kind": "message", "message": "second"},
                ]
            )
            agent_os.main_agent.llm_provider = provider
            executor = CoreLoopExecutor(agent_os.main_agent)

            first = executor.execute(context)
            second = executor.execute(context)

            self.assertEqual(first.status, "running")
            self.assertEqual(second.status, "completed")
            self.assertEqual(stop_calls, ["stop"])

    def test_compact_checkpoint_resumes_the_original_regular_task(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            provider = _ScriptedProvider([{"kind": "message", "message": "continued"}])
            agent_os.main_agent.llm_provider = provider
            request = RequestEnvelope(
                user_query="compact before continuing",
                constraints={"task_type": "compact"},
            )

            first = agent_os.handle_request(request)

            self.assertEqual(first.status, "running")
            run_root = agent_os.run_store.load_run_paths(str(first.run_id)).root
            task_state = read_json(run_root / "task_state.json")
            self.assertEqual(task_state["task_type"], "regular")
            self.assertEqual(task_state["continuation"]["source"], "compact")
            self.assertEqual(read_json(run_root / "turn_state.json")["status"], "paused")

            resumed = agent_os.resume(str(first.run_id))

            self.assertEqual(resumed.status, "completed")
            self.assertEqual(provider.calls, 1)
            self.assertEqual(read_json(run_root / "turn_state.json")["status"], "completed")


if __name__ == "__main__":
    unittest.main()
