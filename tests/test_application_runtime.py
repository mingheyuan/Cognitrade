from __future__ import annotations

import unittest

from research_system.cli.app import build_parser, main
from research_system.cli.application.app_entry_support import (
    build_app_settings as application_build_app_settings,
    execute_app_main_command as application_execute_app_main_command,
)
from research_system.cli.application.app_parser_support import (
    build_app_parser as application_build_app_parser,
)


class ApplicationRuntimeTests(unittest.TestCase):
    def test_application_entry_and_parser_exports_remain_available(self) -> None:
        self.assertIsNotNone(build_parser)
        self.assertIsNotNone(main)
        self.assertIsNotNone(application_build_app_settings)
        self.assertIsNotNone(application_execute_app_main_command)
        self.assertIsNotNone(application_build_app_parser)


if __name__ == "__main__":
    unittest.main()
