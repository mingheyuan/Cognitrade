from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.cli.flow.chat_event_adapter import build_chat_presenter, chat_event_listener
from research_system.cli.flow.chat_research_runner import run_chat_research
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.storage.jsonl import read_json
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import RunEvent
from research_system.services.routing.router.task_router import ChatDispatchDecision


class OrchestratorContextStub:
    instances: list["OrchestratorContextStub"] = []

    def __init__(self, settings, event_listener=None) -> None:
        self.settings = settings
        self.event_listener = event_listener
        self.closed = False
        self.requests: list[RequestEnvelope] = []
        self.response = {
            "run_id": "01CTX",
            "status": "completed",
            "intent": "stock_check",
            "message": "ok",
        }
        self.__class__.instances.append(self)

    def __enter__(self) -> "OrchestratorContextStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    def run(self, request: RequestEnvelope):
        self.requests.append(request)
        return type("Response", (), {"model_dump": lambda _self, mode="json": dict(self.response)})()


class SkillRuntimeStub:
    def run(self, skill_name: str, **kwargs):
        snapshot_type = skill_name.removesuffix("_snapshot_skill").replace("_snapshot", "")
        return {
            "snapshot_type": snapshot_type,
            "symbol": kwargs.get("symbol"),
            "query": kwargs.get("query"),
            "snapshot_date": "2026-06-28",
            "facts": {},
            "source": {"skill": skill_name},
        }


