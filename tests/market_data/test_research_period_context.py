"""Period definitions may ground current qualitative disclosures, not figures."""

from copy import deepcopy

import pytest

from research_system.market_data import research_enrichment as enrichment
from research_system.market_data.contracts import cutoff_at_for_date
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ArchiveCandidate
from research_system.market_data.research_suggestions import resolve_fact_suggestions
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import _input, _payload
from tests.market_data.test_research_archive import CUTOFF, LATER, collector, page

DEFINITION = "报告期、本期 指 2026 年1 月1 日至2026 年6 月30 日"
HEADER = "测试股份有限公司2026 年半年度报告。公司代码：600519。"
DISCLOSURES = [
    ("8_materials", "core_material", "晶圆", "公司主营业务为集成电路芯片设计及销售，主要采购内容为晶圆等原材料以及封装和测试服务。"),
    ("8_materials", "industry_resolved", "集成电路设计行业", "公司所处的集成电路设计行业正处于国产化发展阶段。"),
    ("8_materials", "supply_security", "完成了完整的国产供应链整合", "报告期内，公司完成了完整的国产供应链整合，形成了产品化能力。"),
    ("9_futures", "hedging_disclosure_status", "□适用 √不适用", "衍生品投资情况\n□适用 √不适用"),
    ("7_industry", "industry", "计算机、通信和其他电子设备制造业", "公司属于制造业中的计算机、通信和其他电子设备制造业。"),
]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("period binding must not contact sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def frozen_input(*, dimension="8_materials", field="core_material", value="晶圆",
                 quote=DISCLOSURES[0][3], header=HEADER, definition=DEFINITION):
    text = f"{header}\n{definition}。\n{quote}\n其他披露。"
    row = {"snapshot_id": "snapshot", "snapshot_hash": "a" * 64,
           "symbol": "600519.SH", "revision_allowed": True, "raw": {},
           "documents": [{"evidence_id": "proof", "document_hash": "b" * 64, "inline_content": text}]}
    suggestion = {"snapshot_id": "snapshot", "field": field, "new_value": value,
                  "unit": "文本", "period_text": "2026年半年度", "finding": "公司报告明确披露该事实",
                  "citations": [{"evidence_id": "proof", "quote": quote}]}
    return {dimension: row}, suggestion


@pytest.mark.parametrize("dimension,field,value,quote", DISCLOSURES)
def test_compiler_binds_same_report_exact_period_definition(dimension, field, value, quote):
    inputs, suggestion = frozen_input(dimension=dimension, field=field, value=value, quote=quote)
    before = deepcopy(inputs)
    result = resolve_fact_suggestions([suggestion], inputs=inputs)
    proposal = result["proposals"][0]
    assert result["rejected"] == []
    assert any(c["quote"] == DEFINITION for c in proposal["citations"])
    assert proposal["new_value"] == value
    for citation in proposal["citations"]:
        assert inputs[dimension]["documents"][0]["inline_content"][citation["start"]:citation["end"]] == citation["quote"]
    assert inputs == before


@pytest.mark.parametrize("change", [
    {"header": HEADER.replace("600519", "688041")},
    {"header": "行业资料2026年半年度报告。"},
    {"definition": DEFINITION.replace("2026", "2025")},
    {"definition": DEFINITION + "。报告期 指 2025 年1 月1 日至2025 年6 月30 日"},
    {"definition": ""},
    {"quote": "上年公司采购晶圆。"},
    {"quote": "公司预计采购晶圆。"},
    {"quote": "报告期后，公司采购晶圆。"},
    {"quote": "2025年公司采购晶圆。"},
    {"dimension": "8_materials", "field": "cost_share", "value": "40%", "quote": "报告期内成本占比40%。"},
    {"value": "40", "quote": "公司采购原料40。"},
])
def test_definition_does_not_authorize_ambiguous_period_or_quantitative_facts(change):
    inputs, suggestion = frozen_input(**change)
    proposal = resolve_fact_suggestions([suggestion], inputs=inputs)["proposals"][0]
    assert len(proposal["citations"]) == 1


