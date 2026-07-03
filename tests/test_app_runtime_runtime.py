from __future__ import annotations

import unittest

from research_system.cli.app_runtime import (
    CliCommandRuntime,
    ChatAppRuntime,
    ChatEntryRuntime,
)


class AppRuntimeRuntimeTests(unittest.TestCase):
    def test_app_runtime_facade_exports_runtime_types(self) -> None:
        self.assertIsNotNone(CliCommandRuntime)
        self.assertIsNotNone(ChatAppRuntime)
        self.assertIsNotNone(ChatEntryRuntime)


if __name__ == "__main__":
    unittest.main()
