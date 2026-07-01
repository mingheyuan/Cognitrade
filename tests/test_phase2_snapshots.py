from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.agents.collector.service import CollectorService
from research_system.agents.screener.service import ScreenerService
from research_system.runtime.provider_health import ProviderHealthService
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.runtime.skill_runtime import SkillRuntime
from research_system.skills.capital_flow_snapshot.skill import CapitalFlowSnapshotSkill
from research_system.skills.events_snapshot.skill import EventsSnapshotSkill
from research_system.skills.lhb_snapshot.skill import LHBSnapshotSkill
from research_system.skills.sentiment_snapshot.skill import SentimentSnapshotSkill


class _Phase2SkillRuntimeStub:
    def run(self, skill_name: str, **kwargs):
        symbol = kwargs.get("symbol")
        if skill_name == "market_snapshot_skill":
            return {
                "snapshot_type": "market",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {"market_overview": {"spot": {"鏈€鏂颁环": 10.1, "娑ㄨ穼骞?": 3.1, "鎴愪氦棰?": 2200000000}}},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "fundamental_snapshot_skill":
            return {
                "snapshot_type": "fundamental",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {"financial_abstract": {"营收": 100}},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "notice_snapshot_skill":
            return {
                "snapshot_type": "notice",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {"notice_count": 4},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "lhb_snapshot_skill":
            return {
                "snapshot_type": "lhb",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {"buy_top_total": 10000000, "sell_top_total": 5000000},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "capital_flow_snapshot_skill":
            return {
                "snapshot_type": "capital_flow",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {"latest_flow": {"主力净流入": 3.2}, "main_flow_rank": {"排名": 12}},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "sentiment_snapshot_skill":
            return {
                "snapshot_type": "sentiment",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {"hot_rank_latest": [{"rank": 5}], "hot_keywords": [{"keyword": "AI"}]},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "events_snapshot_skill":
            return {
                "snapshot_type": "events",
                "symbol": symbol,
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {"event_keywords": ["业绩", "合作"], "event_notices": [{"title": "合作公告"}]},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "sector_snapshot_skill":
            return {
                "snapshot_type": "sector",
                "sector": "创新药",
                "theme": "concept",
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {"top_constituents": [{"code": "300363", "name": "博腾股份"}]},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        if skill_name == "policy_snapshot_skill":
            return {
                "snapshot_type": "policy",
                "sector": kwargs.get("sector"),
                "theme": kwargs.get("sector"),
                "query": kwargs.get("query"),
                "snapshot_date": "2026-06-30",
                "facts": {"policy_signals": [{"title": "创新药政策窗口"}], "policy_digest": {"top_policy_titles": ["创新药政策窗口"]}},
                "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
            }
        raise AssertionError(f"unexpected skill: {skill_name}")


class Phase2CollectorTests(unittest.TestCase):
    def test_stock_check_collects_phase2_snapshots(self) -> None:
        collector = CollectorService(_Phase2SkillRuntimeStub())
        payload = collector.run(
            intent="stock_check",
            query="看看 000001",
            constraints={"snapshot_types": ["market", "fundamental", "notice", "lhb", "capital_flow", "sentiment", "events"]},
        )
        snapshot_types = [item["snapshot_type"] for item in payload["snapshots"]]
        self.assertEqual(snapshot_types, ["market", "fundamental", "notice", "lhb", "capital_flow", "sentiment", "events"])

    def test_sector_research_collects_policy_related_snapshot(self) -> None:
        collector = CollectorService(_Phase2SkillRuntimeStub())
        payload = collector.run(intent="sector_research", query="创新药", constraints={"include_policy_snapshot": True})
        snapshot_types = [item["snapshot_type"] for item in payload["snapshots"]]
        self.assertEqual(snapshot_types, ["sector", "policy"])


class Phase2ScreenerTests(unittest.TestCase):
    def test_screener_uses_phase2_scores(self) -> None:
        screener = ScreenerService()
        payload = screener.run(
            query="最近有什么股票值得投入",
            source_payloads=[{"candidate_universe": [{"symbol": "000001", "name": "平安银行"}], "investment_cycle": "mid_term"}],
            snapshot_payloads=_Phase2SkillRuntimeStubCollector().snapshots(),
            constraints={"top_k": 1, "min_score": 0.0},
        )
        candidate = payload["screened_candidates"][0]
        self.assertGreater(candidate["score_breakdown"]["capital_flow"], 0)
        self.assertGreater(candidate["score_breakdown"]["sentiment"], 0)
        self.assertGreater(candidate["score_breakdown"]["lhb"], 0)
        self.assertGreater(candidate["score_breakdown"]["events"], 0)


class _Phase2SkillRuntimeStubCollector:
    def snapshots(self) -> list[dict]:
        runtime = _Phase2SkillRuntimeStub()
        return [
            runtime.run("market_snapshot_skill", symbol="000001", query="000001"),
            runtime.run("fundamental_snapshot_skill", symbol="000001", query="000001"),
            runtime.run("notice_snapshot_skill", symbol="000001", query="000001"),
            runtime.run("lhb_snapshot_skill", symbol="000001", query="000001"),
            runtime.run("capital_flow_snapshot_skill", symbol="000001", query="000001"),
            runtime.run("sentiment_snapshot_skill", symbol="000001", query="000001"),
            runtime.run("events_snapshot_skill", symbol="000001", query="000001"),
        ]


class Phase2ProviderHealthTests(unittest.TestCase):
    def test_provider_health_reports_phase2_dims(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            with patch("research_system.runtime.provider_health.NetworkProfile.probe_defaults", return_value={"domestic_reachable": True, "overseas_reachable": True, "domestic": [], "overseas": []}):
                payload = ProviderHealthService(settings).build_report()
        dims = {item["dim"] for item in payload["provider_rows"]}
        self.assertTrue({"lhb", "capital_flow", "sentiment", "events", "policy"}.issubset(dims))


class Phase2SkillShapeTests(unittest.TestCase):
    def test_phase2_skills_compile_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            runtime = SkillRuntime(settings)
            self.assertIn("lhb_snapshot_skill", runtime.backend.registry)
            self.assertIn("capital_flow_snapshot_skill", runtime.backend.registry)
            self.assertIn("sentiment_snapshot_skill", runtime.backend.registry)
            self.assertIn("events_snapshot_skill", runtime.backend.registry)
            self.assertIn("policy_snapshot_skill", runtime.backend.registry)


if __name__ == "__main__":
    unittest.main()
