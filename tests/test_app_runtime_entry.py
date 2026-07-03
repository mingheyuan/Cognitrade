from __future__ import annotations

import unittest

from research_system.cli.app import build_parser, main
from research_system.cli.app_entry_support import build_app_settings, execute_app_main_command
from research_system.cli.app_parser_support import build_app_parser


class AppEntryRuntimeTests(unittest.TestCase):
    def test_app_entry_exports_parser_and_main_supports(self) -> None:
        self.assertIsNotNone(build_parser)
        self.assertIsNotNone(main)
        self.assertIsNotNone(build_app_parser)
        self.assertIsNotNone(build_app_settings)
        self.assertIsNotNone(execute_app_main_command)


if __name__ == "__main__":
    unittest.main()
