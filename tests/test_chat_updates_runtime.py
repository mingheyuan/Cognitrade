from __future__ import annotations

import unittest

from research_system.cli.state import TranscriptUpdate as StateTranscriptUpdate
from research_system.cli.state import (
    SESSION_PATCH_UNSET,
    SessionStatePatch,
    ShellStatePatch,
    TranscriptUpdate,
    append_update,
    dispatch_transcript_update,
    warning_update,
)


class ChatUpdatesRuntimeTests(unittest.TestCase):
    def test_top_level_chat_updates_remains_compatible_with_state_package(self) -> None:
        self.assertIs(TranscriptUpdate, StateTranscriptUpdate)

    def test_chat_updates_facade_exports_models_and_helpers(self) -> None:
        update = append_update(
            "meta",
            "hello",
            state_patch=ShellStatePatch(run_status_label="ok"),
            session_patch=SessionStatePatch(last_run_id="01RUN"),
        )

        self.assertIs(SESSION_PATCH_UNSET, SessionStatePatch().thread_id)
        self.assertIsInstance(update, TranscriptUpdate)
        self.assertEqual(warning_update("warn").state_patch.run_status_label, "warning")
        self.assertIsNotNone(dispatch_transcript_update)


if __name__ == "__main__":
    unittest.main()
