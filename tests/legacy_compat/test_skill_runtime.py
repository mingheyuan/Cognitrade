from __future__ import annotations

from pathlib import Path
import unittest

from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.runtime.platform.settings import Settings
from research_system.skills.market_snapshot.skill import MarketSnapshotSkill


class LegacySkillRuntimeTests(unittest.TestCase):
    def test_legacy_skill_import_is_a_pack_implementation_compatibility_export(self) -> None:
        self.assertEqual(MarketSnapshotSkill.skill_name, "market_snapshot_skill")
        self.assertTrue(
            MarketSnapshotSkill.__module__.startswith(
                "domain_packs.finance_ashare.skills"
            )
        )

    def test_legacy_skill_runtime_facade_still_executes_without_entering_agent_os(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        runtime = SkillRuntime(Settings.from_env(repository_root))

        result = runtime.run("market_snapshot_skill", query="legacy compatibility", symbol=None)

        self.assertEqual(result["snapshot_type"], "market")
        self.assertEqual(result["degrade_reason"], "symbol_missing")


if __name__ == "__main__":
    unittest.main()
