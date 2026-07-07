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
from research_system.cli.interactions.chat_interaction_output_support import (
    emit_input_update,
    emit_meta_update,
    emit_warning_update,
    write_tui_update,
)
from research_system.cli.interactions.chat_interaction_run_support import (
    request_stop,
    submit_tui_input,
    toggle_commands_popup,
)
from research_system.cli.interactions.chat_interaction_output_support import (
    emit_input_update as pkg_emit_input_update,
    emit_meta_update as pkg_emit_meta_update,
    emit_warning_update as pkg_emit_warning_update,
    write_tui_update as pkg_write_tui_update,
)
from research_system.cli.interactions.chat_interaction_run_support import (
    request_stop as pkg_request_stop,
    submit_tui_input as pkg_submit_tui_input,
    toggle_commands_popup as pkg_toggle_commands_popup,
)


class ChatInteractionsPackageRuntimeTests(unittest.TestCase):
    def test_interactions_package_exports_remain_compatible_with_submodules(self) -> None:
        self.assertIs(emit_input_update, pkg_emit_input_update)
        self.assertIs(emit_meta_update, pkg_emit_meta_update)
        self.assertIs(emit_warning_update, pkg_emit_warning_update)
        self.assertIs(write_tui_update, pkg_write_tui_update)
        self.assertIs(request_stop, pkg_request_stop)
        self.assertIs(submit_tui_input, pkg_submit_tui_input)
        self.assertIs(toggle_commands_popup, pkg_toggle_commands_popup)


if __name__ == "__main__":
    unittest.main()