def test_period_cannot_be_borrowed_from_another_bound_document():
    inputs, suggestion = frozen_input(definition="")
    other, _ = frozen_input()
    doc = {**other["8_materials"]["documents"][0], "evidence_id": "other", "document_hash": "c" * 64}
    inputs["8_materials"]["documents"].append(doc)
    proposal = resolve_fact_suggestions([suggestion], inputs=inputs)["proposals"][0]
    assert len(proposal["citations"]) == 1


@pytest.mark.parametrize("prefix", ["上年", "公司预计未来", "2025年"])
def test_truncated_quote_does_not_hide_a_competing_period(prefix):
    inputs, suggestion = frozen_input()
    document = inputs["8_materials"]["documents"][0]
    quote = suggestion["citations"][0]["quote"]
    document["inline_content"] = document["inline_content"].replace(quote, prefix + quote)
    proposal = resolve_fact_suggestions([suggestion], inputs=inputs)["proposals"][0]
    assert len(proposal["citations"]) == 1


def test_old_checkbox_section_does_not_inherit_current_report_period():
    dimension, field, value, quote = DISCLOSURES[3]
    inputs, suggestion = frozen_input(dimension=dimension, field=field, value=value, quote=quote)
    doc = inputs[dimension]["documents"][0]
    doc["inline_content"] = doc["inline_content"].replace(quote, "上年披露\n" + quote)
    proposal = resolve_fact_suggestions([suggestion], inputs=inputs)["proposals"][0]
    assert len(proposal["citations"]) == 1


def test_real_persistence_validates_period_definition_and_preserves_original(tmp_path):
    inputs, suggestion = frozen_input()
    text = inputs["8_materials"]["documents"][0]["inline_content"]
    body = page(modified="2026-08-19", document_id="period-test").replace(
        "原始政策正文，公布经济统计与货币政策措施。".encode(), text.encode(), 1)
    evidence = collector(tmp_path / "docs", body).archive(
        ArchiveCandidate("https://www.cninfo.com.cn/release/period-test.html"),
        dimension="8_materials", symbol="600519.SH", cutoff_at=CUTOFF)
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'period.sqlite'}")
    database.create_schema()
    try:
        with database.session() as session:
            repo = MarketDataRepository(session)
            payload = {**_payload("8_materials"), "raw": {}, "_uzi_raw_result": {"data": {}, "error": "original_failure"}}
            snapshot = SnapshotIngestionService(repo).ingest(_input(
                "8_materials", payload=payload, evidence=[evidence], observed_at=LATER, ingested_at=LATER))
            documents = enrichment._research_documents(snapshot, repo.list_snapshot_evidence(snapshot.id),
                cutoff_at_for_date(snapshot.as_of_date, snapshot.exchange_timezone))
            inputs["8_materials"].update(snapshot_id=snapshot.id, snapshot_hash=snapshot.content_hash, documents=documents)
            suggestion.update(snapshot_id=snapshot.id, citations=[{"evidence_id": documents[0]["evidence_id"], "quote": DISCLOSURES[0][3]}])
            old_hash, old_payload = snapshot.content_hash, deepcopy(snapshot.payload_json)
            proposals = resolve_fact_suggestions([suggestion], inputs=inputs)["proposals"]
            assert len(proposals[0]["citations"]) == 2
            # Direct callers cannot use a report-period definition as proof for
            # a quantitative field, or omit its explicit citation from storage.
            for change in ({"field": "cost_share"}, {"citations": proposals[0]["citations"][:1]}):
                rejected = enrichment.persist_fact_revisions(repo, proposals=[{**proposals[0], **change}],
                    registry=DEFAULT_DIMENSION_REGISTRY, job_id="offline-invalid-period", now=LATER)
                assert not rejected["revisions"] and rejected["rejected"][0]["reason"] == "period_not_grounded"
            result = enrichment.persist_fact_revisions(repo, proposals=proposals,
                registry=DEFAULT_DIMENSION_REGISTRY, job_id="offline-period", now=LATER)
            assert result["rejected"] == []
            revision = repo.require_snapshot(result["revisions"][0]["snapshot_id"])
            assert revision.payload_json["raw"]["core_material"] == "晶圆"
            assert revision.payload_json["_uzi_raw_result"] == old_payload["_uzi_raw_result"]
            assert snapshot.content_hash == old_hash and snapshot.payload_json == old_payload
            assert revision.query_manifest_json["research_enrichment"]["period_binding_version"]
    finally:
        database.close()
