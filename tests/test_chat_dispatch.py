from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.cli.chat_dispatch import (
    answer_basic_chat,
    decide_chat_dispatch,
    print_streamed_basic_chat_answer,
    should_answer_basic_chat,
)
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings, ensure_workspace_layout


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
    def test_basic_identity_question_is_answered_without_research_routing(self) -> None:
        reply = answer_basic_chat(query="who are you?", llm_provider=ProviderUnavailableStub())
        self.assertIn("Cognitrade", reply)

    def test_basic_identity_question_is_answered_in_chinese_without_garbled_text(self) -> None:
        reply = answer_basic_chat(query="你是谁", llm_provider=ProviderUnavailableStub())
        self.assertIn("Cognitrade", reply)
        self.assertIn("A 股研究", reply)
        self.assertNotIn("????", reply)

    def test_basic_question_detector_returns_true_for_identity_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                self.assertTrue(should_answer_basic_chat(query="who are you?", task_router=router))
            finally:
                orchestrator.close()

    def test_chat_dispatch_prefers_direct_answer_for_index_definition_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(query="what is the shanghai composite index?", task_router=router)
                self.assertEqual(dispatch.mode, "direct_answer")
                self.assertIsNone(dispatch.intent)
            finally:
                orchestrator.close()

    def test_chat_dispatch_prefers_research_for_time_sensitive_index_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(query="latest shanghai composite performance", task_router=router)
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
                    query="what stocks are worth following as a watchlist recently",
                    task_router=router,
                )
                self.assertEqual(dispatch.mode, "research")
                self.assertEqual(dispatch.intent, "stock_screen")
                self.assertIn("fallback_rule_route:route_table", str(dispatch.reason))
            finally:
                orchestrator.close()

    def test_chat_dispatch_prefers_compare_for_single_stock_start_expanded_to_bank_peers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(
                    query="以 000001 平安银行为起点，扩到银行股：招商银行、宁波银行、兴业银行、江苏银行、成都银行等,最近哪几只股票更值得投资",
                    task_router=router,
                )
                self.assertEqual(dispatch.mode, "research")
                self.assertEqual(dispatch.intent, "stock_compare")
                self.assertEqual(dispatch.reason, "query_hint")
            finally:
                orchestrator.close()

    def test_chat_dispatch_uses_semantic_route_before_llm_for_research_queries(self) -> None:
        from research_system.services.route_table import IntentRouteRule, IntentRouteTable, IntentRouteTableStore

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
        from research_system.services.task_router import TaskRouter

        router = TaskRouter(route_table_store=store)
        dispatch = decide_chat_dispatch(query="Any follow ideas for the next swing opportunity?", task_router=router)

        self.assertEqual(dispatch.mode, "research")
        self.assertEqual(dispatch.intent, "stock_screen")
        self.assertIn("fallback_rule_route:route_table_semantic", str(dispatch.reason))

    def test_basic_chat_streams_when_provider_supports_streaming(self) -> None:
        from io import StringIO
        from contextlib import redirect_stdout

        buffer = StringIO()
        with redirect_stdout(buffer):
            print_streamed_basic_chat_answer(
                query="Hello",
                llm_provider=ProviderStreamingStub(),
                emit_text=lambda output_writer, update: print(update.text, end="" if update.action == "stream_append" else "\n"),
            )
        output = buffer.getvalue()
        self.assertIn("brief reasoning summary", output)
        self.assertIn("Hello, streaming reply.", output)


if __name__ == "__main__":
    unittest.main()
