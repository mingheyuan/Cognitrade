"""A legacy manual recovery cannot undo a grounded raw fact revision."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from research_system.market_data import agent_recovery, research_enrichment
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ArchiveCandidate
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_native_symbol_transport import _source
from tests.market_data.test_pit_core import AS_OF, NOW
from tests.market_data.test_research_archive import CUTOFF, collector, page
from tests.market_data.test_research_enrichment import _job


def test_manual_recovery_preserves_grounded_value_original_envelope_and_transport(tmp_path, monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *args, **kwargs: pytest.fail("provenance test attempted network"))
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: pytest.fail("provenance test attempted network"))
    quote = "2026年上半年，600519所在行业收入增长25%，行业集中度40%。"
    body = page(document_id="source-123").replace("原始政策正文，公布经济统计与货币政策措施。".encode(), quote.encode())
    original_document = collector(tmp_path / "docs", body, observed=NOW).archive(
        ArchiveCandidate("https://www.pbc.gov.cn/release/123.html"), dimension="7_industry", symbol="600519.SH", cutoff_at=CUTOFF,
    )
    _, source = _source({"full": "600519.SH", "fund_managers": [], "similar_stocks": None})
    source["snapshot_type"] = "industry"
    source["quality_status"] = "failed"
    source["facts"]["availability"] = "failed"
    original_envelope = {"data": {"growth": "10%", "industry": "测试行业"}, "source": "original-provider", "error": "original timeout", "_timeout": True}
    source["facts"]["_uzi_raw_result"] = deepcopy(original_envelope)
    value = UziSnapshotAdapter().adapt(source, symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=AS_OF)
    value.evidence.append(original_document)
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'manual-provenance.sqlite'}")
    database.create_schema()
    try:
        with database.session() as session:
            repo = MarketDataRepository(session)
            original = SnapshotIngestionService(repo).ingest(value)
            proof = next(row for row in repo.list_snapshot_evidence(original.id) if row.source_kind == "research_document")
            start = proof.inline_content.index(quote)
            proposal = {
                "snapshot_id": original.id, "snapshot_hash": original.content_hash,
                "dimension": "7_industry", "field": "growth", "old_exists": True,
                "old_value": "10%", "new_value": "25%", "unit": "%",
                "period_start": "2026-01-01", "period_end": "2026-06-30", "period_text": "2026年上半年",
                "finding": "原文披露行业收入增长25%", "citations": [{"evidence_id": proof.id, "document_hash": proof.content_hash, "start": start, "end": start + len(quote), "quote": quote}],
            }
            revised = research_enrichment.persist_fact_revisions(repo, proposals=[proposal], registry=DEFAULT_DIMENSION_REGISTRY, job_id="offline", now=NOW + timedelta(hours=1))
            assert revised["rejected"] == []
            revision_id = revised["revisions"][0]["snapshot_id"]
            revision = repo.require_snapshot(revision_id)
            revision_hash = revision.content_hash
            original_id, original_hash = original.id, original.content_hash
            transport_proof = next(row for row in repo.list_snapshot_evidence(revision_id) if row.source_kind == "native_symbol_transport")
            transport_id, transport_observed = transport_proof.id, transport_proof.observed_at

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                value = NOW + timedelta(hours=2)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        monkeypatch.setattr(agent_recovery, "datetime", Clock)
        job_id = _job(database)
        consumed = []

        def legacy_recovery(snapshots, **kwargs):
            industry = next(row for row in snapshots if row["snapshot_type"] == "industry")
            consumed.append(deepcopy(industry))
            industry["facts"]["agent_verified_sector"] = "测试行业"
            return {"attempted": 1, "succeeded": 1, "failed": 0}

        monkeypatch.setattr(agent_recovery, "recover_uzi_draft_snapshots", legacy_recovery)
        result = agent_recovery.recover_collection_job_draft(
            database, job_id=job_id, settings=None,
            research_pipeline=lambda: {key: {"status": "completed"} for key in ("research_archive", "research_enrichment", "native_quant_auxiliary")},
        )
        assert consumed[0]["facts"]["_uzi_raw_result"]["data"]["growth"] == "25%"
        assert result["persisted_snapshot_count"] == 1
        with database.session() as session:
            repo = MarketDataRepository(session)
            latest = repo.require_snapshot(result["persisted_snapshots"][0]["snapshot_id"])
            assert latest.payload_json["raw"]["growth"] == "25%"
            assert latest.payload_json["_uzi_raw_result"]["data"]["growth"] == "25%"
            assert latest.payload_json["_uzi_collector_original_result"] == original_envelope
            assert latest.payload_json["_uzi_raw_result"]["error"] == "original timeout"
            assert latest.payload_json["_uzi_raw_result"]["_timeout"] is True
            assert latest.payload_json["_uzi_raw_result"]["source"] == "original-provider"
            assert latest.status == repo.require_snapshot(revision_id).status == "data_gap"
            assert latest.payload_json["_uzi_top_level_fields"] == source["facts"]["_uzi_top_level_fields"]
            latest_proof = next(row for row in repo.list_snapshot_evidence(latest.id) if row.source_kind == "native_symbol_transport")
            assert latest_proof.id == transport_id and latest_proof.observed_at == transport_observed
            assert proof.id in {row.id for row in repo.list_snapshot_evidence(latest.id)}
            assert repo.require_snapshot(original_id).content_hash == original_hash
            assert repo.require_snapshot(revision_id).content_hash == revision_hash
            assert repo.require_snapshot(revision_id).payload_json["_uzi_raw_result"] == original_envelope
    finally:
        database.close()
