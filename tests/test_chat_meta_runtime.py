from __future__ import annotations

import unittest

from research_system.cli.chat_meta_display_actions import (
    handle_show_last_action,
    handle_show_thread_action,
)
from research_system.cli.chat_meta_state_actions import (
    handle_clear_action,
    handle_set_events_action,
    handle_set_intent_action,
    handle_set_run_id_action,
    handle_set_thinking_action,
)
from research_system.cli.chat_meta_thread_actions import (
    handle_archive_thread_action,
    handle_compact_thread_action,
    handle_fork_thread_action,
    handle_new_thread_action,
    handle_read_thread_action,
    handle_rename_thread_action,
    handle_set_thread_action,
    handle_unarchive_thread_action,
)
from research_system.cli.meta.chat_meta_display_actions import (
    handle_show_last_action as package_handle_show_last_action,
    handle_show_thread_action as package_handle_show_thread_action,
)
from research_system.cli.meta.chat_meta_state_actions import (
    handle_clear_action as package_handle_clear_action,
    handle_set_events_action as package_handle_set_events_action,
    handle_set_intent_action as package_handle_set_intent_action,
    handle_set_run_id_action as package_handle_set_run_id_action,
    handle_set_thinking_action as package_handle_set_thinking_action,
)
from research_system.cli.meta.chat_meta_thread_actions import (
    handle_archive_thread_action as package_handle_archive_thread_action,
    handle_compact_thread_action as package_handle_compact_thread_action,
    handle_fork_thread_action as package_handle_fork_thread_action,
    handle_new_thread_action as package_handle_new_thread_action,
    handle_read_thread_action as package_handle_read_thread_action,
    handle_rename_thread_action as package_handle_rename_thread_action,
    handle_set_thread_action as package_handle_set_thread_action,
    handle_unarchive_thread_action as package_handle_unarchive_thread_action,
)


class ChatMetaRuntimeTests(unittest.TestCase):
    def test_top_level_meta_display_exports_match_package_exports(self) -> None:
        self.assertIs(handle_show_thread_action, package_handle_show_thread_action)
        self.assertIs(handle_show_last_action, package_handle_show_last_action)

    def test_top_level_meta_state_exports_match_package_exports(self) -> None:
        self.assertIs(handle_set_intent_action, package_handle_set_intent_action)
        self.assertIs(handle_set_run_id_action, package_handle_set_run_id_action)
        self.assertIs(handle_clear_action, package_handle_clear_action)
        self.assertIs(handle_set_thinking_action, package_handle_set_thinking_action)
        self.assertIs(handle_set_events_action, package_handle_set_events_action)

    def test_top_level_meta_thread_exports_match_package_exports(self) -> None:
        self.assertIs(handle_new_thread_action, package_handle_new_thread_action)
        self.assertIs(handle_rename_thread_action, package_handle_rename_thread_action)
        self.assertIs(handle_read_thread_action, package_handle_read_thread_action)
        self.assertIs(handle_compact_thread_action, package_handle_compact_thread_action)
        self.assertIs(handle_fork_thread_action, package_handle_fork_thread_action)
        self.assertIs(handle_archive_thread_action, package_handle_archive_thread_action)
        self.assertIs(handle_unarchive_thread_action, package_handle_unarchive_thread_action)
        self.assertIs(handle_set_thread_action, package_handle_set_thread_action)


if __name__ == "__main__":
    unittest.main()
