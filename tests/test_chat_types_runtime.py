from __future__ import annotations

import unittest

from research_system.cli.chat_types import (
    ChatSessionState,
    ShellUiState,
    ShellViewState,
    TuiSession,
)


class ChatTypesRuntimeTests(unittest.TestCase):
    def test_chat_types_facade_exports_runtime_models(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )

        self.assertIsInstance(session.view_state, ShellViewState)
        self.assertIsInstance(session.ui_state, ShellUiState)
        session.current_run_id = "01TRACE"
        self.assertEqual(session.view_state.current_run_id, "01TRACE")


if __name__ == "__main__":
    unittest.main()
