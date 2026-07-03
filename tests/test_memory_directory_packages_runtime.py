from __future__ import annotations

import unittest

from research_system.services.memory import query as memory_query_package
from research_system.services.memory import write as memory_write_package
from research_system.services.memory.base_query_support import MemoryServiceBaseQuerySupport
from research_system.services.memory.chat_query_support import MemoryServiceChatQuerySupport
from research_system.services.memory.methodology_query_support import MemoryServiceMethodologyQuerySupport
from research_system.services.memory.methodology_writer import MethodologyMemoryWriter
from research_system.services.memory.query_support import MemoryServiceQuerySupport
from research_system.services.memory.write_support import MemoryServiceWriteSupport


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


if __name__ == "__main__":
    unittest.main()
