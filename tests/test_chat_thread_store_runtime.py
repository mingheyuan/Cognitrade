from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class ChatThreadStoreRuntimeTests(unittest.TestCase):
    def test_store_exposes_index_turn_and_record_supports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)

            store = ChatThreadStore(settings)

            self.assertIs(store.index_support.store, store)
            self.assertIs(store.turn_support.store, store)
            self.assertIs(store.record_support.store, store)


if __name__ == "__main__":
    unittest.main()
