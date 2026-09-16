"""Fixed source roles and concrete known misattributions; entirely offline."""
import json
from copy import deepcopy

import pytest

from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as q
from domain_packs.finance_ashare.subagents.uzi_evidence_bindings import document_attribution_conflicts
from domain_packs.finance_ashare.subagents.uzi_llm_support import model_failure_record
from research_system.market_data.contracts import canonical_hash
from tests.test_uzi_qualitative_company_document_context import (
    CapturingProvider, SHARED_URL, attach_shared_document, fixed_rows,
)
from tests.test_uzi_validation_diagnostic_contract import VALIDATOR


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *a, **k: pytest.fail("unexpected network"))


def test_legacy_navigation_is_classified_without_rewriting_frozen_facts():
    rows = fixed_rows(historical=True)
    industry = next(row for row in rows if row.get("dimension") == "7_industry")
    url = "https://webapi.cninfo.com.cn/#/apiDoc"
    industry["facts"]["_uzi_raw_result"] = {
        "data": {"industry_pe": 35.2, "historical_evidence": [{"url": url, "title": "行业分类"}]},
        "source": "akshare_cninfo_pit", "error": None, "_timeout": False,
    }
    before, digest = deepcopy(rows), canonical_hash(rows)
    scoped, _, documents = q._fixed_report_inputs(rows, {})
    view = q._dimension_context(scoped, "7_industry", stock_documents=documents)
    assert url not in view["allowed_urls"]
    assert view["source_discovery"] == [{"url": url, "source_kind": "discovery_entry",
                                          "reason": "navigation_not_research_document"}]
    origin = view["snapshot_origins"][0]
    assert origin["snapshot_id"] == industry["snapshot_id"]
    assert origin["content_hash"] == industry["content_hash"]
    assert origin["data_path"] == "facts._uzi_raw_result.data"
    assert origin["source"] == "akshare_cninfo_pit"
    assert rows == before and canonical_hash(rows) == digest
    # Contemporary extraction must not promote the same old navigation either.
    assert q._extract_evidence_records(industry["facts"]) == []


@pytest.mark.parametrize("finding", [
    "2026-09-02 铜 CU0 收盘价 108040，60 日趋势 3.77%。",
    "固定数据未完成行业—原材料—合约映射，未执行价格查询。",
])
def test_known_misattributions_fail_the_actual_group_check(finding):
    rows = fixed_rows(historical=True)
    doc = attach_shared_document(rows)
    scoped, _, documents = q._fixed_report_inputs(rows, {})
    bundle = {dim: q._dimension_context(scoped, dim, stock_documents=documents) for dim in q._DIMENSION_TYPES}
    provider = CapturingProvider(urls={dim: SHARED_URL for dim in q._DIMENSION_TYPES})
    group = provider.parse(system_prompt="", user_prompt="负责分组：C · Cost-Transmission", schema=q.QualitativeGroupResult)
    group.dimensions[1].evidence[0].finding = finding
    with pytest.raises(ValueError) as caught:
        q._validate_group(group, ("8_materials", "9_futures"), bundle)
    record = model_failure_record(caught.value, stage="qualitative_C · Cost-Transmission")
    assert record["validation_reasons"] == [{"field": "dimensions.1.evidence", "code": "qualitative_source_attribution_conflict"}]
    VALIDATOR.validate(record)
    assert finding not in json.dumps(record) and doc["url"] not in json.dumps(record)


@pytest.mark.parametrize("finding", [
    "2026-08-01 铜 CU0 收盘价 108040。",
    "情景假设 2026-09-02 铜 CU0 收盘价为 108040。",
    "公司公告预测 2026-09-02 的结算价可能升至 108040。",
    "公司披露本报告期未开展期货套期保值。",
    "以固定行情来源计算成本传导，具体幅度未知。",
    "2026-08-01 铜 CU0 收盘价 108040。2026-09-02 公司将召开股东大会。",
    "铜 CU0 收盘价 108040，公司将于 2026-09-02 召开股东大会。",
])
def test_scope_check_is_not_a_generic_semantic_or_forecast_gate(finding):
    assert not document_attribution_conflicts(finding, ["2026-08-14T00:00:00Z"])


def test_same_original_supports_two_distinct_facts_without_new_source_quota():
    rows = fixed_rows(historical=True)
    attach_shared_document(rows)
    provider = CapturingProvider(urls={dim: SHARED_URL for dim in q._DIMENSION_TYPES})
    result = q.apply_qualitative_deep_dive({}, snapshot_rows=rows, llm_provider=provider)
    assert result["qualitative_agent_execution"]["status"] == "completed"
    assert result["qualitative_agent_execution"]["evidence_count"] == 12
    assert all("SOURCE_ROLES_V1" in call["system_prompt"] for call in provider.calls)


def test_unknown_publication_is_not_inferred_from_a_url_or_observation():
    assert not document_attribution_conflicts("2026-09-02 铜 CU0 收盘价 108040。", [])
    assert not document_attribution_conflicts("2026-09-02 铜 CU0 收盘价 108040。", ["unknown"])
