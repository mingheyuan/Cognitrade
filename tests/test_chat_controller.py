from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.adapters.storage.local_memory_adapter import LocalMemoryAdapter
from research_system.cli.flow.chat_controller import handle_chat_line
from research_system.cli.memory.chat_memory import list_chat_threads
from research_system.cli.rendering.chat_rendering import apply_tui_update
from research_system.cli.state.chat_types import ChatSessionState, TuiSession
from research_system.cli.state.chat_updates import TranscriptUpdate
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.memory import MemoryService
from research_system.services.routing.router.task_router import TaskRouter


class ProviderUnavailableStub:
    available = False


class StockCheckSkillRuntimeStub:
    def run(self, skill_name: str, **kwargs) -> dict:
        symbol = str(kwargs.get("symbol") or "")
        if skill_name == "market_snapshot_skill":
            return {
                "snapshot_type": "market",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {"market_overview": {"spot": {"latest_price": 12.3, "pct_change": 1.1, "amount": 1800000000}}},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "fundamental_snapshot_skill":
            return {
                "snapshot_type": "fundamental",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {"financial_abstract": {"revenue": 100, "net_profit": 20}},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "notice_snapshot_skill":
            return {
                "snapshot_type": "notice",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {"notice_count": 2},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "capital_flow_snapshot_skill":
            return {
                "snapshot_type": "capital_flow",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {"net_inflow": 12345678},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "sentiment_snapshot_skill":
            return {
                "snapshot_type": "sentiment",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-28",
                "facts": {"sentiment_score": 0.7},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        raise AssertionError(f"unexpected skill: {skill_name}")


class ChatControllerTests(unittest.TestCase):
    def test_handle_chat_line_stores_pending_clarification(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        def fake_run_chat_research(*args, **kwargs):
            return {
                "status": "needs_clarification",
                "intent": "stock_screen",
                "message": "Clarification required",
                "clarification": {"question": "Short-term, mid-term, or long-term?"},
            }

        handle_chat_line(
            Settings.from_env(Path(".")),
            session.state,
            "what stocks are worth following recently",
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
            run_chat_research_fn=fake_run_chat_research,
        )

        self.assertEqual(session.state.pending_clarification_query, "what stocks are worth following recently")
        self.assertEqual(session.state.pending_clarification_question, "Short-term, mid-term, or long-term?")

    def test_handle_chat_line_updates_last_run_id_via_session_patch(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        def fake_run_chat_research(*args, **kwargs):
            return {
                "run_id": "01RUNFLOW",
                "status": "completed",
                "intent": "stock_check",
                "display_summary": "done",
            }

        query = "\u5206\u6790 000001"
        handle_chat_line(
            Settings.from_env(Path(".")),
            session.state,
            query,
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
            run_chat_research_fn=fake_run_chat_research,
        )

        self.assertEqual(session.state.last_run_id, "01RUNFLOW")
        self.assertEqual(session.state.last_user_query, query)
        self.assertEqual(session.state.last_result_intent, "stock_check")
        self.assertEqual(session.state.last_result_summary, "done")

    def test_handle_chat_line_answers_recent_topic_from_session_memory(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(
                last_user_query="compare guizhou maotai and byd",
                last_result_intent="stock_compare",
                last_result_summary="compare result is still cautious",
                last_run_id="01MEMORY",
            ),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        handle_chat_line(
            Settings.from_env(Path(".")),
            session.state,
            "what were we just talking about?",
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
        )

        answers = [message.text for message in session.output_lines if message.kind == "answer"]
        self.assertTrue(answers)
        self.assertIn("compare guizhou maotai and byd", answers[-1])
        self.assertIn("stock_compare", answers[-1])

    def test_handle_chat_line_answers_recent_topic_from_persistent_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            MemoryService(LocalMemoryAdapter(settings)).write_chat_research_memory(
                query="compare guizhou maotai and byd",
                intent="stock_compare",
                run_id="01MEM0",
                summary="compare result is still cautious",
                conclusion="continue comparing cashflow and valuation",
                follow_up="supplement same-basis cashflow and valuation assumptions",
                status="completed",
            )
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            handle_chat_line(
                settings,
                session.state,
                "what were we just talking about?",
                llm_provider=ProviderUnavailableStub(),
                task_router=TaskRouter(),
                cli_command_runner=lambda *args, **kwargs: {},
                payload_printer=lambda payload, as_json=False, output_writer=None: None,
                output_writer=lambda update: apply_tui_update(session, update),
            )

            answers = [message.text for message in session.output_lines if message.kind == "answer"]
            self.assertTrue(answers)
            self.assertIn("compare guizhou maotai and byd", answers[-1])
            self.assertIn("stock_compare", answers[-1])
            self.assertEqual(session.state.last_run_id, "01MEM0")

    def test_planner_consumes_thread_profile_context_hints_from_chat_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                plan = orchestrator.planner.build_initial_plan(
                    intent="stock_check",
                    request=RequestEnvelope(
                        user_query="continue the previous conclusion",
                        conversation_context=[
                            {
                                "type": "chat_thread_profile",
                                "topic_title": "compare maotai and byd",
                                "topic_intent": "stock_compare",
                                "last_result_summary": "comparison is still cautious",
                                "recommended_next_step": "supplement same-basis cashflow and valuation assumptions",
                                "open_question": "Do you want medium-term or long-term comparison?",
                                "thread_status": "clarification_pending",
                            }
                        ],
                    ),
                )
            finally:
                orchestrator.close()

        self.assertEqual(plan.dispatch_trace["context_hints"]["thread_topic"], "compare maotai and byd")
        self.assertEqual(plan.dispatch_trace["context_hints"]["thread_status"], "clarification_pending")
        self.assertIn("thread_next_step=", plan.entry_reason or "")
        retrieve_action = next(action for action in plan.actions if action.node_id == "retrieve")
        self.assertEqual(
            retrieve_action.inputs["planning_context_hints"]["recommended_next_step"],
            "supplement same-basis cashflow and valuation assumptions",
        )

    def test_planner_consumes_relevant_methodology_memory_from_chat_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                plan = orchestrator.planner.build_initial_plan(
                    intent="stock_check",
                    request=RequestEnvelope(
                        user_query="continue the previous conclusion",
                        conversation_context=[
                            {
                                "type": "relevant_methodology_memory",
                                "memory_type": "dimension_experience",
                                "benchmark_scope": "defensive_compounder",
                                "match_score": 3.5,
                            }
                        ],
                    ),
                )
            finally:
                orchestrator.close()

        self.assertEqual(
            plan.dispatch_trace["context_hints"]["methodology_benchmark_scope"],
            "defensive_compounder",
        )
        retrieve_action = next(action for action in plan.actions if action.node_id == "retrieve")
        self.assertEqual(
            retrieve_action.inputs["planning_context_hints"]["methodology_memory_match_score"],
            "3.5",
        )

    def test_handle_chat_line_passes_recent_context_into_research_runner(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(
                last_user_query="analyze maotai and byd",
                last_result_intent="stock_compare",
                last_result_summary="comparison is still cautious",
                last_run_id="01LASTCTX",
            ),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        seen_contexts: list[list[dict]] = []

        def fake_run_chat_research(*args, **kwargs):
            seen_contexts.append(list(kwargs.get("conversation_context") or []))
            return {
                "run_id": "01RUNFLOW",
                "status": "completed",
                "intent": "stock_check",
                "display_summary": "done",
            }

        handle_chat_line(
            Settings.from_env(Path(".")),
            session.state,
            "\u5206\u6790 000001",
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
            run_chat_research_fn=fake_run_chat_research,
        )

        self.assertTrue(seen_contexts)
        self.assertTrue(
            any(
                item.get("type") == "recent_research_memory" and item.get("query") == "analyze maotai and byd"
                for item in seen_contexts[0]
            )
        )

    def test_handle_chat_line_short_follow_up_reuses_previous_compare_intent(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(
                last_user_query="compare guizhou maotai, ningde shidai, and byd. who is more worth investing in?",
                last_result_intent="stock_compare",
                last_run_id="01COMPARE",
            ),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        captured: dict[str, object] = {}

        def fake_run_chat_research(*args, **kwargs):
            captured["query"] = kwargs.get("query")
            captured["intent"] = kwargs.get("intent")
            captured["run_id"] = kwargs.get("run_id")
            return {
                "run_id": "01RUNFLOW",
                "status": "completed",
                "intent": "stock_compare",
                "display_summary": "done",
            }

        handle_chat_line(
            Settings.from_env(Path(".")),
            session.state,
            "who is more resilient?",
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
            run_chat_research_fn=fake_run_chat_research,
        )

        self.assertEqual(captured["intent"], "stock_compare")
        self.assertIsNone(captured["run_id"])
        self.assertIn("compare guizhou maotai, ningde shidai, and byd", str(captured["query"]))

    def test_handle_chat_line_follow_up_can_upgrade_stale_stock_check_to_bank_compare(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(
                current_intent="stock_check",
                last_user_query="\u5206\u6790 000001",
                last_result_intent="stock_check",
                last_run_id="01PINGAN",
            ),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        captured: dict[str, object] = {}

        def fake_run_chat_research(*args, **kwargs):
            captured["query"] = kwargs.get("query")
            captured["intent"] = kwargs.get("intent")
            return {
                "run_id": "01BANKCOMPARE",
                "status": "completed",
                "intent": "stock_compare",
                "display_summary": "done",
            }

        bank_compare_query = (
            "\u4ee5 000001 \u5e73\u5b89\u94f6\u884c\u4e3a\u8d77\u70b9\uff0c\u6269\u5230\u94f6\u884c\u80a1\uff1a"
            "\u62db\u5546\u94f6\u884c\u3001\u5b81\u6ce2\u94f6\u884c\u3001\u5174\u4e1a\u94f6\u884c\u3001"
            "\u6c5f\u82cf\u94f6\u884c\u3001\u6210\u90fd\u94f6\u884c\u7b49,\u6700\u8fd1\u54ea\u51e0\u53ea"
            "\u80a1\u7968\u66f4\u503c\u5f97\u6295\u8d44"
        )
        handle_chat_line(
            Settings.from_env(Path(".")),
            session.state,
            bank_compare_query,
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
            run_chat_research_fn=fake_run_chat_research,
        )

        self.assertEqual(captured["intent"], "stock_compare")
        self.assertIn("\u5e73\u5b89\u94f6\u884c", str(captured["query"]))

    def test_handle_chat_line_review_follow_up_uses_previous_run_id(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(
                last_user_query="analyze ping an bank",
                last_result_intent="stock_check",
                last_run_id="01PREVIOUS",
            ),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        captured: dict[str, object] = {}

        def fake_run_chat_research(*args, **kwargs):
            captured["intent"] = kwargs.get("intent")
            captured["run_id"] = kwargs.get("run_id")
            captured["query"] = kwargs.get("query")
            return {
                "run_id": "01REVIEWRUN",
                "status": "completed",
                "intent": "review_thesis",
                "display_summary": "done",
            }

        handle_chat_line(
            Settings.from_env(Path(".")),
            session.state,
            "review the previous conclusion",
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
            run_chat_research_fn=fake_run_chat_research,
        )

        self.assertEqual(captured["intent"], "review_thesis")
        self.assertEqual(captured["run_id"], "01PREVIOUS")

    def test_handle_chat_line_direct_answer_persists_into_thread_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState()
            outputs: list[TranscriptUpdate] = []

            handle_chat_line(
                settings,
                state,
                "who are you?",
                llm_provider=ProviderUnavailableStub(),
                task_router=TaskRouter(),
                cli_command_runner=lambda *args, **kwargs: {},
                payload_printer=lambda payload, as_json=False, output_writer=None: None,
                output_writer=outputs.append,
            )

            self.assertIsNotNone(state.thread_id)
            threads = list_chat_threads(settings=settings, limit=5)
            self.assertTrue(threads)
            self.assertEqual(threads[0]["thread_id"], state.thread_id)

    def test_handle_chat_line_stop_uses_stop_callback(self) -> None:
        state = ChatSessionState()
        stop_calls: list[bool] = []
        outputs: list[TranscriptUpdate] = []

        handle_chat_line(
            Settings.from_env(Path(".")),
            state,
            "/stop",
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=outputs.append,
            stop_callback=lambda: stop_calls.append(True),
        )

        self.assertEqual(stop_calls, [True])
        self.assertTrue(outputs)
        self.assertEqual(outputs[-1].kind, "meta")
        self.assertEqual(outputs[-1].text, "stop requested")

    def test_handle_chat_line_review_thesis_requires_run_id_emits_warning_update(self) -> None:
        state = ChatSessionState(current_intent="review_thesis")
        outputs: list[TranscriptUpdate] = []

        handle_chat_line(
            Settings.from_env(Path(".")),
            state,
            "continue",
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=outputs.append,
        )

        self.assertTrue(outputs)
        self.assertEqual(outputs[-1].kind, "warning")
        self.assertIn("review_thesis requires", outputs[-1].text)

    def test_orchestrator_event_listener_receives_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            events: list[str] = []

            def listener(event) -> None:
                events.append(event.event_type)

            orchestrator = ResearchOrchestrator(settings, event_listener=listener)
            try:
                orchestrator.retriever.qdrant_available = False
                orchestrator.retriever.qdrant_error = "disabled_for_test"
                response = orchestrator.run(RequestEnvelope(intent="stock_check", user_query="\u5206\u6790 000001"))

                self.assertIn(response.status, {"completed", "failed"})
                self.assertIn("request_received", events)
                self.assertIn("run_accepted", events)
            finally:
                orchestrator.close()

    def test_orchestrator_stock_check_consumes_methodology_memory_context_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            memory_service = MemoryService(LocalMemoryAdapter(settings))
            memory_service.write_methodology_memory(
                memory_type="dimension_experience",
                content={
                    "title": "Cashflow resilience heuristic",
                    "summary": "Prefer stable free cash flow and lower leverage.",
                    "benchmark_scope": "defensive_compounder",
                },
            )
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.retriever.disable_qdrant_for_test()
                orchestrator.collector.skill_runtime = StockCheckSkillRuntimeStub()
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="stock_check",
                        user_query="continue the previous conclusion",
                        conversation_context=[
                            {
                                "type": "relevant_methodology_memory",
                                "memory_type": "dimension_experience",
                                "benchmark_scope": "defensive_compounder",
                                "match_score": 3.5,
                            }
                        ],
                    )
                )
            finally:
                orchestrator.close()

            self.assertEqual(response.status, "completed")
            report = (response.result or {}).get("report") or {}
            debug = report.get("debug") or {}
            retrieval_summary = debug.get("retrieval_summary") or ""
            self.assertIn("methodology_memory=", retrieval_summary)
            self.assertIn("Cashflow resilience heuristic", retrieval_summary)
            self.assertIn("cashflow resilience", str(report.get("follow_up") or "").lower())


if __name__ == "__main__":
    unittest.main()
