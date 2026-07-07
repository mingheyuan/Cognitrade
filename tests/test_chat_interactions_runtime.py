from __future__ import annotations

import unittest

from research_system.cli.interactions import (
    emit_input_update,
    emit_meta_update,
    emit_warning_update,
    request_stop,
    submit_tui_input,
    toggle_commands_popup,
    write_tui_update,
)


class ChatInteractionsRuntimeTests(unittest.TestCase):
    def test_chat_interactions_facade_exports_runtime_helpers(self) -> None:
        self.assertIsNotNone(submit_tui_input)
        self.assertIsNotNone(request_stop)
        self.assertIsNotNone(toggle_commands_popup)
        self.assertIsNotNone(write_tui_update)
        self.assertIsNotNone(emit_meta_update)
        self.assertIsNotNone(emit_input_update)
        self.assertIsNotNone(emit_warning_update)


if __name__ == "__main__":
    unittest.main()
