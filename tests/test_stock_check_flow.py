from __future__ import annotations

import unittest

from research_system.adapters.storage.qdrant_adapter import QdrantAdapter
from research_system.agents.collector.service import CollectorService
from research_system.schemas.runtime import BlackboardEntry


class FakeSkillRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, skill_name: str, **kwargs) -> dict:
        self.calls.append((skill_name, kwargs))
        snapshot_type = skill_name.removesuffix("_snapshot_skill").replace("_snapshot", "")
        return {
            "snapshot_type": snapshot_type,
            "symbol": kwargs.get("symbol"),
            "query": kwargs.get("query"),
            "snapshot_date": "2026-06-28",
            "facts": {},
            "source": {"skill": skill_name, "vendor": "stub", "from_cache": False},
        }


class StockCheckFlowTests(unittest.TestCase):
    def test_collector_runs_three_stock_check_skills_with_symbol(self) -> None:
        runtime = FakeSkillRuntime()
        collector = CollectorService(runtime)

        payload = collector.run(intent="stock_check", query="帮我看下 000001 这个股票")

        self.assertEqual(payload["sources"][0]["symbol"], "000001")
        self.assertEqual(
            [name for name, _ in runtime.calls],
            [
                "market_snapshot_skill",
                "fundamental_snapshot_skill",
                "notice_snapshot_skill",
            ],
        )
        self.assertTrue(all(kwargs["symbol"] == "000001" for _, kwargs in runtime.calls))

    def test_market_snapshot_payload_text_is_stable(self) -> None:
        adapter = object.__new__(QdrantAdapter)
        entry = BlackboardEntry(
            run_id="run-stock",
            thread_id="run-stock",
            request_id="req-stock",
            kind="verified_snapshot",
            agent="collector",
            phase="collect",
            payload={
                "snapshot_type": "market",
                "symbol": "000001",
                "query": "帮我看下 000001 这个股票",
                "snapshot_date": "2026-06-28",
                "facts": {
                    "market_hist": {
                        "latest_bar": {
                            "date": "2026-06-26",
                            "close": 12.34,
                            "pct_change": 1.23,
                        }
                    },
                    "market_overview": {
                        "latest_price": 12.34,
                        "turnover_rate": 3.21,
                    },
                },
            },
            source_refs=[{"type": "skill", "skill": "market_snapshot_skill", "run_id": "run-stock"}],
        )

        payload = adapter.to_payload(entry)

        self.assertEqual(payload["document_title"], "market_snapshot:000001")
        self.assertEqual(payload["run_id"], "run-stock")
        self.assertEqual(payload["source_type"], "market_snapshot")
        self.assertIn("market snapshot for 000001", payload["content"])
        self.assertIn("snapshot_date: 2026-06-28", payload["content"])
        self.assertIn("latest_bar:", payload["content"])
        self.assertIn("market_overview:", payload["content"])

    def test_skill_payload_shape_accepts_phase1_source_metadata(self) -> None:
        runtime = FakeSkillRuntime()
        collector = CollectorService(runtime)

        payload = collector.run(intent="stock_check", query="看看 000001")

        snapshot = payload["snapshots"][0]
        self.assertIn("source", snapshot)
        self.assertEqual(snapshot["source"]["vendor"], "stub")

    def test_collector_stock_check_uses_planning_hints_to_add_capital_flow_and_sentiment(self) -> None:
        runtime = FakeSkillRuntime()
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

        skill_names = [name for name, _ in runtime.calls]
        self.assertIn("capital_flow_snapshot_skill", skill_names)
        self.assertIn("sentiment_snapshot_skill", skill_names)
        self.assertEqual(payload["sources"][0]["collection_strategy"]["preferred_methodology"], "cashflow_resilience")
        self.assertIn("capital_flow", payload["sources"][0]["collection_strategy"]["hint_augmented_snapshot_types"])


if __name__ == "__main__":
    unittest.main()
