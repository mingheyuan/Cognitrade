from __future__ import annotations

import unittest

from research_system.cli.app import build_parser, main
from research_system.cli.application.app_entrypoint import (
    build_parser as package_build_parser,
    main as package_main,
)


class AppEntrypointRuntimeTests(unittest.TestCase):
    def test_top_level_app_entry_exports_remain_available(self) -> None:
        self.assertIsNotNone(build_parser)
        self.assertIsNotNone(main)
        self.assertIsNotNone(package_build_parser)
        self.assertIsNotNone(package_main)


if __name__ == "__main__":
    unittest.main()
