from __future__ import annotations

import unittest

from research_system.cli.chat_memory_candidate_dedupe import dedupe_relevant_candidates
from research_system.cli.chat_memory_external_candidates import (
    build_cross_thread_memory_candidates,
    build_methodology_memory_candidates,
)
from research_system.cli.chat_memory_relevant_support import build_relevant_memory_context
from research_system.cli.chat_memory_thread_candidates import (
    build_archive_candidates,
    build_summary_candidates,
    build_thread_profile_candidates,
)


class ChatMemoryRelevantRuntimeTests(unittest.TestCase):
    def test_chat_memory_relevant_support_exports_candidate_helpers(self) -> None:
        self.assertIsNotNone(build_relevant_memory_context)
        self.assertIsNotNone(build_thread_profile_candidates)
        self.assertIsNotNone(build_summary_candidates)
        self.assertIsNotNone(build_archive_candidates)
        self.assertIsNotNone(build_cross_thread_memory_candidates)
        self.assertIsNotNone(build_methodology_memory_candidates)
        self.assertIsNotNone(dedupe_relevant_candidates)


if __name__ == "__main__":
    unittest.main()
