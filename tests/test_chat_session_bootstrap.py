from __future__ import annotations

import unittest
from pathlib import Path

from research_system.cli.chat_session_bootstrap import build_chat_banner_lines, build_chat_session_state, build_chat_startup_context
from research_system.runtime.settings import Settings


class ChatSessionBootstrapTests(unittest.TestCase):
    def test_build_chat_session_state_sets_defaults_and_thread(self) -> None:
        state = build_chat_session_state(
            Settings.from_env(Path(".")),
            initial_intent="stock_screen",
            initial_run_id="01TARGET",
            thinking="off",
            events="full",
        )

        self.assertEqual(state.current_intent, "stock_screen")
        self.assertEqual(state.review_target_run_id, "01TARGET")
        self.assertFalse(state.thinking_enabled)
        self.assertEqual(state.events_mode, "full")
        self.assertTrue((state.thread_id or "").startswith("thread_"))

    def test_build_chat_startup_context_returns_runtime_and_banner_lines_for_line_mode(self) -> None:
        runtime, banner_lines = build_chat_startup_context(
            Settings.from_env(Path(".")),
            initial_intent="stock_screen",
            initial_run_id="01TARGET",
            thinking="off",
            events="full",
            line_mode=True,
            fallback_reason="line_mode_selected",
        )

        self.assertEqual(runtime.state.current_intent, "stock_screen")
        self.assertEqual(runtime.state.review_target_run_id, "01TARGET")
        self.assertFalse(runtime.state.thinking_enabled)
        self.assertEqual(runtime.state.events_mode, "full")
        self.assertEqual(banner_lines[0], "Cognitrade chat")
        self.assertIn("mode: line", banner_lines)
        self.assertTrue(any(line.startswith("thread: ") for line in banner_lines))

    def test_build_chat_banner_lines_for_line_mode_omits_fallback_label(self) -> None:
        lines = build_chat_banner_lines(
            thread_id="thread_123",
            resumed_query=None,
            line_mode=True,
            fallback_reason="line_mode_selected",
        )

        self.assertEqual(lines[0], "Cognitrade chat")
        self.assertIn("mode: line", lines)
        self.assertIn("thread: thread_123", lines)
        self.assertFalse(any("fallback" in line.lower() for line in lines))

    def test_build_chat_banner_lines_for_tui_mentions_trace_controls(self) -> None:
        lines = build_chat_banner_lines(
            thread_id="thread_123",
            resumed_query=None,
            line_mode=False,
        )

        self.assertTrue(any("F2 toggles transcript/trace" in line for line in lines))
        self.assertTrue(any("F3 cycles trace task focus" in line for line in lines))
        self.assertTrue(any("F4 restores all tasks" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
