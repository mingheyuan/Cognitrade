from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.cli import chat_thread_queries
from research_system.cli.memory import chat_thread_queries as canonical_chat_thread_queries
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class ChatThreadQueriesRuntimeTests(unittest.TestCase):
    def test_thread_query_facade_exports_remain_available(self) -> None:
        self.assertIs(chat_thread_queries.ensure_session_thread, canonical_chat_thread_queries.ensure_session_thread)
        self.assertIs(chat_thread_queries.read_session_thread, canonical_chat_thread_queries.read_session_thread)
        self.assertIs(chat_thread_queries.current_thread_summary, canonical_chat_thread_queries.current_thread_summary)
        self.assertIs(chat_thread_queries.thread_topic_title, canonical_chat_thread_queries.thread_topic_title)

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)

            self.assertTrue(callable(chat_thread_queries.ensure_session_thread))
            self.assertTrue(callable(chat_thread_queries.read_session_thread))
            self.assertTrue(callable(chat_thread_queries.current_thread_summary))
            self.assertEqual(chat_thread_queries.thread_topic_title(last_user_query="compare banks", last_result_summary=None), "compare banks")


if __name__ == "__main__":
    unittest.main()
