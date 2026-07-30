from __future__ import annotations

import unittest

from research_system.cli.application.app_entrypoint import build_parser, main


class AppEntrypointRuntimeTests(unittest.TestCase):
    def test_application_entry_exports_remain_available(self) -> None:
        self.assertIsNotNone(build_parser)
        self.assertIsNotNone(main)


if __name__ == "__main__":
    unittest.main()
