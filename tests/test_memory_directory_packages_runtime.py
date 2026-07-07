from __future__ import annotations

import unittest

from research_system.services.memory import query as memory_query_package
from research_system.services.memory import write as memory_write_package
from research_system.services.memory.core import service as memory_service_package
from research_system.services.memory.write import methodology_writer as methodology_writer_package
from research_system.services.memory.query.base_query_support import MemoryServiceBaseQuerySupport
from research_system.services.memory.query.chat_query_support import MemoryServiceChatQuerySupport
from research_system.services.memory.query.methodology_query_support import MemoryServiceMethodologyQuerySupport
from research_system.services.memory.write.methodology_writer import MethodologyMemoryWriter
from research_system.services.memory.query.query_support import MemoryServiceQuerySupport
from research_system.services.memory.write.write_support import MemoryServiceWriteSupport


class MemoryDirectoryPackagesRuntimeTests(unittest.TestCase):
    def test_query_package_exports(self) -> None:
        self.assertIs(memory_query_package.MemoryServiceBaseQuerySupport, MemoryServiceBaseQuerySupport)
        self.assertIs(memory_query_package.MemoryServiceChatQuerySupport, MemoryServiceChatQuerySupport)
        self.assertIs(
            memory_query_package.MemoryServiceMethodologyQuerySupport,
            MemoryServiceMethodologyQuerySupport,
        )
        self.assertIs(memory_query_package.MemoryServiceQuerySupport, MemoryServiceQuerySupport)

    def test_write_package_exports(self) -> None:
        self.assertIs(memory_write_package.MemoryServiceWriteSupport, MemoryServiceWriteSupport)
        self.assertIs(memory_write_package.MethodologyMemoryWriter, MethodologyMemoryWriter)
        self.assertIs(methodology_writer_package.MethodologyMemoryWriter, MethodologyMemoryWriter)
        self.assertTrue(hasattr(memory_service_package, "MemoryService"))


if __name__ == "__main__":
    unittest.main()
