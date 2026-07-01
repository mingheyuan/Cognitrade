from __future__ import annotations

from typing import Any

from research_system.adapters.vendors.data_source_registry import DataSourceRegistry
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.runtime.settings import Settings
from research_system.skills.capital_flow_snapshot.skill import CapitalFlowSnapshotSkill
from research_system.skills.events_snapshot.skill import EventsSnapshotSkill
from research_system.skills.fundamental_snapshot.skill import FundamentalSnapshotSkill
from research_system.skills.lhb_snapshot.skill import LHBSnapshotSkill
from research_system.skills.market_snapshot.skill import MarketSnapshotSkill
from research_system.skills.notice_snapshot.skill import NoticeSnapshotSkill
from research_system.skills.policy_snapshot.skill import PolicySnapshotSkill
from research_system.skills.sector_snapshot.skill import SectorSnapshotSkill
from research_system.skills.sentiment_snapshot.skill import SentimentSnapshotSkill


class LocalSkillBackend:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.data_sources = DataSourceRegistry()
        self.provider_registry = VendorProviderRegistry(self.settings, self.data_sources)
        self.market_router = MarketRouter()
        self.registry = {
            "market_snapshot_skill": MarketSnapshotSkill(self.provider_registry, self.market_router),
            "fundamental_snapshot_skill": FundamentalSnapshotSkill(self.provider_registry, self.market_router),
            "notice_snapshot_skill": NoticeSnapshotSkill(self.provider_registry, self.market_router),
            "sector_snapshot_skill": SectorSnapshotSkill(self.provider_registry),
            "lhb_snapshot_skill": LHBSnapshotSkill(self.provider_registry, self.market_router),
            "capital_flow_snapshot_skill": CapitalFlowSnapshotSkill(self.provider_registry, self.market_router),
            "sentiment_snapshot_skill": SentimentSnapshotSkill(self.provider_registry, self.market_router),
            "events_snapshot_skill": EventsSnapshotSkill(self.provider_registry, self.market_router),
            "policy_snapshot_skill": PolicySnapshotSkill(self.provider_registry),
        }

    def run(self, skill_name: str, **kwargs: Any) -> dict[str, Any]:
        skill = self.registry[skill_name]
        return skill.run(**kwargs)


class SkillRuntime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.backend = LocalSkillBackend(settings)

    def run(self, skill_name: str, **kwargs: Any) -> dict[str, Any]:
        return self.backend.run(skill_name, **kwargs)
