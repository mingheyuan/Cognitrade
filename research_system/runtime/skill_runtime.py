from __future__ import annotations

from typing import Any

from research_system.skills.fundamental_snapshot.skill import FundamentalSnapshotSkill
from research_system.skills.market_snapshot.skill import MarketSnapshotSkill
from research_system.skills.notice_snapshot.skill import NoticeSnapshotSkill
from research_system.skills.sector_snapshot.skill import SectorSnapshotSkill


class LocalSkillBackend:
    def __init__(self) -> None:
        self.registry = {
            "market_snapshot_skill": MarketSnapshotSkill(),
            "fundamental_snapshot_skill": FundamentalSnapshotSkill(),
            "notice_snapshot_skill": NoticeSnapshotSkill(),
            "sector_snapshot_skill": SectorSnapshotSkill(),
        }

    def run(self, skill_name: str, **kwargs: Any) -> dict[str, Any]:
        skill = self.registry[skill_name]
        return skill.run(**kwargs)


class SkillRuntime:
    def __init__(self) -> None:
        self.backend = LocalSkillBackend()

    def run(self, skill_name: str, **kwargs: Any) -> dict[str, Any]:
        return self.backend.run(skill_name, **kwargs)
