from __future__ import annotations

import unittest

from research_system.services import memory as memory_package
from research_system.services.memory import core as memory_core_package
from research_system.services.memory import query as memory_query_package
from research_system.services.memory import write as memory_write_package
from research_system.services.memory.base_query_support import MemoryServiceBaseQuerySupport
from research_system.services.memory.chat_query_support import MemoryServiceChatQuerySupport
from research_system.services.memory.methodology_writer import MethodologyMemoryWriter
from research_system.services.memory.methodology_query_support import MemoryServiceMethodologyQuerySupport
from research_system.services.memory.protocol import (
    CHAT_RESEARCH_MEMORY_TYPE,
    CHAT_RESEARCH_SNAPSHOT_VERSION,
    MEMORY_RECORD_SCHEMA_VERSION,
    METHODOLOGY_MEMORY_TYPES,
    METHODOLOGY_SNAPSHOT_VERSION,
    build_memory_id,
    build_memory_recorded_at,
)
from research_system.services.memory.query_support import MemoryServiceQuerySupport
from research_system.services.memory.service import MemoryService
from research_system.services.memory.write_support import MemoryServiceWriteSupport


class MemoryPackageRuntimeTests(unittest.TestCase):
    def test_memory_package_exports(self) -> None:
        self.assertIs(memory_package.MemoryServiceBaseQuerySupport, MemoryServiceBaseQuerySupport)
        self.assertIs(memory_package.MemoryServiceChatQuerySupport, MemoryServiceChatQuerySupport)
        self.assertIs(
            memory_package.MemoryServiceMethodologyQuerySupport,
            MemoryServiceMethodologyQuerySupport,
        )
        self.assertIs(memory_package.MemoryServiceQuerySupport, MemoryServiceQuerySupport)
        self.assertIs(memory_package.MemoryService, MemoryService)
        self.assertIs(memory_package.MemoryServiceWriteSupport, MemoryServiceWriteSupport)
        self.assertIs(memory_package.MethodologyMemoryWriter, MethodologyMemoryWriter)
        self.assertIs(memory_package.CHAT_RESEARCH_MEMORY_TYPE, CHAT_RESEARCH_MEMORY_TYPE)
        self.assertIs(memory_package.CHAT_RESEARCH_SNAPSHOT_VERSION, CHAT_RESEARCH_SNAPSHOT_VERSION)
        self.assertIs(memory_package.MEMORY_RECORD_SCHEMA_VERSION, MEMORY_RECORD_SCHEMA_VERSION)
        self.assertIs(memory_package.METHODOLOGY_MEMORY_TYPES, METHODOLOGY_MEMORY_TYPES)
        self.assertIs(memory_package.METHODOLOGY_SNAPSHOT_VERSION, METHODOLOGY_SNAPSHOT_VERSION)
        self.assertIs(memory_package.build_memory_id, build_memory_id)
        self.assertIs(memory_package.build_memory_recorded_at, build_memory_recorded_at)
        self.assertIs(memory_package.MemoryServiceCore, MemoryService)

    def test_memory_core_package_exports(self) -> None:
        self.assertIs(memory_core_package.MemoryService, MemoryService)
        self.assertIs(memory_core_package.CHAT_RESEARCH_MEMORY_TYPE, CHAT_RESEARCH_MEMORY_TYPE)
        self.assertIs(memory_core_package.CHAT_RESEARCH_SNAPSHOT_VERSION, CHAT_RESEARCH_SNAPSHOT_VERSION)
        self.assertIs(memory_core_package.MEMORY_RECORD_SCHEMA_VERSION, MEMORY_RECORD_SCHEMA_VERSION)
        self.assertIs(memory_core_package.METHODOLOGY_MEMORY_TYPES, METHODOLOGY_MEMORY_TYPES)
        self.assertIs(memory_core_package.METHODOLOGY_SNAPSHOT_VERSION, METHODOLOGY_SNAPSHOT_VERSION)
        self.assertIs(memory_core_package.build_memory_id, build_memory_id)
        self.assertIs(memory_core_package.build_memory_recorded_at, build_memory_recorded_at)

    def test_memory_query_package_exports(self) -> None:
        self.assertIs(memory_query_package.MemoryServiceBaseQuerySupport, MemoryServiceBaseQuerySupport)
        self.assertIs(memory_query_package.MemoryServiceChatQuerySupport, MemoryServiceChatQuerySupport)
        self.assertIs(
            memory_query_package.MemoryServiceMethodologyQuerySupport,
            MemoryServiceMethodologyQuerySupport,
        )
        self.assertIs(memory_query_package.MemoryServiceQuerySupport, MemoryServiceQuerySupport)

    def test_memory_write_package_exports(self) -> None:
        self.assertIs(memory_write_package.MemoryServiceWriteSupport, MemoryServiceWriteSupport)
        self.assertIs(memory_write_package.MethodologyMemoryWriter, MethodologyMemoryWriter)


if __name__ == "__main__":
    unittest.main()
