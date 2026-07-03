from __future__ import annotations

import unittest

from research_system.agents import screen_reviewer as screen_reviewer_package
from research_system.agents.screen_reviewer.service import ScreenReviewerService


class ScreenReviewerPackageRuntimeTests(unittest.TestCase):
    def test_screen_reviewer_package_exports_service(self) -> None:
        self.assertIs(screen_reviewer_package.ScreenReviewerService, ScreenReviewerService)


if __name__ == "__main__":
    unittest.main()
