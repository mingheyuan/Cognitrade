from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from domain_packs.finance_ashare.subagents import task_intake
from domain_packs.finance_ashare.tools import snapshot_collector
from domain_packs.finance_ashare.tools.security_master import AShareSecurityMaster
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.platform.settings import Settings


class _SnapshotSkill:
    def __init__(self, snapshot_type: str) -> None:
        self.snapshot_type = snapshot_type

    def run(self, *, query: str, symbol: str) -> dict:
        return {
            "snapshot_type": self.snapshot_type,
            "symbol": symbol,
            "facts": {"availability": "available"},
            "source": {"provider_id": f"test_{self.snapshot_type}"},
        }


class _SecurityMaster:
    def candidate_universe(self, *, limit: int, supplied_symbols=()):
        rows = [
            {"symbol": "600001", "name": "Alpha", "amount": 3_000_000_000},
            {"symbol": "600002", "name": "Beta", "amount": 2_000_000_000},
            {"symbol": "600003", "name": "Gamma", "amount": 1_000_000_000},
        ]
        return rows[:limit]


class FinancePackMigrationCompletionTests(unittest.TestCase):
    def test_normal_finance_collector_uses_pack_owned_skill_implementations(self) -> None:
        skill_types = [
            *snapshot_collector._SYMBOL_SKILLS.values(),
            snapshot_collector.SectorSnapshotSkill,
            snapshot_collector.PolicySnapshotSkill,
        ]
        self.assertTrue(skill_types)
        for skill_type in skill_types:
            self.assertTrue(
                skill_type.__module__.startswith("domain_packs.finance_ashare.skills"),
                skill_type,
            )
            self.assertNotIn("research_system.skills", skill_type.__module__)

    def test_security_master_resolves_provider_backed_company_names(self) -> None:
        master = AShareSecurityMaster(
            universe_loader=lambda: [
                {"代码": "603019", "名称": "中科曙光"},
                {"代码": "000977", "名称": "浪潮信息"},
            ]
        )

        with patch.object(task_intake, "_SECURITY_MASTER", master):
            decision = task_intake.resolve_request(
                {"user_query": "比较中科曙光和浪潮信息的基本面与估值"}
            )

        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(decision["intent"], "stock_compare")
        self.assertEqual(decision["slots"]["symbols"], ["603019", "000977"])
        self.assertEqual(decision["trace"]["security_resolution_source"], "provider_security_master")

    def test_stock_screen_builds_candidate_pool_and_deep_enrichment(self) -> None:
        fake_skills = {
            snapshot_type: (lambda current=snapshot_type: _SnapshotSkill(current))
            for snapshot_type in snapshot_collector._SYMBOL_SKILLS
        }
        with patch.dict(snapshot_collector._SYMBOL_SKILLS, fake_skills, clear=True):
            result = snapshot_collector.collect_snapshot_bundle(
                query="筛选流动性较好且证据完整的候选",
                symbols=[],
                snapshot_types=["market", "fundamental", "notice"],
                intent="stock_screen",
                candidate_pool_size=3,
                enrichment_top_n=1,
                security_master=_SecurityMaster(),
            )

        self.assertEqual(result["symbols"], ["600001", "600002", "600003"])
        self.assertEqual(result["collection"]["universe_source"], "ashare_liquidity_ranked")
        self.assertEqual(result["collection"]["enrichment_targets"], ["600001"])
        collected = {
            (row["symbol"], row["snapshot_type"])
            for row in result["snapshots"]
            if row.get("symbol")
        }
        self.assertTrue({("600001", item) for item in snapshot_collector._SCREEN_ENRICHMENT_TYPES}.issubset(collected))
        self.assertEqual(len([row for row in result["snapshots"] if row["snapshot_type"] == "market"]), 3)

    def test_stock_research_collects_declared_symbol_snapshots(self) -> None:
        fake_skills = {
            snapshot_type: (lambda current=snapshot_type: _SnapshotSkill(current))
            for snapshot_type in snapshot_collector._SYMBOL_SKILLS
        }
        with patch.dict(snapshot_collector._SYMBOL_SKILLS, fake_skills, clear=True):
            result = snapshot_collector.collect_snapshot_bundle(
                query="Analyze 600750.",
                symbols=["600750"],
                snapshot_types=["market", "fundamental", "notice"],
                intent="stock_check",
            )

        self.assertEqual(
            {(row["symbol"], row["snapshot_type"]) for row in result["snapshots"]},
            {
                ("600750", "market"),
                ("600750", "fundamental"),
                ("600750", "notice"),
            },
        )
        self.assertEqual(result["collection"]["quality_status"], "available")

    def test_sector_collection_emits_real_sector_and_policy_snapshots(self) -> None:
        class SectorSkill:
            def run(self, *, query: str) -> dict:
                return {
                    "snapshot_type": "sector",
                    "sector": "机器人",
                    "facts": {
                        "top_constituents": [
                            {"code": "300001", "name": "机器人甲", "pct_change": 2.5}
                        ]
                    },
                }

        class PolicySkill:
            def run(self, *, query: str, sector: str, constituents: list[dict]) -> dict:
                return {
                    "snapshot_type": "policy",
                    "sector": sector,
                    "facts": {"policy_signals": [{"title": "产业支持"}]},
                }

        with (
            patch.object(snapshot_collector, "SectorSnapshotSkill", SectorSkill),
            patch.object(snapshot_collector, "PolicySnapshotSkill", PolicySkill),
        ):
            result = snapshot_collector.collect_snapshot_bundle(
                query="研究机器人板块",
                symbols=[],
                snapshot_types=["sector", "policy"],
                intent="sector_research",
                sector="机器人",
            )

        self.assertEqual(
            [row["snapshot_type"] for row in result["snapshots"]],
            ["sector", "policy"],
        )
        self.assertEqual(result["collection"]["universe_source"], "sector_constituents")
        self.assertEqual(result["candidate_universe"][0]["symbol"], "300001")

    def test_api_previous_run_review_reads_target_artifact_board(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        snapshots = [
            {
                "snapshot_type": "market",
                "symbol": "600000",
                "facts": {"market_overview": {"latest_price": 10.0, "amount": 2_000_000_000}},
            },
            {
                "snapshot_type": "fundamental",
                "symbol": "600000",
                "facts": {"financial_abstract": {"net_profit": 100}},
            },
            {
                "snapshot_type": "notice",
                "symbol": "600000",
                "facts": {"notice_count": 2},
            },
        ]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            with TestClient(
                create_app(settings=settings, api_config=ApiConfig(api_token="migration-completion"))
            ) as client:
                headers = {"Authorization": "Bearer migration-completion"}
                prior = client.post(
                    "/api/v1/runs",
                    headers=headers,
                    json={
                        "intent": "stock_check",
                        "user_query": "分析 600000",
                        "slots": {"snapshots": snapshots},
                    },
                ).json()
                review_response = client.post(
                    "/api/v1/runs",
                    headers=headers,
                    json={
                        "intent": "review_thesis",
                        "user_query": "复核上一轮研究判断",
                        "slots": {"snapshots": snapshots},
                    },
                )
                replay = client.get(
                    f"/api/v1/runs/{review_response.json()['run_id']}/replay",
                    headers=headers,
                ).json()

        self.assertEqual(prior["status"], "completed")
        self.assertEqual(review_response.status_code, 200, review_response.text)
        self.assertEqual(review_response.json()["status"], "completed")
        review_artifact = next(
            artifact for artifact in replay["artifacts"] if artifact["artifact_type"] == "thesis_review"
        )
        self.assertEqual(review_artifact["content"]["review_target_run_id"], prior["run_id"])
        self.assertTrue(review_artifact["content"]["review_target_found"])
        self.assertGreater(review_artifact["content"]["evidence_comparison"]["fresh_evidence_count"], 0)

    def test_specialized_intents_deliver_pack_artifacts_through_agent_os_api(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        snapshots = [
            {
                "snapshot_type": "market",
                "symbol": "600000",
                "facts": {"market_overview": {"latest_price": 10.0, "amount": 2_000_000_000}},
            },
            {
                "snapshot_type": "fundamental",
                "symbol": "600000",
                "facts": {
                    "financial_abstract": {
                        "revenue": 1000,
                        "net_profit": 120,
                        "operating_cash_flow": 160,
                    }
                },
            },
            {
                "snapshot_type": "notice",
                "symbol": "600000",
                "facts": {"notice_count": 2},
            },
        ]
        sector_snapshots = [
            {
                "snapshot_type": "sector",
                "sector": "机器人",
                "facts": {
                    "top_constituents": [
                        {"code": "600001", "name": "Alpha", "pct_change": 2.5}
                    ]
                },
            },
            {
                "snapshot_type": "policy",
                "sector": "机器人",
                "facts": {"policy_signals": [{"title": "产业支持"}]},
            },
        ]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            with TestClient(
                create_app(settings=settings, api_config=ApiConfig(api_token="specialized-intents"))
            ) as client:
                headers = {"Authorization": "Bearer specialized-intents"}

                screen = client.post(
                    "/api/v1/runs",
                    headers=headers,
                    json={
                        "intent": "stock_screen",
                        "user_query": "筛选 600000 的现金流和估值",
                        "slots": {"symbols": ["600000"], "snapshots": snapshots},
                    },
                )
                self.assertEqual(screen.status_code, 200, screen.text)

                sector = client.post(
                    "/api/v1/runs",
                    headers=headers,
                    json={
                        "intent": "sector_research",
                        "user_query": "研究机器人板块",
                        "slots": {"sector": "机器人", "snapshots": sector_snapshots},
                    },
                )
                self.assertEqual(sector.status_code, 200, sector.text)

                prior = client.post(
                    "/api/v1/runs",
                    headers=headers,
                    json={
                        "intent": "stock_check",
                        "user_query": "分析 600000",
                        "slots": {"snapshots": snapshots},
                    },
                )
                self.assertEqual(prior.status_code, 200, prior.text)
                review = client.post(
                    "/api/v1/runs",
                    headers=headers,
                    json={
                        "intent": "review_thesis",
                        "user_query": "复核上一轮研究判断",
                        "slots": {
                            "review_target_run_id": prior.json()["run_id"],
                            "snapshots": snapshots,
                        },
                    },
                )
                self.assertEqual(review.status_code, 200, review.text)

                expected = {
                    screen.json()["run_id"]: "screening_result",
                    sector.json()["run_id"]: "sector_analysis",
                    review.json()["run_id"]: "thesis_review",
                }
                for run_id, specialist_artifact_type in expected.items():
                    replay = client.get(f"/api/v1/runs/{run_id}/replay", headers=headers)
                    self.assertEqual(replay.status_code, 200, replay.text)
                    payload = replay.json()
                    self.assertEqual(payload["snapshot"]["status"], "completed")
                    artifact_types = {row["artifact_type"] for row in payload["artifacts"]}
                    self.assertTrue(
                        {
                            specialist_artifact_type,
                            "research_report",
                            "main_agent_synthesis",
                            "assistant_message",
                        }.issubset(artifact_types),
                            (run_id, artifact_types),
                        )

                    specialist_gate = {
                        "screening_result": "finance.screening.quality",
                        "sector_analysis": "finance.sector.analysis",
                        "thesis_review": "finance.thesis.review",
                    }[specialist_artifact_type]
                    gate_rows = {
                        row["gate_id"]: row
                        for row in payload["snapshot"].get("gate_evaluations", [])
                    }
                    self.assertEqual(gate_rows[specialist_gate]["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()
