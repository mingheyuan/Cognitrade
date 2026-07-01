from __future__ import annotations

import unittest

from research_system.agents.collector.service import CollectorService


class _StrategySkillRuntimeStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, skill_name: str, **kwargs):
        self.calls.append((skill_name, kwargs))
        if skill_name == "sector_snapshot_skill":
            return {
                "snapshot_type": "sector",
                "sector": "innovation_drug",
                "theme": "innovation_drug",
                "query": kwargs.get("query"),
                "snapshot_date": "2026-07-01",
                "facts": {"top_constituents": []},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "policy_snapshot_skill":
            return {
                "snapshot_type": "policy",
                "sector": kwargs.get("sector"),
                "query": kwargs.get("query"),
                "snapshot_date": "2026-07-01",
                "facts": {"policy_count": 1},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        return {
            "snapshot_type": skill_name.removesuffix("_snapshot_skill").replace("_snapshot", ""),
            "symbol": kwargs.get("symbol"),
            "query": kwargs.get("query"),
            "snapshot_date": "2026-07-01",
            "facts": {},
            "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
        }


class CollectorStrategyTests(unittest.TestCase):
    def test_sector_research_policy_hint_enables_policy_snapshot_collection(self) -> None:
        runtime = _StrategySkillRuntimeStub()
        collector = CollectorService(runtime)

        payload = collector.run(
            intent="sector_research",
            query="continue the policy catalyst angle",
            constraints={
                "planning_context_hints": {
                    "recommended_next_step": "check policy catalyst and regulatory signals",
                    "thread_topic": "innovation drug policy research",
                }
            },
        )

        strategy = payload["sources"][0]["collection_strategy"]
        self.assertTrue(strategy["include_policy_snapshot"])
        self.assertEqual(strategy["preferred_methodology"], None)
        self.assertEqual([name for name, _ in runtime.calls], ["sector_snapshot_skill", "policy_snapshot_skill"])
        self.assertEqual(len(payload["snapshots"]), 2)

    def test_stock_check_methodology_memory_hint_sets_collection_methodology_focus(self) -> None:
        runtime = _StrategySkillRuntimeStub()
        collector = CollectorService(runtime)

        payload = collector.run(
            intent="stock_check",
            query="continue the previous work",
            constraints={
                "symbol": "000001",
                "snapshot_types": ["market"],
                "planning_context_hints": {
                    "methodology_benchmark_scope": "defensive_compounder",
                },
            },
        )

        strategy = payload["sources"][0]["collection_strategy"]
        self.assertEqual(strategy["preferred_methodology"], "cashflow_resilience")
        self.assertIn("focus_source=methodology_memory", strategy["notes"])


if __name__ == "__main__":
    unittest.main()
