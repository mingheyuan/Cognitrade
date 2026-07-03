from __future__ import annotations

import unittest

from research_system.agents import collector as collector_package
from research_system.agents import critic as critic_package
from research_system.agents import distiller as distiller_package
from research_system.agents import reporter as reporter_package
from research_system.agents import retriever as retriever_package
from research_system.agents import reviewer as reviewer_package
from research_system.agents import screen_reviewer as screen_reviewer_package
from research_system.agents import screener as screener_package
from research_system.agents.collector.service import CollectorService
from research_system.agents.critic.service import CriticService
from research_system.agents.distiller.service import DistillerService
from research_system.agents.reporter.service import ReporterService
from research_system.agents.retriever.service import RetrieverService
from research_system.agents.reviewer.service import ReviewerService
from research_system.agents.screen_reviewer.service import ScreenReviewerService
from research_system.agents.screener.service import ScreenerService


class AgentPackagesRuntimeTests(unittest.TestCase):
    def test_agent_package_exports(self) -> None:
        self.assertIs(collector_package.CollectorService, CollectorService)
        self.assertIs(critic_package.CriticService, CriticService)
        self.assertIs(distiller_package.DistillerService, DistillerService)
        self.assertIs(reporter_package.ReporterService, ReporterService)
        self.assertIs(retriever_package.RetrieverService, RetrieverService)
        self.assertIs(reviewer_package.ReviewerService, ReviewerService)
        self.assertIs(screen_reviewer_package.ScreenReviewerService, ScreenReviewerService)
        self.assertIs(screener_package.ScreenerService, ScreenerService)


if __name__ == "__main__":
    unittest.main()
