from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.services.routing.router import (
    TaskRouter as PackageTaskRouter,
    TaskRouterChatSupport as PackageTaskRouterChatSupport,
    TaskRouterIdentifySupport as PackageTaskRouterIdentifySupport,
    TaskRouterLlmSupport as PackageTaskRouterLlmSupport,
    TaskRouterRuleSupport as PackageTaskRouterRuleSupport,
)
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.storage.jsonl import read_json
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.routing.router.task_router import TaskRouter
from research_system.services.routing.router.task_router_chat_support import TaskRouterChatSupport
from research_system.services.routing.router.task_router_identify_support import TaskRouterIdentifySupport
from research_system.services.routing.router.task_router_llm_support import TaskRouterLlmSupport
from research_system.services.routing.router.task_router_rule_support import TaskRouterRuleSupport


class TaskRouterRuntimeTests(unittest.TestCase):
    def test_router_package_exports_match_compatibility_exports(self) -> None:
        self.assertIs(PackageTaskRouter, TaskRouter)
        self.assertIs(PackageTaskRouterChatSupport, TaskRouterChatSupport)
        self.assertIs(PackageTaskRouterIdentifySupport, TaskRouterIdentifySupport)
        self.assertIs(PackageTaskRouterLlmSupport, TaskRouterLlmSupport)
        self.assertIs(PackageTaskRouterRuleSupport, TaskRouterRuleSupport)

    def test_router_wires_rule_llm_identify_and_chat_supports(self) -> None:
        router = TaskRouter()

        self.assertIsInstance(router.rule_support, TaskRouterRuleSupport)
        self.assertIsInstance(router.llm_support, TaskRouterLlmSupport)
        self.assertIsInstance(router.identify_support, TaskRouterIdentifySupport)
        self.assertIsInstance(router.chat_support, TaskRouterChatSupport)
        self.assertIs(router.identify_support.rule_support, router.rule_support)
        self.assertIs(router.identify_support.llm_support, router.llm_support)
        self.assertIs(router.chat_support.rule_support, router.rule_support)
        self.assertIs(router.chat_support.llm_support, router.llm_support)

    def test_router_auto_binds_local_runtime_for_identify(self) -> None:
        class TrackingRouter(TaskRouter):
            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self.bind_calls = 0
                self.clear_calls = 0

            def bind_runtime(self, *, context, support, request_id: str) -> None:
                self.bind_calls += 1
                super().bind_runtime(context=context, support=support, request_id=request_id)

            def clear_runtime(self) -> None:
                self.clear_calls += 1
                super().clear_runtime()

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            router = TrackingRouter(settings=settings)

            decision = router.identify(RequestEnvelope(user_query="compare 000001 and 600519"))

            self.assertEqual(router.bind_calls, 1)
            self.assertEqual(router.clear_calls, 1)
            self.assertEqual(decision.intent, "stock_compare")

    def test_router_auto_binds_local_runtime_for_chat_dispatch(self) -> None:
        class TrackingRouter(TaskRouter):
            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self.bind_calls = 0
                self.clear_calls = 0

            def bind_runtime(self, *, context, support, request_id: str) -> None:
                self.bind_calls += 1
                super().bind_runtime(context=context, support=support, request_id=request_id)

            def clear_runtime(self) -> None:
                self.clear_calls += 1
                super().clear_runtime()

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            router = TrackingRouter(settings=settings)

            decision = router.decide_chat_dispatch("what is the shanghai composite index")

            self.assertEqual(router.bind_calls, 1)
            self.assertEqual(router.clear_calls, 1)
            self.assertEqual(decision.mode, "direct_answer")

    def test_router_identify_local_runtime_uses_settings_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.max_subagent_calls_per_turn = 5
            settings.max_child_threads_per_turn = 2
            router = TaskRouter(settings=settings)

            with patch(
                "research_system.services.routing.router.task_router.bind_local_runtime",
                wraps=__import__("research_system.services.routing.router.task_router", fromlist=["bind_local_runtime"]).bind_local_runtime,
            ) as bind_runtime:
                decision = router.identify(RequestEnvelope(user_query="compare 000001 and 600519"))

            self.assertEqual(decision.intent, "stock_compare")
            self.assertEqual(bind_runtime.call_args.kwargs["runs_root"], settings.runs_root)
            self.assertEqual(bind_runtime.call_args.kwargs["max_subagent_calls_per_turn"], 5)
            self.assertEqual(bind_runtime.call_args.kwargs["max_child_threads_per_turn"], 2)

    def test_router_identify_prefers_compare_for_chinese_multi_stock_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            router = TaskRouter(settings=settings)

            decision = router.identify(RequestEnvelope(user_query="000001 和 600519 对比"))

            self.assertEqual(decision.intent, "stock_compare")

    def test_router_chat_dispatch_local_runtime_uses_settings_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.max_subagent_calls_per_turn = 4
            settings.max_child_threads_per_turn = 1
            router = TaskRouter(settings=settings)

            with patch(
                "research_system.services.routing.router.task_router.bind_local_runtime",
                wraps=__import__("research_system.services.routing.router.task_router", fromlist=["bind_local_runtime"]).bind_local_runtime,
            ) as bind_runtime:
                decision = router.decide_chat_dispatch("what is the shanghai composite index")

            self.assertEqual(decision.mode, "direct_answer")
            self.assertEqual(bind_runtime.call_args.kwargs["runs_root"], settings.runs_root)
            self.assertEqual(bind_runtime.call_args.kwargs["max_subagent_calls_per_turn"], 4)
            self.assertEqual(bind_runtime.call_args.kwargs["max_child_threads_per_turn"], 1)

    def test_router_local_runtime_finalizes_thread_and_turn_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            router = TaskRouter(settings=settings)

            decision = router.identify(RequestEnvelope(user_query="screen growth stocks"))

            self.assertEqual(decision.intent, "stock_screen")
            run_root = next(Path(settings.runs_root).iterdir())
            self.assertEqual((read_json(run_root / "thread_state.json") or {}).get("status"), "completed")
            self.assertEqual((read_json(run_root / "turn_state.json") or {}).get("status"), "completed")


if __name__ == "__main__":
    unittest.main()
