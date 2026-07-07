from __future__ import annotations

import unittest

from research_system.cli.runtime.app_runtime import (
    CliCommandRuntime,
    ChatAppRuntime,
    ChatEntryRuntime,
)


class AppRuntimeRuntimeTests(unittest.TestCase):
    def test_app_runtime_facade_exports_runtime_types(self) -> None:
        self.assertIsNotNone(CliCommandRuntime)
        self.assertIsNotNone(ChatAppRuntime)
        self.assertIsNotNone(ChatEntryRuntime)

    def test_chat_app_runtime_forwards_settings_and_state_to_basic_answer_stream(self) -> None:
        captured: dict[str, object] = {}

        def basic_answer_stream(**kwargs):
            captured["kwargs"] = kwargs
            return "streamed answer"

        def controller(settings, state, line, **kwargs):
            result = kwargs["basic_answer_stream_fn"](
                query="who are you",
                llm_provider="provider",
                output_writer=None,
                conversation_context=[],
            )
            captured["result"] = result
            return "done"

        runtime = ChatAppRuntime(
            controller_handle_chat_line_fn=controller,
            cli_command_runner=None,
            payload_printer=lambda *args, **kwargs: None,
            dispatch_transcript_update_fn=lambda *args, **kwargs: None,
            chat_dispatch_fn=None,
            basic_answer_stream_print_fn=basic_answer_stream,
            run_chat_research_fn=None,
            print_chat_result_payload_fn=lambda *args, **kwargs: None,
        )

        result = runtime.handle_chat_line(
            settings="settings_obj",
            state="state_obj",
            line="hello",
            llm_provider="provider",
            task_router="router",
        )

        self.assertEqual(result, "done")
        self.assertEqual(captured["result"], "streamed answer")
        self.assertEqual(captured["kwargs"]["settings"], "settings_obj")
        self.assertEqual(captured["kwargs"]["state"], "state_obj")
        self.assertIn("emit_text", captured["kwargs"])

    def test_chat_app_runtime_forwards_settings_and_state_to_result_printer(self) -> None:
        captured: dict[str, object] = {}

        def result_printer(payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            return None

        def controller(settings, state, line, **kwargs):
            kwargs["print_chat_result_payload_fn"](
                {"status": "completed", "display_summary": "ok"},
                llm_provider="provider",
                query="q",
                requested_intent=None,
                output_writer=None,
            )
            return "done"

        runtime = ChatAppRuntime(
            controller_handle_chat_line_fn=controller,
            cli_command_runner=None,
            payload_printer=lambda *args, **kwargs: None,
            dispatch_transcript_update_fn=lambda *args, **kwargs: None,
            chat_dispatch_fn=None,
            basic_answer_stream_print_fn=lambda **kwargs: None,
            run_chat_research_fn=None,
            print_chat_result_payload_fn=result_printer,
        )

        result = runtime.handle_chat_line(
            settings="settings_obj",
            state="state_obj",
            line="hello",
            llm_provider="provider",
            task_router="router",
        )

        self.assertEqual(result, "done")
        self.assertEqual(captured["payload"], {"status": "completed", "display_summary": "ok"})
        self.assertEqual(captured["kwargs"]["settings"], "settings_obj")
        self.assertEqual(captured["kwargs"]["state"], "state_obj")


if __name__ == "__main__":
    unittest.main()
