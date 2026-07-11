from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from research_system.cli.flow.chat_dispatch import (
    answer_basic_chat,
    decide_chat_dispatch,
    print_streamed_agent_os_chat_answer,
    print_streamed_basic_chat_answer,
    should_answer_basic_chat,
)
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.storage.jsonl import read_json
from research_system.services.routing.router.task_router import TaskRouter


class ProviderUnavailableStub:
    available = False


class ProviderStreamingStub:
    available = True

    def stream_complete(self, **kwargs):
        on_text_delta = kwargs["on_text_delta"]
        on_reasoning_summary = kwargs.get("on_reasoning_summary")
        if callable(on_reasoning_summary):
            on_reasoning_summary("brief reasoning summary")
        on_text_delta("Hello")
        on_text_delta(", streaming reply.")
        return "Hello, streaming reply."

    def complete(self, **kwargs):
        return "fallback should not be used"


class ChatDispatchTests(unittest.TestCase):
    def test_production_basic_chat_answer_uses_agent_os_core_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                }
            )
            updates = []
            answer = print_streamed_agent_os_chat_answer(
                settings=settings,
                state=type("State", (), {"thread_id": "chat-thread"})(),
                query="你是谁？",
                llm_provider=ProviderUnavailableStub(),
                emit_text=lambda _writer, update: updates.append(update),
            )

        self.assertIn("Agent OS", answer)
        self.assertTrue(updates)

    def test_basic_identity_question_is_answered_without_research_routing(self) -> None:
        settings = Settings.from_env(Path("."))
        reply = answer_basic_chat(
            settings=settings,
            state=object(),
            query="who are you?",
            llm_provider=ProviderUnavailableStub(),
        )
        self.assertIn("Cognitrade", reply)

    def test_basic_identity_question_is_answered_in_chinese_without_garbled_text(self) -> None:
        settings = Settings.from_env(Path("."))
        reply = answer_basic_chat(
            settings=settings,
            state=object(),
            query="你是谁？",
            llm_provider=ProviderUnavailableStub(),
        )
        self.assertIn("Cognitrade", reply)
        self.assertIn("A 股研究", reply)
        self.assertNotIn("????", reply)

    def test_basic_question_detector_returns_true_for_identity_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            router = TaskRouter()
            self.assertTrue(
                should_answer_basic_chat(
                    settings=settings,
                    state=object(),
                    query="who are you?",
                    task_router=router,
                )
            )

    def test_chat_dispatch_prefers_direct_answer_for_index_definition_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(
                    settings=settings,
                    state=object(),
                    query="what is the shanghai composite index?",
                    task_router=router,
                )
                self.assertEqual(dispatch.mode, "direct_answer")
                self.assertIsNone(dispatch.intent)
            finally:
                orchestrator.close()

    def test_chat_dispatch_prefers_direct_answer_for_chinese_definition_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(
                    settings=settings,
                    state=object(),
                    query="什么是上证指数？",
                    task_router=router,
                )
                self.assertEqual(dispatch.mode, "direct_answer")
                self.assertIsNone(dispatch.intent)
            finally:
                orchestrator.close()

    def test_chat_dispatch_runtime_finalizes_thread_and_turn_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(
                    settings=settings,
                    state=object(),
                    query="what is the shanghai composite index?",
                    task_router=router,
                )
                self.assertEqual(dispatch.mode, "direct_answer")
            finally:
                orchestrator.close()
            chat_run_root = next((settings.workspace_root / "chat_runs").iterdir())
            self.assertEqual((read_json(chat_run_root / "thread_state.json") or {}).get("status"), "completed")
            self.assertEqual((read_json(chat_run_root / "turn_state.json") or {}).get("status"), "completed")

    def test_chat_dispatch_runtime_uses_settings_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.max_llm_calls_per_turn = 3
            settings.max_subagent_calls_per_turn = 4
            settings.max_child_threads_per_turn = 2
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                with patch(
                    "research_system.cli.runtime.chat_llm_runtime.build_runtime_context",
                    wraps=__import__("research_system.cli.runtime.chat_llm_runtime", fromlist=["build_runtime_context"]).build_runtime_context,
                ) as build_runtime_context:
                    dispatch = decide_chat_dispatch(
                        settings=settings,
                        state=object(),
                        query="what is the shanghai composite index?",
                        task_router=router,
                    )
                self.assertEqual(dispatch.mode, "direct_answer")
                self.assertEqual(build_runtime_context.call_args.kwargs["max_llm_calls_per_turn"], 3)
                self.assertEqual(build_runtime_context.call_args.kwargs["max_subagent_calls_per_turn"], 4)
                self.assertEqual(build_runtime_context.call_args.kwargs["max_child_threads_per_turn"], 2)
            finally:
                orchestrator.close()

    def test_chat_dispatch_prefers_research_for_time_sensitive_index_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(
                    settings=settings,
                    state=object(),
                    query="latest shanghai composite performance",
                    task_router=router,
                )
                self.assertEqual(dispatch.mode, "research")
            finally:
                orchestrator.close()

    def test_chat_dispatch_uses_route_table_before_llm_for_research_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(
                    settings=settings,
                    state=object(),
                    query="what stocks are worth following as a watchlist recently",
                    task_router=router,
                )
                self.assertEqual(dispatch.mode, "research")
                self.assertEqual(dispatch.intent, "stock_screen")
                self.assertIn("fallback_rule_route:route_table", str(dispatch.reason))
            finally:
                orchestrator.close()

    def test_chat_dispatch_prefers_compare_for_single_stock_start_expanded_to_bank_peers(self) -> None:
        bank_compare_query = (
            "\u4ee5 000001 \u5e73\u5b89\u94f6\u884c\u4e3a\u8d77\u70b9\uff0c\u6269\u5230\u94f6\u884c\u80a1\uff1a"
            "\u62db\u5546\u94f6\u884c\u3001\u5b81\u6ce2\u94f6\u884c\u3001\u5174\u4e1a\u94f6\u884c\u3001"
            "\u6c5f\u82cf\u94f6\u884c\u3001\u6210\u90fd\u94f6\u884c\u7b49,\u6700\u8fd1\u54ea\u51e0\u53ea"
            "\u80a1\u7968\u66f4\u503c\u5f97\u6295\u8d44"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(
                    settings=settings,
                    state=object(),
                    query=bank_compare_query,
                    task_router=router,
                )
                self.assertEqual(dispatch.mode, "research")
                self.assertEqual(dispatch.intent, "stock_compare")
                self.assertEqual(dispatch.reason, "query_hint")
            finally:
                orchestrator.close()

    def test_chat_dispatch_uses_semantic_route_before_llm_for_research_queries(self) -> None:
        from research_system.services.routing.tables import (
            IntentRouteRule,
            IntentRouteTable,
            IntentRouteTableStore,
        )

        store = IntentRouteTableStore(
            default_table=IntentRouteTable(
                version="chat-semantic-v1",
                rules=[
                    IntentRouteRule(
                        rule_id="screen_semantic",
                        intent="stock_screen",
                        match_type="semantic",
                        keywords=["worth follow", "watchlist", "candidate", "opportunity"],
                        priority=20,
                        target="lowered",
                        semantic_threshold=0.25,
                    )
                ],
            ),
            cache_ttl_seconds=0.0,
        )
        router = TaskRouter(route_table_store=store)
        dispatch = decide_chat_dispatch(
            settings=Settings.from_env(Path(".")),
            state=object(),
            query="Any follow ideas for the next swing opportunity?",
            task_router=router,
        )

        self.assertEqual(dispatch.mode, "research")
        self.assertEqual(dispatch.intent, "stock_screen")
        self.assertIn("fallback_rule_route:route_table_semantic", str(dispatch.reason))

    def test_basic_chat_streams_when_provider_supports_streaming(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            print_streamed_basic_chat_answer(
                settings=Settings.from_env(Path(".")),
                state=object(),
                query="Hello",
                llm_provider=ProviderStreamingStub(),
                emit_text=lambda output_writer, update: print(update.text, end="" if update.action == "stream_append" else "\n"),
            )
        output = buffer.getvalue()
        self.assertIn("brief reasoning summary", output)
        self.assertIn("Hello, streaming reply.", output)


if __name__ == "__main__":
    unittest.main()
