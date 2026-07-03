from __future__ import annotations

import unittest

from research_system.cli import application, meta, transcript
from research_system.cli.application.app_entry_support import build_app_settings, execute_app_main_command
from research_system.cli.application.app_entrypoint import (
    build_parser,
    execute_cli_command,
    handle_chat_line,
    main,
    print_cli_payload,
)
from research_system.cli.application.app_parser_support import build_app_parser
from research_system.cli.meta.chat_meta_display_actions import handle_show_last_action, handle_show_thread_action
from research_system.cli.meta.chat_meta_state_actions import (
    handle_clear_action,
    handle_set_events_action,
    handle_set_intent_action,
    handle_set_run_id_action,
    handle_set_thinking_action,
)
from research_system.cli.meta.chat_meta_thread_actions import (
    handle_archive_thread_action,
    handle_compact_thread_action,
    handle_fork_thread_action,
    handle_new_thread_action,
    handle_read_thread_action,
    handle_rename_thread_action,
    handle_set_thread_action,
    handle_unarchive_thread_action,
)
from research_system.cli.transcript.chat_transcript import (
    TranscriptMessage,
    display_block_title_for_message,
    make_transcript_message,
)
from research_system.cli.transcript.chat_transcript_store import (
    append_stream_transcript_message,
    append_transcript_message,
    clear_stream_slot,
    record_transcript_event,
    replace_transcript_message,
    reset_turn_scoped_slots,
)


class CliRemainingPackagesRuntimeTests(unittest.TestCase):
    def test_application_package_exports(self) -> None:
        self.assertIs(application.build_app_settings, build_app_settings)
        self.assertIs(application.execute_app_main_command, execute_app_main_command)
        self.assertIs(application.build_app_parser, build_app_parser)
        self.assertIs(application.build_parser, build_parser)
        self.assertIs(application.execute_cli_command, execute_cli_command)
        self.assertIs(application.handle_chat_line, handle_chat_line)
        self.assertIs(application.print_cli_payload, print_cli_payload)
        self.assertIs(application.main, main)

    def test_meta_package_exports(self) -> None:
        self.assertIs(meta.handle_show_thread_action, handle_show_thread_action)
        self.assertIs(meta.handle_show_last_action, handle_show_last_action)
        self.assertIs(meta.handle_set_intent_action, handle_set_intent_action)
        self.assertIs(meta.handle_set_run_id_action, handle_set_run_id_action)
        self.assertIs(meta.handle_clear_action, handle_clear_action)
        self.assertIs(meta.handle_set_thinking_action, handle_set_thinking_action)
        self.assertIs(meta.handle_set_events_action, handle_set_events_action)
        self.assertIs(meta.handle_new_thread_action, handle_new_thread_action)
        self.assertIs(meta.handle_rename_thread_action, handle_rename_thread_action)
        self.assertIs(meta.handle_read_thread_action, handle_read_thread_action)
        self.assertIs(meta.handle_compact_thread_action, handle_compact_thread_action)
        self.assertIs(meta.handle_fork_thread_action, handle_fork_thread_action)
        self.assertIs(meta.handle_archive_thread_action, handle_archive_thread_action)
        self.assertIs(meta.handle_unarchive_thread_action, handle_unarchive_thread_action)
        self.assertIs(meta.handle_set_thread_action, handle_set_thread_action)

    def test_transcript_package_exports(self) -> None:
        self.assertIs(transcript.TranscriptMessage, TranscriptMessage)
        self.assertIs(transcript.make_transcript_message, make_transcript_message)
        self.assertIs(transcript.display_block_title_for_message, display_block_title_for_message)
        self.assertIs(transcript.append_transcript_message, append_transcript_message)
        self.assertIs(transcript.replace_transcript_message, replace_transcript_message)
        self.assertIs(transcript.append_stream_transcript_message, append_stream_transcript_message)
        self.assertIs(transcript.clear_stream_slot, clear_stream_slot)
        self.assertIs(transcript.reset_turn_scoped_slots, reset_turn_scoped_slots)
        self.assertIs(transcript.record_transcript_event, record_transcript_event)


if __name__ == "__main__":
    unittest.main()
