from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.adapters.storage.local_memory_adapter import LocalMemoryAdapter
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.proposals import ProposalDraft
from research_system.services.memory import (
    CHAT_RESEARCH_MEMORY_TYPE,
    CHAT_RESEARCH_SNAPSHOT_VERSION,
    MEMORY_RECORD_SCHEMA_VERSION,
    METHODOLOGY_SNAPSHOT_VERSION,
    MemoryService,
)
from research_system.services.methodologies.policy.methodology_manifest_service import (
    MethodologyManifestService,
)
from research_system.services.memory.write.methodology_writer import MethodologyMemoryWriter


class MethodologyMemoryTests(unittest.TestCase):
    def test_approved_proposal_updates_manifest_and_writes_local_memory_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)

            proposal = ProposalDraft(
                proposal_type="MethodologyTemplatePatch",
                status="approved",
                title="Promote event-driven dimension",
                summary="Promote policy catalyst dimension into active methodology.",
                created_from_run_id="01RUN",
                author_agent="tester",
                sample_size=2,
                benchmark_scope="event_driven",
                evidence_summary="{}",
            )

            manifest_service = MethodologyManifestService(settings)
            memory_writer = MethodologyMemoryWriter(MemoryService(LocalMemoryAdapter(settings)))

            manifest = manifest_service.approve(proposal)
            record = memory_writer.write_from_proposal(proposal)

            self.assertIn(proposal.proposal_id, manifest["approved_proposal_ids"])
            self.assertEqual(record["memory_type"], "methodology_summary")
            self.assertEqual(record["metadata"]["proposal_id"], proposal.proposal_id)
            self.assertEqual(record["metadata"]["proposal_type"], "MethodologyTemplatePatch")
            self.assertEqual(record["metadata"]["benchmark_scope"], "event_driven")
            self.assertEqual(record["metadata"]["sample_size"], 2)
            export_text = (settings.memory_root / "records.jsonl").read_text(encoding="utf-8")
            self.assertIn(proposal.proposal_id, export_text)
            self.assertIn("Promote event-driven dimension", (settings.memory_root / "MEMORY.md").read_text(encoding="utf-8"))

    def test_chat_research_memory_can_be_written_and_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            service = MemoryService(LocalMemoryAdapter(settings))

            record = service.write_chat_research_memory(
                query="compare maotai and catl",
                intent="stock_compare",
                run_id="01CHAT",
                summary="comparison remains cautious",
                conclusion="current ranking is still inconclusive",
                follow_up="supplement same-basis cashflow and valuation assumptions",
                status="completed",
            )
            latest = service.load_latest_chat_research_memory()

            self.assertEqual(record["memory_type"], CHAT_RESEARCH_MEMORY_TYPE)
            self.assertIsNotNone(latest)
            self.assertEqual(latest["query"], "compare maotai and catl")
            self.assertEqual(latest["intent"], "stock_compare")
            self.assertEqual(latest["run_id"], "01CHAT")
            self.assertEqual(latest["summary"], "comparison remains cautious")
            self.assertEqual(latest["follow_up"], "supplement same-basis cashflow and valuation assumptions")
            self.assertIn("memory_id", record["content"])
            self.assertEqual(record["content"]["schema_version"], MEMORY_RECORD_SCHEMA_VERSION)
            self.assertEqual(record["content"]["snapshot_version"], CHAT_RESEARCH_SNAPSHOT_VERSION)
            self.assertEqual(record["metadata"]["memory_scope"], "chat_research")
            self.assertTrue(str(record["metadata"]["snapshot_id"]).startswith("chat_snapshot:"))
            self.assertEqual(record["metadata"]["entity_scope"], "chat_intent:stock_compare")

    def test_local_memory_adapter_refreshes_summary_and_registry_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            service = MemoryService(LocalMemoryAdapter(settings))

            service.write_chat_research_memory(
                query="analyze guizhou maotai",
                intent="stock_check",
                run_id="01CHAT",
                summary="chat summary",
                conclusion="chat conclusion",
                follow_up="chat next step",
                status="completed",
            )
            service.write_methodology_memory(
                memory_type="methodology_summary",
                content={"title": "Method note", "summary": "method summary"},
                metadata={"status": "approved"},
            )

            summary_text = (settings.memory_root / "memory_summary.md").read_text(encoding="utf-8")
            registry_text = (settings.memory_root / "MEMORY.md").read_text(encoding="utf-8")

            self.assertIn("Intent: stock_check", summary_text)
            self.assertIn("Query: analyze guizhou maotai", summary_text)
            self.assertIn("Conclusion: chat conclusion", summary_text)
            self.assertIn("chat_research_summary: analyze guizhou maotai", registry_text)
            self.assertIn("methodology_summary: Method note", registry_text)

    def test_memory_service_separates_chat_and_methodology_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            service = MemoryService(LocalMemoryAdapter(settings))

            service.write_chat_research_memory(
                query="analyze guizhou maotai",
                intent="stock_check",
                run_id="01CHAT",
                summary="chat summary",
                conclusion="chat conclusion",
                follow_up="chat next step",
                status="completed",
            )
            service.write_methodology_memory(
                memory_type="methodology_summary",
                content={"title": "Method note", "summary": "method summary"},
                metadata={"status": "approved"},
            )

            chat_rows = service.read_recent_chat_memory(limit=5)
            methodology_rows = service.read_recent_methodology_memory(limit_per_type=5)

            self.assertEqual(len(chat_rows), 1)
            self.assertEqual(chat_rows[0]["memory_type"], CHAT_RESEARCH_MEMORY_TYPE)
            self.assertEqual(chat_rows[0]["content"]["follow_up"], "chat next step")
            self.assertTrue(methodology_rows)
            self.assertEqual(methodology_rows[0]["memory_type"], "methodology_summary")
            self.assertEqual(methodology_rows[0]["metadata"]["snapshot_version"], METHODOLOGY_SNAPSHOT_VERSION)
            self.assertTrue(methodology_rows[0]["metadata"]["entity_scope"].startswith("methodology:"))

    def test_memory_service_search_chat_memory_can_exclude_current_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            service = MemoryService(LocalMemoryAdapter(settings))

            service.write_chat_research_memory(
                query="compare guizhou maotai cashflow resilience",
                intent="stock_compare",
                run_id="01T1",
                summary="thread one summary",
                conclusion="thread one conclusion",
                follow_up="thread one next step",
                status="completed",
                thread_id="thread-1",
            )
            service.write_chat_research_memory(
                query="compare byd leverage and cashflow resilience",
                intent="stock_compare",
                run_id="01T2",
                summary="thread two summary",
                conclusion="thread two conclusion",
                follow_up="thread two next step",
                status="completed",
                thread_id="thread-2",
            )

            rows = service.search_chat_research_memory(
                query="cashflow resilience",
                limit=5,
                exclude_thread_id="thread-1",
            )

            self.assertTrue(rows)
            self.assertTrue(all(row["content"]["thread_id"] != "thread-1" for row in rows))
            self.assertEqual(rows[0]["content"]["thread_id"], "thread-2")
            self.assertTrue(rows[0]["match_score"] > 0)

    def test_memory_service_search_methodology_memory_matches_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            service = MemoryService(LocalMemoryAdapter(settings))

            service.write_methodology_memory(
                memory_type="dimension_experience",
                content={
                    "title": "Cashflow resilience heuristic",
                    "summary": "Prefer stable free cash flow and lower leverage.",
                    "benchmark_scope": "defensive_compounder",
                },
                metadata={"status": "approved"},
            )

            rows = service.search_methodology_memory(query="free cash flow leverage", limit=5)

            self.assertTrue(rows)
            self.assertEqual(rows[0]["memory_type"], "dimension_experience")
            self.assertEqual(rows[0]["content"]["title"], "Cashflow resilience heuristic")
            self.assertTrue(rows[0]["match_score"] > 0)

    def test_search_methodology_memory_passes_benchmark_scope_into_adapter_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            adapter = LocalMemoryAdapter(settings)
            service = MemoryService(adapter)
            captured_filters: list[dict[str, object] | None] = []

            def fake_search(*, query: str, memory_types=None, limit: int = 10, metadata_filters=None):
                captured_filters.append(dict(metadata_filters or {}))
                return []

            with patch.object(adapter, "search", side_effect=fake_search):
                rows = service.search_methodology_memory(
                    query="policy catalyst",
                    benchmark_scope="event_driven",
                    limit=5,
                )

            self.assertEqual(rows, [])
            self.assertTrue(captured_filters)
            self.assertEqual(captured_filters[0]["memory_scope"], "methodology")
            self.assertEqual(captured_filters[0]["benchmark_scope"], "event_driven")

    def test_chat_memory_read_and_search_keep_latest_snapshot_per_entity_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            service = MemoryService(LocalMemoryAdapter(settings))

            service.write_chat_research_memory(
                query="compare guizhou maotai cashflow resilience",
                intent="stock_compare",
                run_id="01OLD",
                summary="older summary",
                conclusion="older conclusion",
                follow_up="older next step",
                status="completed",
                thread_id="thread-compare",
            )
            service.write_chat_research_memory(
                query="compare guizhou maotai cashflow resilience",
                intent="stock_compare",
                run_id="01NEW",
                summary="newer summary",
                conclusion="newer conclusion",
                follow_up="newer next step",
                status="completed",
                thread_id="thread-compare",
            )

            recent_rows = service.read_recent_chat_memory(limit=5)
            search_rows = service.search_chat_research_memory(query="cashflow resilience", limit=5)

            self.assertEqual(len(recent_rows), 1)
            self.assertEqual(recent_rows[0]["content"]["run_id"], "01NEW")
            self.assertEqual(len(search_rows), 1)
            self.assertEqual(search_rows[0]["content"]["run_id"], "01NEW")

    def test_methodology_memory_read_and_search_keep_latest_snapshot_per_entity_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            service = MemoryService(LocalMemoryAdapter(settings))

            service.write_methodology_memory(
                memory_type="dimension_experience",
                content={
                    "title": "Cashflow resilience heuristic",
                    "summary": "Older summary",
                    "benchmark_scope": "defensive_compounder",
                },
                metadata={"status": "approved"},
            )
            service.write_methodology_memory(
                memory_type="dimension_experience",
                content={
                    "title": "Cashflow resilience heuristic",
                    "summary": "Newer summary",
                    "benchmark_scope": "defensive_compounder",
                },
                metadata={"status": "approved"},
            )

            recent_rows = service.read_recent_methodology_memory(limit_per_type=5)
            search_rows = service.search_methodology_memory(query="cashflow resilience", limit=5)

            self.assertEqual(len(recent_rows), 1)
            self.assertEqual(recent_rows[0]["content"]["summary"], "Newer summary")
            self.assertEqual(len(search_rows), 1)
            self.assertEqual(search_rows[0]["content"]["summary"], "Newer summary")


if __name__ == "__main__":
    unittest.main()
