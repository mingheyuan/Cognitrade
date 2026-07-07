from __future__ import annotations

import unittest

from research_system.cli.commands.chat_command_catalog import CHAT_COMMAND_SUMMARY_LINES, CHAT_HELP_LINES, print_chat_help


class ChatCommandCatalogTests(unittest.TestCase):
    def test_print_chat_help_emits_all_help_lines(self) -> None:
        updates = []

        print_chat_help(output_writer=updates.append)

        self.assertEqual(len(updates), len(CHAT_HELP_LINES))
        self.assertEqual([update.text for update in updates], CHAT_HELP_LINES)

    def test_help_and_popup_command_catalog_cover_proposal_commands(self) -> None:
        self.assertTrue(any("/proposals" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/proposal" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/review-proposal" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/proposals" in line for line in CHAT_COMMAND_SUMMARY_LINES))
        self.assertTrue(any("/proposal" in line for line in CHAT_COMMAND_SUMMARY_LINES))
        self.assertTrue(any("/review-proposal" in line for line in CHAT_COMMAND_SUMMARY_LINES))

    def test_help_and_popup_command_catalog_cover_fork_command(self) -> None:
        self.assertTrue(any("/fork" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/fork" in line for line in CHAT_COMMAND_SUMMARY_LINES))

    def test_help_and_popup_command_catalog_cover_rename_command(self) -> None:
        self.assertTrue(any("/rename" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/rename" in line for line in CHAT_COMMAND_SUMMARY_LINES))

    def test_help_and_popup_command_catalog_cover_thread_archive_commands(self) -> None:
        self.assertTrue(any("/thread-archive" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/thread-unarchive" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/thread-archive" in line for line in CHAT_COMMAND_SUMMARY_LINES))
        self.assertTrue(any("/thread-unarchive" in line for line in CHAT_COMMAND_SUMMARY_LINES))

    def test_help_and_popup_command_catalog_cover_thread_read_command(self) -> None:
        self.assertTrue(any("/thread-read" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/thread-read" in line for line in CHAT_COMMAND_SUMMARY_LINES))

    def test_help_and_popup_command_catalog_cover_subagent_read_command(self) -> None:
        self.assertTrue(any("/subagent-read" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/subagent-read" in line for line in CHAT_COMMAND_SUMMARY_LINES))

    def test_help_and_popup_command_catalog_cover_run_steps_command(self) -> None:
        self.assertTrue(any("/run-steps" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/run-steps" in line for line in CHAT_COMMAND_SUMMARY_LINES))

    def test_help_and_popup_command_catalog_cover_runtime_devtool_commands(self) -> None:
        self.assertTrue(any("/metrics" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/run-thread" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/rerun-from" in line for line in CHAT_HELP_LINES))
        self.assertTrue(any("/metrics" in line for line in CHAT_COMMAND_SUMMARY_LINES))
        self.assertTrue(any("/run-thread" in line for line in CHAT_COMMAND_SUMMARY_LINES))
        self.assertTrue(any("/rerun-from" in line for line in CHAT_COMMAND_SUMMARY_LINES))


if __name__ == "__main__":
    unittest.main()
