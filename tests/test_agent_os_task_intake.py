from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.intake.pack_intake import PackIntakeService
from research_system.runtime.intake.request_intake import RequestIntakeService
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.platform.settings import Settings
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.core_runtime import IntakeClassificationProposal


class _ModelIntakeProvider:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **_kwargs):
        self.calls += 1
        return IntakeClassificationProposal(
            request_kind="domain_task",
            domain_id="finance_ashare",
            intent="stock_screen",
            confidence=0.92,
        )


class AgentOSTaskIntakeTests(unittest.TestCase):
    def test_request_intake_uses_model_candidate_before_core_fallback(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        provider = _ModelIntakeProvider()
        service = RequestIntakeService(
            DomainPackRegistry.load(repository_root / "domain_packs"),
            llm_provider=provider,
        )

        decision = service.resolve(RequestEnvelope(
            user_query="find interesting companies",
            constraints={"research_profile": "lite", "investment_cycle": "short_term"},
        ))

        self.assertEqual(decision.status, "accepted")
        self.assertEqual(decision.request_kind, "domain_task")
        self.assertEqual(decision.domain_id, "finance_ashare")
        self.assertEqual(decision.intent, "stock_screen")
        self.assertEqual(decision.trace["source"], "model_classification")
        self.assertEqual(provider.calls, 1)

    def test_finance_pack_resolves_company_aliases_and_comparison_intent(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = PackIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))

        decision = service.resolve(
            RequestEnvelope(
                user_query="\u5bf9\u6bd4\u5e73\u5b89\u94f6\u884c\u548c\u62db\u5546\u94f6\u884c",
                constraints={"research_profile": "medium"},
            )
        )

        self.assertEqual(decision.status, "accepted")
        self.assertEqual(decision.intent, "stock_compare")
        self.assertEqual(decision.slots["symbols"], ["000001", "600036"])

    def test_single_stock_uzi_command_rejects_multiple_symbols(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = PackIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))

        decision = service.resolve(
            RequestEnvelope(
                intent="quick_scan",
                user_query="快速扫描 600519 和 000001",
                slots={"symbols": ["600519", "000001"]},
            )
        )

        self.assertEqual(decision.status, "needs_clarification")
        self.assertEqual(decision.intent, "quick_scan")
        self.assertEqual(
            decision.clarification.context["reason"],
            "uzi_single_symbol_command_requires_one_symbol",
        )
        self.assertEqual(decision.clarification.context["suggested_intent"], "stock_compare")

    def test_pack_intake_exposes_declared_capability_candidates(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = PackIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))

        decision = service.resolve(
            RequestEnvelope(
                user_query="筛选最近值得关注的股票",
                constraints={"research_profile": "deep", "investment_cycle": "long_term"},
            )
        )

        self.assertEqual(decision.status, "accepted")
        self.assertEqual(decision.intent, "stock_screen")
        self.assertEqual(
            decision.candidate_capabilities,
            [
                "finance.query.normalize",
                "finance.ashare.snapshot.collect",
                "finance.stock.screen",
                "finance.screen.report.compose",
            ],
        )
        self.assertEqual(decision.trace["intent_definition"], "stock_screen")

    def test_finance_pack_returns_clarification_for_incomplete_target(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = PackIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))

        decision = service.resolve(RequestEnvelope(user_query="compare one bank"))

        self.assertEqual(decision.status, "needs_clarification")
        self.assertEqual(decision.intent, "stock_compare")
        self.assertEqual(decision.clarification.missing_fields, ["symbols"])

    def test_finance_pack_requires_user_selected_research_profile(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = PackIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))

        decision = service.resolve(RequestEnvelope(user_query="分析贵州茅台"))

        self.assertEqual(decision.status, "needs_clarification")
        self.assertEqual(decision.intent, "stock_check")
        self.assertEqual(decision.clarification.missing_fields, ["research_profile"])
        self.assertEqual(
            decision.clarification.context["allowed_profiles"],
            ["lite", "medium", "deep"],
        )

    def test_finance_pack_requires_user_selected_stock_screen_cycle(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = PackIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))

        decision = service.resolve(
            RequestEnvelope(
                intent="stock_screen",
                user_query="筛选最近值得关注的股票",
                constraints={"research_profile": "lite"},
            )
        )

        self.assertEqual(decision.status, "needs_clarification")
        self.assertEqual(decision.intent, "stock_screen")
        self.assertEqual(decision.clarification.missing_fields, ["investment_cycle"])
        self.assertEqual(
            decision.clarification.context["allowed_cycles"],
            ["short_term", "mid_term", "long_term"],
        )

    def test_finance_pack_does_not_treat_unnamed_multiple_banks_as_sector_research(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = PackIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))

        decision = service.resolve(RequestEnvelope(user_query="看看两家银行"))

        self.assertEqual(decision.status, "needs_clarification")
        self.assertEqual(decision.intent, "stock_compare")
        self.assertEqual(decision.clarification.missing_fields, ["symbols"])
        self.assertIsNone(decision.clarification.question)
        self.assertEqual(
            decision.clarification.context["reason"],
            "comparison_targets_missing",
        )

    def test_portfolio_queries_route_only_to_native_uzi_method_intents(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = PackIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))
        holdings = [{"ticker": "600519", "weight": 100, "return_pct": 8.0, "asset_class": "A股"}]
        for query, expected_intent in {
            "对这个组合做收益归因": "uzi_returns",
            "按目标权重对这个组合做持仓再平衡": "uzi_rebalance",
            "按资产类别对组合做资产配置再平衡": "uzi_portfolio_rebalance_method",
        }.items():
            with self.subTest(query=query):
                decision = service.resolve(RequestEnvelope(
                    user_query=query,
                    constraints={"portfolio_holdings": holdings},
                ))
                self.assertEqual(decision.status, "accepted")
                self.assertEqual(decision.intent, expected_intent)

        batch = service.resolve(RequestEnvelope(
            user_query="分析一下这个组合",
            constraints={"portfolio_holdings": holdings, "research_profile": "lite"},
        ))
        self.assertEqual(batch.status, "accepted")
        self.assertEqual(batch.intent, "uzi_portfolio_batch")

    def test_main_agent_composes_pack_clarification_from_structured_context(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                    "openai_api_key": None,
                    "openai_base_url": None,
                }
            )
            agent_os = AgentOS(settings)
            agent_os.main_agent.llm_provider = None
            agent_os.request_intake.llm_provider = None

            response = agent_os.handle_request(RequestEnvelope(user_query="看看两家银行"))

            self.assertEqual(response.status, "needs_clarification")
            self.assertEqual(response.intent, "stock_compare")
            self.assertIn("银行", response.message)
            self.assertIn("代码", response.message)
            self.assertEqual(response.clarification.missing_fields, ["symbols"])
            request_state = json.loads(
                (settings.runs_root / str(response.run_id) / "agent_os_request.json")
                .read_text(encoding="utf-8")
            )
            self.assertIsNone(
                request_state["request_intake"]["clarification_context"]
                .get("question")
            )

    def test_versioned_api_uses_pack_intake_when_intent_is_omitted(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                    "openai_api_key": None,
                    "openai_base_url": None,
                }
            )
            with TestClient(
                create_app(settings=settings, api_config=ApiConfig(api_token="task-intake-test"))
            ) as client:
                response = client.post(
                    "/api/v1/runs",
                    headers={"Authorization": "Bearer task-intake-test"},
                    json={
                        "user_query": "\u5bf9\u6bd4\u5e73\u5b89\u94f6\u884c\u548c\u62db\u5546\u94f6\u884c",
                        "constraints": {"research_profile": "lite"},
                        "slots": {
                            "snapshots": [
                                {"snapshot_type": "market", "symbol": "000001", "facts": {"market_overview": {"latest_price": 10}}},
                                {"snapshot_type": "fundamental", "symbol": "000001", "facts": {"financial_abstract": {"net_profit": 1}}},
                                {"snapshot_type": "market", "symbol": "600036", "facts": {"market_overview": {"latest_price": 20}}},
                                {"snapshot_type": "fundamental", "symbol": "600036", "facts": {"financial_abstract": {"net_profit": 2}}},
                            ]
                        },
                    },
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["intent"], "stock_compare")
        self.assertEqual(response.json()["status"], "gated")

    def test_versioned_api_persists_a_resumable_clarification_run(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
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
                create_app(settings=settings, api_config=ApiConfig(api_token="task-intake-test"))
            ) as client:
                response = client.post(
                    "/api/v1/runs",
                    headers={"Authorization": "Bearer task-intake-test"},
                    json={"user_query": "analyze a company"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "needs_clarification")
                run_id = response.json()["run_id"]
                self.assertTrue(run_id)
                self.assertTrue((root / "runs" / run_id / "clarification_pending.json").is_file())

    def test_explicit_thesis_review_without_target_is_a_pack_clarification(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
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
                create_app(settings=settings, api_config=ApiConfig(api_token="task-intake-test"))
            ) as client:
                response = client.post(
                    "/api/v1/runs",
                    headers={"Authorization": "Bearer task-intake-test"},
                    json={
                        "intent": "review_thesis",
                        "user_query": "review the previous thesis",
                        "constraints": {"research_profile": "medium"},
                    },
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "needs_clarification")
        self.assertEqual(response.json()["clarification"]["missing_fields"], ["review_target_run_id"])


if __name__ == "__main__":
    unittest.main()
