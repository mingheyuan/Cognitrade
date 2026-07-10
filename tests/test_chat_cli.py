from __future__ import annotations

import unittest
from pathlib import Path

from research_system.cli.commands.chat_commands import parse_chat_input as commands_parse_chat_input
from research_system.cli.commands.chat_commands import (
    chat_completion_candidates,
    chat_completion_current_token,
    clear_pending_clarification,
    merge_pending_clarification,
    parse_chat_input,
    resolve_chat_run_id,
    should_resume_pending_clarification,
    store_pending_clarification,
)
from research_system.cli.flow.chat_dispatch import answer_basic_chat
from research_system.cli.follow_up import (
    is_short_term_memory_query,
    looks_like_follow_up_research_query,
    resolve_follow_up_research_request,
    short_term_memory_reply,
)
from research_system.cli.state.chat_types import ChatSessionState
from research_system.runtime.platform.settings import Settings


class ChatCliTests(unittest.TestCase):
    def test_top_level_chat_commands_remains_compatible_with_commands_package(self) -> None:
        self.assertIs(parse_chat_input, commands_parse_chat_input)

    def test_plain_text_maps_to_run(self) -> None:
        parsed = parse_chat_input("最近创新药板块怎么样")
        self.assertEqual(parsed, {"action": "run", "query": "最近创新药板块怎么样"})

    def test_run_command_marks_explicit_research_request(self) -> None:
        parsed = parse_chat_input("/run 分析贵州茅台")
        self.assertEqual(parsed, {"action": "run", "query": "分析贵州茅台", "explicit_command": True})

    def test_resume_command_is_parsed(self) -> None:
        parsed = parse_chat_input("/resume 01ABC")
        self.assertEqual(parsed, {"action": "resume", "run_id": "01ABC"})

    def test_trace_command_is_parsed(self) -> None:
        parsed = parse_chat_input("/trace 01ABC")
        self.assertEqual(parsed, {"action": "trace", "run_id": "01ABC"})

    def test_review_command_sets_intent_and_run_id(self) -> None:
        parsed = parse_chat_input('/review 01TARGET "复核上次创新药判断"')
        self.assertEqual(
            parsed,
            {
                "action": "run",
                "intent": "review_thesis",
                "run_id": "01TARGET",
                "query": "复核上次创新药判断",
            },
        )

    def test_resolve_last_run_id(self) -> None:
        state = ChatSessionState(last_run_id="01LAST")
        self.assertEqual(resolve_chat_run_id("last", state), "01LAST")
        self.assertEqual(resolve_chat_run_id("01ABC", state), "01ABC")

    def test_intent_auto_clears_default(self) -> None:
        parsed = parse_chat_input("/intent auto")
        self.assertEqual(parsed, {"action": "set_intent", "intent": None})

    def test_thinking_command_is_parsed(self) -> None:
        parsed = parse_chat_input("/thinking off")
        self.assertEqual(parsed, {"action": "set_thinking", "enabled": False})

    def test_events_command_is_parsed(self) -> None:
        parsed = parse_chat_input("/events full")
        self.assertEqual(parsed, {"action": "set_events", "events_mode": "full"})

    def test_events_read_command_is_parsed(self) -> None:
        self.assertEqual(parse_chat_input("/events last"), {"action": "events", "run_id": "last"})
        self.assertEqual(parse_chat_input("/events-read 01ABC"), {"action": "events", "run_id": "01ABC"})
        self.assertEqual(parse_chat_input("/metrics 01ABC"), {"action": "metrics", "run_id": "01ABC"})
        self.assertEqual(
            parse_chat_input("/run-thread last thread-child"),
            {"action": "run-thread", "run_id": "last", "thread_id": "thread-child"},
        )
        self.assertEqual(
            parse_chat_input("/subagent-read last 01INVOCATION"),
            {"action": "subagent-read", "run_id": "last", "call_id": "01INVOCATION"},
        )
        self.assertEqual(parse_chat_input("/run-steps last"), {"action": "run-steps", "run_id": "last"})
        self.assertEqual(
            parse_chat_input("/rerun-from last retrieve"),
            {"action": "rerun-from", "run_id": "last", "node_id": "retrieve"},
        )

    def test_stop_command_is_parsed(self) -> None:
        parsed = parse_chat_input("/stop")
        self.assertEqual(parsed, {"action": "stop"})

    def test_commands_command_is_parsed(self) -> None:
        parsed = parse_chat_input("/commands")
        self.assertEqual(parsed, {"action": "commands"})

    def test_new_thread_command_is_parsed(self) -> None:
        parsed = parse_chat_input("/new")
        self.assertEqual(parsed, {"action": "new_thread"})

    def test_fork_thread_command_is_parsed(self) -> None:
        parsed = parse_chat_input("/fork")
        self.assertEqual(parsed, {"action": "fork_thread"})

    def test_rename_thread_command_is_parsed(self) -> None:
        parsed = parse_chat_input('/rename "Bank compare shortlist"')
        self.assertEqual(parsed, {"action": "rename_thread", "thread_label": "Bank compare shortlist"})

    def test_thread_archive_commands_are_parsed(self) -> None:
        self.assertEqual(parse_chat_input("/thread-archive"), {"action": "archive_thread"})
        self.assertEqual(
            parse_chat_input("/thread-archive abc123"),
            {"action": "archive_thread", "thread_id": "abc123"},
        )
        self.assertEqual(
            parse_chat_input("/thread-unarchive abc123"),
            {"action": "unarchive_thread", "thread_id": "abc123"},
        )

    def test_thread_command_is_parsed(self) -> None:
        self.assertEqual(parse_chat_input("/thread"), {"action": "show_thread"})
        self.assertEqual(parse_chat_input("/thread abc123"), {"action": "set_thread", "thread_id": "abc123"})
        self.assertEqual(parse_chat_input("/thread-read abc123"), {"action": "read_thread", "thread_id": "abc123"})

    def test_threads_command_is_parsed(self) -> None:
        self.assertEqual(parse_chat_input("/threads"), {"action": "threads", "limit": 10, "scope": "active"})
        self.assertEqual(parse_chat_input("/threads 5"), {"action": "threads", "limit": 5, "scope": "active"})
        self.assertEqual(parse_chat_input("/threads archived"), {"action": "threads", "limit": 10, "scope": "archived"})
        self.assertEqual(parse_chat_input("/threads 5 all"), {"action": "threads", "limit": 5, "scope": "all"})

    def test_compact_command_is_parsed(self) -> None:
        self.assertEqual(parse_chat_input("/compact"), {"action": "compact_thread"})

    def test_proposals_commands_are_parsed(self) -> None:
        self.assertEqual(parse_chat_input("/proposals"), {"action": "proposals", "limit": 50, "status": None})
        self.assertEqual(parse_chat_input("/proposals 10 pending"), {"action": "proposals", "limit": 10, "status": "pending"})
        self.assertEqual(parse_chat_input("/proposal 01PROP"), {"action": "proposal", "proposal_id": "01PROP"})
        self.assertEqual(
            parse_chat_input("/review-proposal 01PROP approve looks good"),
            {"action": "review-proposal", "proposal_id": "01PROP", "decision": "approve", "reason": "looks good"},
        )
        self.assertEqual(
            parse_chat_input("/subagent-discoveries 10 approved"),
            {"action": "subagent-discoveries", "limit": 10, "status": "approved"},
        )
        self.assertEqual(
            parse_chat_input("/subagent-discovery 01DISC"),
            {"action": "subagent-discovery", "ticket_id": "01DISC"},
        )
        self.assertEqual(
            parse_chat_input("/review-subagent-discovery 01DISC approve validated"),
            {
                "action": "review-subagent-discovery",
                "ticket_id": "01DISC",
                "decision": "approve",
                "reason": "validated",
            },
        )
        self.assertEqual(
            parse_chat_input("/activate-subagent-discovery 01DISC stock_snapshot_collector candidate_screening_strategy"),
            {
                "action": "activate-subagent-discovery",
                "ticket_id": "01DISC",
                "delegate_subagent_type": "stock_snapshot_collector",
                "candidate_subagent_type": "candidate_screening_strategy",
            },
        )
        self.assertEqual(
            parse_chat_input(
                "/activate-subagent-discovery 01DISC --registration-type http_json_runner "
                "--candidate candidate_collect_http_extension --source-id eastmoney "
                "--endpoint-path /api/market-signal --http-method GET --timeout-seconds 9.5"
            ),
            {
                "action": "activate-subagent-discovery",
                "ticket_id": "01DISC",
                "delegate_subagent_type": None,
                "candidate_subagent_type": "candidate_collect_http_extension",
                "registration_template_id": None,
                "registration_type": "http_json_runner",
                "skill_name": None,
                "snapshot_type": None,
                "source_id": "eastmoney",
                "tool_name": None,
                "endpoint_path": "/api/market-signal",
                "http_method": "GET",
                "openapi_spec_uri": None,
                "openapi_operation_id": None,
                "timeout_seconds": 9.5,
            },
        )
        self.assertEqual(
            parse_chat_input(
                "/activate-subagent-discovery 01DISC --registration-type openapi_http_runner "
                "--candidate candidate_collect_http_extension --source-id eastmoney "
                "--openapi-spec-uri https://example.com/openapi.json "
                "--openapi-operation-id getMarketSignal"
            ),
            {
                "action": "activate-subagent-discovery",
                "ticket_id": "01DISC",
                "delegate_subagent_type": None,
                "candidate_subagent_type": "candidate_collect_http_extension",
                "registration_template_id": None,
                "registration_type": "openapi_http_runner",
                "skill_name": None,
                "snapshot_type": None,
                "source_id": "eastmoney",
                "tool_name": None,
                "endpoint_path": None,
                "http_method": None,
                "openapi_spec_uri": "https://example.com/openapi.json",
                "openapi_operation_id": "getMarketSignal",
                "timeout_seconds": None,
            },
        )
        self.assertEqual(
            parse_chat_input(
                "/activate-subagent-discovery 01DISC --registration-type mcp_tool_runner "
                "--candidate candidate_collect_mcp_extension --source-id akshare_mcp "
                "--tool-name market_signal_lookup --timeout-seconds 7.5"
            ),
            {
                "action": "activate-subagent-discovery",
                "ticket_id": "01DISC",
                "delegate_subagent_type": None,
                "candidate_subagent_type": "candidate_collect_mcp_extension",
                "registration_template_id": None,
                "registration_type": "mcp_tool_runner",
                "skill_name": None,
                "snapshot_type": None,
                "source_id": "akshare_mcp",
                "tool_name": "market_signal_lookup",
                "endpoint_path": None,
                "http_method": None,
                "openapi_spec_uri": None,
                "openapi_operation_id": None,
                "timeout_seconds": 7.5,
            },
        )
        self.assertEqual(
            parse_chat_input(
                "/activate-subagent-discovery 01DISC --registration-template-id openapi:getMarketSignal "
                "--candidate candidate_collect_http_extension"
            ),
            {
                "action": "activate-subagent-discovery",
                "ticket_id": "01DISC",
                "delegate_subagent_type": None,
                "candidate_subagent_type": "candidate_collect_http_extension",
                "registration_template_id": "openapi:getMarketSignal",
                "registration_type": None,
                "skill_name": None,
                "snapshot_type": None,
                "source_id": None,
                "tool_name": None,
                "endpoint_path": None,
                "http_method": None,
                "openapi_spec_uri": None,
                "openapi_operation_id": None,
                "timeout_seconds": None,
            },
        )

    def test_pending_clarification_can_resume_plain_follow_up(self) -> None:
        state = ChatSessionState(
            pending_clarification_query="最近有什么股票值得投入",
            pending_clarification_intent=None,
        )
        self.assertTrue(
            should_resume_pending_clarification(
                query="中线",
                intent=None,
                state=state,
            )
        )

    def test_pending_clarification_merges_follow_up_into_query(self) -> None:
        state = ChatSessionState(
            pending_clarification_query="最近有什么股票值得投入",
            pending_clarification_intent="stock_screen",
            pending_clarification_run_id="01CLARIFY",
        )
        query, intent, run_id = merge_pending_clarification(
            query="中线",
            intent=None,
            run_id=None,
            state=state,
        )
        self.assertIn("最近有什么股票值得投入", query)
        self.assertIn("补充信息: 中线", query)
        self.assertEqual(intent, "stock_screen")
        self.assertEqual(run_id, "01CLARIFY")

    def test_completion_current_token_uses_last_segment(self) -> None:
        self.assertEqual(chat_completion_current_token("/eve"), "/eve")
        self.assertEqual(chat_completion_current_token("/events fu"), "fu")
        self.assertEqual(chat_completion_current_token("/events "), "")

    def test_completion_candidates_for_intent_command(self) -> None:
        candidates = chat_completion_candidates("/intent ", ChatSessionState())
        self.assertIn("stock_screen", candidates)
        self.assertIn("stock_check", candidates)
        self.assertIn("stock_compare", candidates)
        self.assertIn("review_thesis", candidates)

    def test_completion_candidates_include_recent_run_ids(self) -> None:
        state = ChatSessionState(last_run_id="01LAST", review_target_run_id="01TARGET")
        candidates = chat_completion_candidates("/resume ", state)
        self.assertEqual(candidates, ["last", "01LAST", "01TARGET"])
        self.assertEqual(chat_completion_candidates("/trace ", state), ["last", "01LAST", "01TARGET"])
        self.assertEqual(chat_completion_candidates("/events ", state), ["off", "compact", "full", "last", "01LAST", "01TARGET"])
        self.assertEqual(chat_completion_candidates("/events-read ", state), ["last", "01LAST", "01TARGET"])
        self.assertEqual(chat_completion_candidates("/metrics ", state), ["last", "01LAST", "01TARGET"])
        self.assertEqual(chat_completion_candidates("/run-thread ", state), ["last", "01LAST", "01TARGET"])
        self.assertEqual(chat_completion_candidates("/subagent-read ", state), ["last", "01LAST", "01TARGET"])
        self.assertEqual(chat_completion_candidates("/run-steps ", state), ["last", "01LAST", "01TARGET"])
        self.assertEqual(chat_completion_candidates("/rerun-from ", state), ["last", "01LAST", "01TARGET"])

    def test_completion_candidates_for_proposal_commands(self) -> None:
        self.assertEqual(chat_completion_candidates("/proposals ", ChatSessionState()), ["10", "20", "50", "pending", "approved", "rejected"])
        self.assertEqual(chat_completion_candidates("/review-proposal 01PROP ", ChatSessionState()), ["approve", "reject"])
        self.assertEqual(
            chat_completion_candidates("/subagent-discoveries ", ChatSessionState()),
            ["10", "20", "50", "pending", "approved", "rejected"],
        )
        self.assertEqual(
            chat_completion_candidates("/review-subagent-discovery 01DISC ", ChatSessionState()),
            ["approve", "reject"],
        )
        self.assertEqual(chat_completion_candidates("/threads ", ChatSessionState()), ["5", "10", "20", "active", "all", "archived"])

    def test_store_pending_clarification_returns_session_patch(self) -> None:
        patch = store_pending_clarification(
            query="最近有什么股票值得投入",
            intent="stock_screen",
            run_id="01CLARIFY",
            payload={"clarification": {"question": "Short-term, mid-term, or long-term?"}},
        )
        self.assertEqual(patch.pending_clarification_query, "最近有什么股票值得投入")
        self.assertEqual(patch.pending_clarification_intent, "stock_screen")
        self.assertEqual(patch.pending_clarification_run_id, "01CLARIFY")
        self.assertEqual(patch.pending_clarification_question, "Short-term, mid-term, or long-term?")

    def test_clear_pending_clarification_returns_session_patch(self) -> None:
        patch = clear_pending_clarification()
        self.assertIsNone(patch.pending_clarification_query)
        self.assertIsNone(patch.pending_clarification_intent)
        self.assertIsNone(patch.pending_clarification_run_id)
        self.assertIsNone(patch.pending_clarification_question)

    def test_short_term_memory_query_detector_matches_recent_topic_question(self) -> None:
        self.assertTrue(is_short_term_memory_query("刚刚我们在聊什么"))
        self.assertTrue(is_short_term_memory_query("what were we just talking about?"))

    def test_short_term_memory_reply_uses_last_session_result(self) -> None:
        state = ChatSessionState(
            last_user_query="分析茅台，宁德时代，比亚迪，谁更值得投资",
            last_result_intent="stock_compare",
            last_result_summary="三只股票对比，结论降级。",
            last_run_id="01LAST",
        )

        reply = short_term_memory_reply(state)

        self.assertIn("刚刚我们在聊：", reply)
        self.assertIn("分析茅台，宁德时代，比亚迪，谁更值得投资", reply)
        self.assertIn("stock_compare", reply)
        self.assertIn("三只股票对比，结论降级。", reply)

    def test_answer_basic_chat_passes_recent_context_into_prompt(self) -> None:
        class ProviderStub:
            available = True

            def __init__(self) -> None:
                self.user_prompt = None

            def complete(self, **kwargs):
                self.user_prompt = kwargs["user_prompt"]
                return "ok"

        provider = ProviderStub()

        reply = answer_basic_chat(
            settings=Settings.from_env(Path(".")),
            state=object(),
            query="那刚才的结论是什么意思",
            llm_provider=provider,  # type: ignore[arg-type]
            conversation_context=[
                {
                    "type": "recent_research_memory",
                    "query": "分析茅台和宁德时代",
                    "intent": "stock_compare",
                    "summary": "两只股票对比，结论偏谨慎。",
                    "run_id": "01CHAT",
                }
            ],
        )

        self.assertEqual(reply, "ok")
        self.assertIsNotNone(provider.user_prompt)
        self.assertIn("Recent conversation context", provider.user_prompt)
        self.assertIn("分析茅台和宁德时代", provider.user_prompt)

    def test_follow_up_detector_matches_context_dependent_research_query(self) -> None:
        self.assertTrue(looks_like_follow_up_research_query("谁更稳健一点"))
        self.assertTrue(looks_like_follow_up_research_query("复核上一轮结论"))
        self.assertFalse(looks_like_follow_up_research_query("帮我继续分析 000001"))

    def test_follow_up_resolution_reuses_previous_compare_context(self) -> None:
        state = ChatSessionState(
            last_user_query="分析茅台，宁德时代，比亚迪，谁更值得投资",
            last_result_intent="stock_compare",
            last_run_id="01COMPARE",
        )

        resolution = resolve_follow_up_research_request(
            query="谁更稳健一点",
            parsed_intent=None,
            parsed_run_id=None,
            state=state,
            conversation_context=[],
        )

        self.assertTrue(resolution.resolved)
        self.assertEqual(resolution.intent, "stock_compare")
        self.assertIsNone(resolution.run_id)
        self.assertIn("分析茅台，宁德时代，比亚迪，谁更值得投资", resolution.query)

    def test_follow_up_resolution_maps_review_request_to_last_run(self) -> None:
        state = ChatSessionState(
            last_user_query="分析贵州茅台",
            last_result_intent="stock_check",
            last_run_id="01PREVIOUS",
        )

        resolution = resolve_follow_up_research_request(
            query="复核上一轮结论",
            parsed_intent=None,
            parsed_run_id=None,
            state=state,
            conversation_context=[],
        )

        self.assertTrue(resolution.resolved)
        self.assertEqual(resolution.intent, "review_thesis")
        self.assertEqual(resolution.run_id, "01PREVIOUS")


if __name__ == "__main__":
    unittest.main()
