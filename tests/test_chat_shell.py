from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from research_system.cli.shell.chat_fallback import run_chat_fallback
from research_system.cli.prompt_toolkit_compat import NoConsoleScreenBufferError
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.cli.state.chat_updates import SessionStatePatch, TranscriptUpdate
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope


class ChatShellTests(unittest.TestCase):
    def test_app_chat_defaults_to_line_mode(self) -> None:
        from research_system.cli import app as cli_app

        with patch("research_system.cli.app.run_chat_fallback", return_value=0) as line_mode, patch(
            "research_system.cli.app.run_chat_tui", return_value=0
        ) as tui_mode:
            result = cli_app.main(["chat"])

        self.assertEqual(result, 0)
        line_mode.assert_called_once()
        tui_mode.assert_not_called()

    def test_app_chat_tui_mode_uses_tui_entrypoint(self) -> None:
        from research_system.cli import app as cli_app

        with patch("research_system.cli.app.run_chat_tui", return_value=0) as tui_mode, patch(
            "research_system.cli.app.run_chat_fallback", return_value=0
        ) as line_mode:
            result = cli_app.main(["chat", "--ui", "tui"])

        self.assertEqual(result, 0)
        tui_mode.assert_called_once()
        line_mode.assert_not_called()

    def test_app_chat_falls_back_when_windows_console_is_unavailable(self) -> None:
        from research_system.cli import app as cli_app

        with patch("research_system.cli.app.run_chat_tui", side_effect=NoConsoleScreenBufferError()), patch(
            "research_system.cli.app.run_chat_fallback", return_value=0
        ) as fallback:
            result = cli_app.main(["chat", "--ui", "tui"])

        self.assertEqual(result, 0)
        fallback.assert_called_once()

    def test_orchestrator_event_listener_receives_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_root = Path(__file__).resolve().parents[1]
            settings = Settings.from_env(repository_root).model_copy(
                update={"runs_root": Path(temp_dir) / "runs", "memory_root": Path(temp_dir) / "memory"}
            )
            ensure_workspace_layout(settings)
            events: list[str] = []

            def listener(event) -> None:
                events.append(event.event_type)

            orchestrator = ResearchOrchestrator(settings, event_listener=listener)
            try:
                response = orchestrator.run(RequestEnvelope(intent="stock_check", user_query="甯垜鐪嬩笅 000001 杩欎釜鑲＄エ"))

                self.assertEqual(response.status, "completed")
                self.assertIn("request_received", events)
                self.assertIn("run_accepted", events)
            finally:
                orchestrator.close()

    def test_run_chat_fallback_applies_session_patch_and_persists_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            calls: list[str] = []

            def _handle_chat_line(settings_arg, state, line, **kwargs):
                calls.append(line)
                kwargs["output_writer"](
                    TranscriptUpdate(
                        action="append",
                        text="thread updated",
                        session_patch=SessionStatePatch(last_user_query="patched query", last_result_summary="patched summary"),
                    )
                )
                return "exit"

            stdout = io.StringIO()
            with patch("builtins.input", side_effect=["show last"]), redirect_stdout(stdout):
                result = run_chat_fallback(
                    settings,
                    initial_intent="stock_check",
                    initial_run_id="01TARGET",
                    thinking="on",
                    events="compact",
                    handle_chat_line=_handle_chat_line,
                    fallback_reason="line_mode_selected",
                )

            self.assertEqual(result, 0)
            self.assertEqual(calls, ["show last"])
            rows = ChatThreadStore(settings).list_threads(limit=1)
            self.assertTrue(rows)
            checkpoint = ChatThreadStore(settings).load_thread_checkpoint(thread_id=str(rows[0]["thread_id"]))
            self.assertEqual((checkpoint or {}).get("session_state", {}).get("last_user_query"), "patched query")
            self.assertEqual((checkpoint or {}).get("session_state", {}).get("last_result_summary"), "patched summary")
            self.assertIn("Cognitrade chat", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