class ChatResearchRunnerTests(unittest.TestCase):
    def test_default_cli_research_entry_calls_agent_os_directly(self) -> None:
        captured: list[RequestEnvelope] = []

        class AgentOSStub:
            def __init__(self, settings) -> None:
                self.settings = settings

            def handle_request(self, request, event_callback=None):
                del event_callback
                captured.append(request)
                return type(
                    "Response",
                    (),
                    {"model_dump": lambda _self, mode="json": {"run_id": "01AGENTOS", "status": "completed"}},
                )()

        with patch("research_system.cli.flow.chat_research_runner.AgentOS", AgentOSStub):
            payload = run_chat_research(
                Settings.from_env(Path(".")),
                query="check 600000",
                intent="stock_check",
                run_id=None,
                thinking_enabled=False,
                events_mode="compact",
            )

        self.assertEqual(payload["run_id"], "01AGENTOS")
        self.assertEqual([request.user_query for request in captured], ["check 600000"])

    def test_chat_event_listener_streams_live_subagent_progress_updates(self) -> None:
        presenter = build_chat_presenter(thinking_enabled=True, events_mode="compact")
        captured = []

        chat_event_listener(
            RunEvent(
                request_id="01REQ",
                run_id="01RUN",
                intent="stock_check",
                event_type="subagent_started",
                phase="collect",
                agent="collector",
                message="stock_snapshot_collector started",
                payload={
                    "subagent_type": "stock_snapshot_collector",
                    "objective": "Collect stock-level verified snapshots.",
                    "status": "running",
                },
            ),
            presenter=presenter,
            output_writer=captured.append,
        )
        chat_event_listener(
            RunEvent(
                request_id="01REQ",
                run_id="01RUN",
                intent="stock_check",
                event_type="subagent_completed",
                phase="collect",
                agent="collector",
                message="stock_snapshot_collector completed",
                payload={
                    "subagent_type": "stock_snapshot_collector",
                    "objective": "Collect stock-level verified snapshots.",
                    "status": "completed",
                },
            ),
            presenter=presenter,
            output_writer=captured.append,
        )

        self.assertGreaterEqual(len(captured), 2)
        status_updates = [update for update in captured if getattr(update, "kind", None) == "status"]
        event_updates = [update for update in captured if getattr(update, "kind", None) == "event"]
        patch_updates = [update for update in captured if getattr(update, "state_patch", None) is not None]
        self.assertTrue(any("stock_snapshot_collector" in update.text for update in status_updates))
        self.assertTrue(any("completed" in update.text for update in event_updates))
        self.assertTrue(any(getattr(update.state_patch, "events_payload", None) is not None for update in patch_updates))
        self.assertTrue(any(getattr(update.state_patch, "trace_payload", None) is not None for update in patch_updates))

    def test_run_chat_research_closes_orchestrator(self) -> None:
        OrchestratorContextStub.instances.clear()
        payload = run_chat_research(
            Settings.from_env(Path(".")),
            query="check 000001",
            intent="stock_check",
            run_id=None,
            thinking_enabled=True,
            events_mode="compact",
            orchestrator_cls=OrchestratorContextStub,
        )

        self.assertEqual(payload["run_id"], "01CTX")
        self.assertEqual(len(OrchestratorContextStub.instances), 1)
        stub = OrchestratorContextStub.instances[0]
        self.assertTrue(stub.closed)
        self.assertEqual(len(stub.requests), 1)
        self.assertEqual(stub.requests[0].user_query, "check 000001")

    def test_run_chat_research_passes_conversation_context_into_request(self) -> None:
        OrchestratorContextStub.instances.clear()
        run_chat_research(
            Settings.from_env(Path(".")),
            query="continue the previous conclusion",
            intent="stock_check",
            run_id=None,
            thinking_enabled=True,
            events_mode="compact",
            conversation_context=[
                {
                    "type": "recent_research_memory",
                    "query": "check pingan bank",
                    "intent": "stock_check",
                    "summary": "valuation is not cheap but cashflow is resilient",
                    "run_id": "01MEM",
                }
            ],
            orchestrator_cls=OrchestratorContextStub,
        )

        stub = OrchestratorContextStub.instances[0]
        self.assertEqual(len(stub.requests), 1)
        self.assertEqual(stub.requests[0].conversation_context[0]["query"], "check pingan bank")

    def test_run_chat_research_applies_review_target_and_dispatch_trace_into_request(self) -> None:
        OrchestratorContextStub.instances.clear()
        run_chat_research(
            Settings.from_env(Path(".")),
            query="review the previous conclusion",
            intent="review_thesis",
            run_id="01TARGET",
            thinking_enabled=True,
            events_mode="compact",
            dispatch=ChatDispatchDecision(mode="research", intent="review_thesis", reason="chat_dispatch_test"),
            orchestrator_cls=OrchestratorContextStub,
        )

        stub = OrchestratorContextStub.instances[0]
        request = stub.requests[0]
        self.assertEqual(request.intent, "review_thesis")
        self.assertEqual(request.constraints["review_target_run_id"], "01TARGET")
        self.assertEqual(
            request.constraints["chat_dispatch"],
            {
                "mode": "research",
                "intent": "review_thesis",
                "reason": "chat_dispatch_test",
                "source": "chat_dispatch",
            },
        )

    def test_chat_research_writes_dispatch_trace_into_result_debug(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(__file__).resolve().parents[1]
            settings = Settings.from_env(repository_root).model_copy(
                update={"runs_root": Path(temp_dir) / "runs", "memory_root": Path(temp_dir) / "memory"}
            )
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="stock_check",
                        user_query="check 000001",
                        constraints={
                            "chat_dispatch": {
                                "mode": "research",
                                "intent": "stock_check",
                                "reason": "chat_dispatch_test",
                                "source": "chat_dispatch",
                            }
                        },
                    )
                )
                self.assertEqual(response.status, "completed_partial")
                snapshot = read_json(
                    settings.runs_root / str(response.run_id) / "execution_snapshot.json"
                ) or {}
                dispatch_trace = dict(
                    (snapshot.get("graph") or {}).get("metadata", {}).get("chat_dispatch") or {}
                )
                self.assertEqual(dispatch_trace.get("mode"), "research")
                self.assertEqual(dispatch_trace.get("intent"), "stock_check")
                self.assertEqual(dispatch_trace.get("reason"), "chat_dispatch_test")
            finally:
                orchestrator.close()


if __name__ == "__main__":
    unittest.main()
