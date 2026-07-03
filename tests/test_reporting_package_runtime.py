from __future__ import annotations

import unittest

from research_system.services.reporting.reporting_utils import (
    strategy_follow_up as package_strategy_follow_up,
    summarize_proposal_drafts as package_summarize_proposal_drafts,
)
from research_system.services.reporting_utils import (
    strategy_follow_up,
    summarize_proposal_drafts,
)


class ReportingPackageRuntimeTests(unittest.TestCase):
    def test_top_level_reporting_utils_exports_match_package_exports(self) -> None:
        self.assertIs(strategy_follow_up, package_strategy_follow_up)
        self.assertIs(summarize_proposal_drafts, package_summarize_proposal_drafts)


if __name__ == "__main__":
    unittest.main()
