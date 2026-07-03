from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.cli.chat_thread_store import ChatThreadStore
from research_system.runtime.settings import Settings, ensure_workspace_layout


class ChatMemoryRuntimeTests(unittest.TestCase):
    def test_thread_store_supports_remain_available_for_memory_stack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)

            store = ChatThreadStore(settings)

            self.assertIsNotNone(store.index_support)
            self.assertIsNotNone(store.turn_support)
            self.assertIsNotNone(store.record_support)


if __name__ == "__main__":
    unittest.main()
