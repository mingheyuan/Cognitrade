from __future__ import annotations

import unittest

from research_system.skills import (
    capital_flow_snapshot,
    events_snapshot,
    fundamental_snapshot,
    lhb_snapshot,
    market_snapshot,
    notice_snapshot,
    policy_snapshot,
    sector_snapshot,
    sentiment_snapshot,
)
from research_system.skills.capital_flow_snapshot.skill import CapitalFlowSnapshotSkill
from research_system.skills.events_snapshot.skill import EventsSnapshotSkill
from research_system.skills.fundamental_snapshot.skill import FundamentalSnapshotSkill
from research_system.skills.lhb_snapshot.skill import LHBSnapshotSkill
from research_system.skills.market_snapshot.skill import MarketSnapshotSkill
from research_system.skills.notice_snapshot.skill import NoticeSnapshotSkill
from research_system.skills.policy_snapshot.skill import PolicySnapshotSkill
from research_system.skills.sector_snapshot.skill import SectorSnapshotSkill
from research_system.skills.sentiment_snapshot.skill import SentimentSnapshotSkill


class SnapshotSkillPackagesRuntimeTests(unittest.TestCase):
    def test_skills_root_package_exports_snapshot_modules(self) -> None:
        import research_system.skills as skills_package

        self.assertIs(skills_package.capital_flow_snapshot, capital_flow_snapshot)
        self.assertIs(skills_package.events_snapshot, events_snapshot)
        self.assertIs(skills_package.fundamental_snapshot, fundamental_snapshot)
        self.assertIs(skills_package.lhb_snapshot, lhb_snapshot)
        self.assertIs(skills_package.market_snapshot, market_snapshot)
        self.assertIs(skills_package.notice_snapshot, notice_snapshot)
        self.assertIs(skills_package.policy_snapshot, policy_snapshot)
        self.assertIs(skills_package.sector_snapshot, sector_snapshot)
        self.assertIs(skills_package.sentiment_snapshot, sentiment_snapshot)

    def test_snapshot_skill_package_exports(self) -> None:
        self.assertIs(capital_flow_snapshot.CapitalFlowSnapshotSkill, CapitalFlowSnapshotSkill)
        self.assertIs(events_snapshot.EventsSnapshotSkill, EventsSnapshotSkill)
        self.assertIs(fundamental_snapshot.FundamentalSnapshotSkill, FundamentalSnapshotSkill)
        self.assertIs(lhb_snapshot.LHBSnapshotSkill, LHBSnapshotSkill)
        self.assertIs(market_snapshot.MarketSnapshotSkill, MarketSnapshotSkill)
        self.assertIs(notice_snapshot.NoticeSnapshotSkill, NoticeSnapshotSkill)
        self.assertIs(policy_snapshot.PolicySnapshotSkill, PolicySnapshotSkill)
        self.assertIs(sector_snapshot.SectorSnapshotSkill, SectorSnapshotSkill)
        self.assertIs(sentiment_snapshot.SentimentSnapshotSkill, SentimentSnapshotSkill)


if __name__ == "__main__":
    unittest.main()
