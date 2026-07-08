from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from research_system.agents.collector.service import CollectorService
from research_system.runtime.history.session.session_history import SessionHistory
from research_system.runtime.orchestration.subagent_discovery_admin_support import (
    OrchestratorSubagentDiscoveryAdminSupport,
)
from research_system.runtime.orchestration.subagent_discovery_store import SubagentDiscoveryStore
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentCatalogEntry,
    SubagentToolDiscoveryRequest,
)


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


class _CollectorHybridProviderStub:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **kwargs):
        schema = kwargs["schema"]
        self.calls += 1
        return schema.model_validate(
            {
                "selected_plan_variant": "baseline_snapshot_bundle",
                "selected_node_ids": [
                    "query_normalizer",
                    "collection_strategy_resolver",
                    "stock_snapshot_collector",
                ],
                "request_replan": True,
                "reason": "prefer baseline snapshots for this run",
                "confidence": 0.93,
                "notes": ["hybrid_select_baseline_snapshot_bundle"],
            }
        )


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

    def test_stock_check_strategy_controlled_collect_emits_replanned_catalog_plan(self) -> None:
        runtime = _StrategySkillRuntimeStub()
        collector = CollectorService(runtime)

        payload = collector.run(
            intent="stock_check",
            query="continue the previous conclusion",
            constraints={
                "symbol": "000001",
                "planning_context_hints": {
                    "recommended_next_step": "compare cashflow resilience and short-term sentiment",
                    "thread_topic": "analyze 000001 cashflow resilience",
                },
            },
        )

        subagent_plans = list(payload.get("subagent_plans") or [])
        self.assertEqual(len(subagent_plans), 1)
        node_ids = [str(node.get("node_id") or "") for node in list(subagent_plans[0].get("nodes") or [])]
        self.assertEqual(
            node_ids,
            ["query_normalizer", "collection_strategy_resolver", "stock_snapshot_collector"],
        )
        snapshot_node = next(
            node for node in list(subagent_plans[0].get("nodes") or []) if str(node.get("node_id") or "") == "stock_snapshot_collector"
        )
        self.assertEqual(
            list(snapshot_node.get("inputs", {}).get("snapshot_types") or []),
            ["market", "fundamental", "notice", "capital_flow", "sentiment"],
        )
        patch_op_types = [
            str(operation.get("op_type") or "")
            for patch in list(payload.get("subagent_plan_patches") or [])
            for operation in list((patch or {}).get("operations") or [])
        ]
        self.assertEqual(patch_op_types, ["request_replan"])

    def test_stock_check_hybrid_can_switch_to_baseline_snapshot_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.subagent_phase_shadow_llm_enabled = True
            settings.subagent_phase_hybrid_llm_enabled = True
            settings.subagent_phase_hybrid_llm_min_confidence = 0.8
            settings.max_llm_calls_per_turn = 3
            provider = _CollectorHybridProviderStub()
            runtime = _StrategySkillRuntimeStub()
            collector = CollectorService(
                runtime,
                settings.runs_root,
                llm_provider=provider,
                settings=settings,
            )

            payload = collector.run(
                intent="stock_check",
                query="continue the previous conclusion",
                constraints={
                    "symbol": "000001",
                    "planning_context_hints": {
                        "recommended_next_step": "compare cashflow resilience and short-term sentiment",
                        "thread_topic": "analyze 000001 cashflow resilience",
                    },
                },
            )
            run_root = next(settings.runs_root.iterdir())
            trace_payload = SessionHistory(run_root).trace()

        self.assertEqual(provider.calls, 1)
        self.assertEqual(
            sorted(name for name, _ in runtime.calls),
            [
                "fundamental_snapshot_skill",
                "market_snapshot_skill",
                "notice_snapshot_skill",
            ],
        )
        subagent_plans = list(payload.get("subagent_plans") or [])
        snapshot_node = next(
            node for node in list(subagent_plans[0].get("nodes") or []) if str(node.get("node_id") or "") == "stock_snapshot_collector"
        )
        self.assertEqual(
            list(snapshot_node.get("inputs", {}).get("snapshot_types") or []),
            ["market", "fundamental", "notice"],
        )
        hybrid_rows = [
            row
            for row in list(trace_payload.get("dynamic_trace") or [])
            if str(row.get("event") or "") == "subagent_phase_hybrid_applied"
        ]
        self.assertEqual(len(hybrid_rows), 1)
        self.assertEqual(hybrid_rows[0]["dag_id"], "collect_stock_check")
        self.assertEqual(hybrid_rows[0]["applied_plan_variant"], "baseline_snapshot_bundle")

    def test_collector_registry_can_materialize_runtime_registered_snapshot_skill_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_signal_extension",
                reason="missing market signal extension capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                search_query="stock_check collect market_signal_extension",
                reason=gap.reason,
            )
            ticket = store.create_request(gap=gap, request=request)
            host = type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"market_snapshot_skill": object()})
                    ),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(
                ticket.ticket_id,
                decision="approve",
                candidate_catalog_entries=[
                    SubagentCatalogEntry(
                        subagent_type="candidate_collect_market_signal_extension",
                        summary="Runtime-registered market signal extension.",
                        capabilities=["market_signal_extension"],
                        stage="collect",
                        suitable_intents=["stock_check"],
                        tags=["approved_discovery_candidate"],
                    ).model_dump(mode="json")
                ],
            )
            admin.activate_request(
                ticket.ticket_id,
                registration_type="snapshot_skill_runner",
                skill_name="market_snapshot_skill",
                snapshot_type="market",
                activated_by="tester",
                reason="activate runtime snapshot skill",
            )

            runtime = _StrategySkillRuntimeStub()
            collector = CollectorService(runtime)
            collector._runtime_binding = {"subagent_discovery_store": store}

            entries = collector.registry.catalog_entries(stage="collect", capability="market_signal_extension")
            self.assertEqual([entry.subagent_type for entry in entries], ["candidate_collect_market_signal_extension"])

            definition = collector.registry.definition("candidate_collect_market_signal_extension")
            self.assertEqual(definition.input_model.__name__, "ActivatedSnapshotSkillInputModel")
            outputs = collector.registry.get("candidate_collect_market_signal_extension").run(
                query="check extension",
                symbol="000001",
            )

            self.assertEqual(outputs["snapshot_type"], "market")
            self.assertEqual(runtime.calls, [("market_snapshot_skill", {"query": "check extension", "symbol": "000001"})])


if __name__ == "__main__":
    unittest.main()
