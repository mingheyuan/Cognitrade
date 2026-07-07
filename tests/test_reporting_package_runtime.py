from __future__ import annotations

import unittest

from research_system.services.reporting.reporting_utils import (
    strategy_follow_up,
    summarize_proposal_drafts,
)


class ReportingPackageRuntimeTests(unittest.TestCase):
    def test_reporting_utils_exports_are_available(self) -> None:
        self.assertTrue(callable(strategy_follow_up))
        self.assertTrue(callable(summarize_proposal_drafts))


if __name__ == "__main__":
    unittest.main()
