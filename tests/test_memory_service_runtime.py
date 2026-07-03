from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.adapters.storage.mem0_adapter import Mem0Adapter
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.services.memory_service_base_query_support import MemoryServiceBaseQuerySupport
from research_system.services.memory_service_chat_query_support import MemoryServiceChatQuerySupport
from research_system.services.memory_service import MemoryService
from research_system.services.memory_service_methodology_query_support import (
    MemoryServiceMethodologyQuerySupport,
)
from research_system.services.memory_service_query_support import MemoryServiceQuerySupport
from research_system.services.memory_service_write_support import MemoryServiceWriteSupport


class MemoryServiceRuntimeTests(unittest.TestCase):
    def test_memory_service_wires_write_and_query_support(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)

            service = MemoryService(Mem0Adapter(settings))

            self.assertIsInstance(service.write_support, MemoryServiceWriteSupport)
            self.assertIsInstance(service.query_support, MemoryServiceQuerySupport)
            self.assertIsInstance(service.query_support.base_query_support, MemoryServiceBaseQuerySupport)
            self.assertIsInstance(service.query_support.chat_query_support, MemoryServiceChatQuerySupport)
            self.assertIsInstance(
                service.query_support.methodology_query_support,
                MemoryServiceMethodologyQuerySupport,
            )
            self.assertIs(service.write_support.adapter, service.adapter)
            self.assertIs(service.query_support.adapter, service.adapter)


if __name__ == "__main__":
    unittest.main()
