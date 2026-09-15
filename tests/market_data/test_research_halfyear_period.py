"""The observed issuer 'YYYY年半年度' label is calendar H1, not all year."""

from copy import deepcopy
from datetime import date

import pytest

from research_system.market_data import research_enrichment as enrichment
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ArchiveCandidate
from research_system.market_data.research_suggestions import resolve_fact_suggestions
from tests.market_data.test_pit_core import _input, _payload
from tests.market_data.test_research_archive import CUTOFF, LATER, collector, page
from tests.market_data.test_research_enrichment import source as source  # noqa: PLC0414


@pytest.mark.parametrize("label,expected", [
    ("2026年半年度", (date(2026, 1, 1), date(2026, 6, 30))),
    ("2026年上半年", (date(2026, 1, 1), date(2026, 6, 30))),
    ("2026年度", (date(2026, 1, 1), date(2026, 12, 31))),
    ("2026年下半年", (date(2026, 7, 1), date(2026, 12, 31))),
    ("半年度", None), ("报告期", None),
    ("2026年半年度及以后", None), ("2026年下半年度", None),
])
def test_halfyear_spelling_is_narrow_and_preserves_existing_periods(label, expected):
    assert enrichment._fact_period(label) == expected


def test_literal_halfyear_compiles_and_persists_only_when_quoted_and_before_cutoff(source, tmp_path):
    database, _, _ = source
    quote = "2026年半年度，600519所在行业收入增长25%，行业集中度40%。"
    body = page(modified="2026-08-19", document_id="h1-fact").replace(
        "原始政策正文，公布经济统计与货币政策措施。".encode(), quote.encode(), 1,
    )
    evidence = collector(tmp_path / "halfyear-docs", body).archive(
        ArchiveCandidate("https://www.pbc.gov.cn/release/halfyear.html"),
        dimension="7_industry", symbol="600519.SH", cutoff_at=CUTOFF,
    )
    with database.session() as session:
        repo = MarketDataRepository(session)
        original = SnapshotIngestionService(repo).ingest(_input(
            "7_industry", payload=_payload("7_industry"), evidence=[evidence],
            observed_at=LATER, ingested_at=LATER,
        ))
        before_hash, before_payload = original.content_hash, deepcopy(original.payload_json)
        proof = repo.list_snapshot_evidence(original.id)[0]
        inputs = {"7_industry": {"snapshot_id": original.id, "snapshot_hash": original.content_hash,
            "raw": original.payload_json["raw"], "revision_allowed": True,
            "documents": [{"evidence_id": proof.id, "document_hash": proof.content_hash,
                           "inline_content": proof.inline_content}]}}
        suggestion = {"snapshot_id": original.id, "field": "growth", "new_value": "25%",
            "unit": "%", "period_text": "2026年半年度", "finding": "半年度行业收入增长原文事实",
            "citations": [{"evidence_id": proof.id, "quote": quote}]}
        compiled = resolve_fact_suggestions([suggestion], inputs=inputs)
        assert compiled["rejected"] == []
        proposal = compiled["proposals"][0]
        assert (proposal["period_start"], proposal["period_end"]) == ("2026-01-01", "2026-06-30")
        result = enrichment.persist_fact_revisions(repo, proposals=[proposal],
            registry=DEFAULT_DIMENSION_REGISTRY, job_id="halfyear-test", now=LATER)
        assert result["rejected"] == [] and len(result["revisions"]) == 1
        assert original.content_hash == before_hash and original.payload_json == before_payload
        for change, reason in [
            ({"period_end": "2026-09-30"}, "period_after_cutoff"),
            ({"period_start": "2026-02-01"}, "period_not_supported_or_mismatch"),
            ({"period_text": "2025年半年度"}, "period_not_grounded"),
        ]:
            refused = enrichment.persist_fact_revisions(repo, proposals=[{**proposal, **change}],
                registry=DEFAULT_DIMENSION_REGISTRY, job_id="halfyear-test", now=LATER)
            assert refused["revisions"] == [] and refused["rejected"][0]["reason"] == reason
