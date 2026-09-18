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
    "固定行业快照将长川科技归类为半导体设备；截至2026-09-02，行业净利润同比增长4.69%。",
    "截至2026-09-02、回溯180天的有界公告查询结果为零条事件记录；仅说明指定来源未返回事件。",
    "固定快照截至2026-09-02显示关联品种为铜（CU0），最新收盘价为108040，12个月价格序列起点为78650；按序列首尾计算，价格明显高于12个月起点，但快照未提供公司实际铜采购敞口。",
    "冻结期货快照截至2026年9月2日，显示铜收盘价为108040。",
    "截至2026-09-02的历史有界事件查询窗口为2026-03-06至2026-09-02，规定来源查询结果为零条；因此本轮无法核实近30天公告分类、合同金额、诉讼金额或并购交易。该结论是数据查询状态，不是公司无事件的永久判断。",
    "截至2026年9月2日的历史有界查询窗口为2026年3月6日至9月2日，规定来源查询结果为零条；但固定归档保留一份2026年8月22日公告，内容为公司拟于2026年9月3日参加业绩说明会。",
    "报告披露公司于2026年5月28日召开2025年年度股东会。固定事件快照对截至2026-09-02的180日公告窗口返回“verified_empty”，因此本轮无法核实合同中标金额。",
    "报告披露，公司面临设备、零备件和原材料短缺风险。固定政策快照在2026-08-03至2026-09-02查询窗口返回provider_unavailable，未形成可引用的具体政策条目。",
    "固定宏观快照的查询窗口为2026-08-03至2026-09-02，返回verified_empty。",
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
    "公司公告披露数据快照备份项目已投产，并为客户提供公告查询服务。",
    "公司年报记载行业调查结果，按年度统计下游客户需求。",
    "公司公告截至2026-08-14显示本报告期未开展期货套期保值。",
    "公司年度报告披露公告查询窗口服务已上线，支持历史事件查询。",
    "公司公告披露其快照备份系统在查询窗口内返回provider_unavailable，研发团队已修复。",
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


@pytest.mark.parametrize("finding", [
    "截至2026-09-02的历史有界事件查询窗口为2026-03-06至2026-09-02，规定来源查询结果为零条；仅说明本轮查询状态。",
    "固定政策快照在2026-08-03至2026-09-02查询窗口返回provider_unavailable，未形成可引用的具体政策条目。",
])
def test_bounded_query_limitation_is_allowed_without_borrowing_a_document(finding):
    class WithQueryDisclosure(CapturingProvider):
        def parse(self, **kwargs):
            group = super().parse(**kwargs)
            for dimension in group.dimensions:
                if dimension.dimension_id == "15_events":
                    dimension.evidence.append(q.QualitativeEvidence(source="unknown", finding=finding))
            return group

    rows = fixed_rows(historical=True)
    attach_shared_document(rows)
    result = q.apply_qualitative_deep_dive({}, snapshot_rows=rows, llm_provider=WithQueryDisclosure())
    assert result["qualitative_agent_execution"]["status"] == "completed"
    events = result["qualitative_deep_dive"]["15_events"]
    disclosure = next(row for row in events["disclosures"] if row["finding"] == finding)
    assert disclosure["source"] == "unknown" and disclosure["url"] is None
    assert len(events["evidence"]) == 2
    assert result["qualitative_agent_execution"]["unknown_evidence_count"] == 1


@pytest.mark.parametrize("group_prefix,dimension_index,finding", [
    ("B", 0, "固定行业快照显示行业估值中位数PE为50.84。"),
    ("C", 1, "固定快照截至2026-09-02显示关联品种为铜（CU0），最新收盘价为108040。"),
    ("B", 1, "截至2026-09-02的历史有界事件查询窗口为2026-03-06至2026-09-02，规定来源查询结果为零条；该结论是数据查询状态。"),
    ("A", 1, "固定政策快照在2026-08-03至2026-09-02查询窗口返回provider_unavailable，未形成可引用的具体政策条目。"),
])
def test_old_misattributed_cache_repairs_only_the_affected_group(
    tmp_path, monkeypatch, group_prefix, dimension_index, finding,
):
    rows = fixed_rows(historical=True)
    attach_shared_document(rows)
    urls = {dim: SHARED_URL for dim in q._DIMENSION_TYPES}
    first = CapturingProvider(urls=urls, workspace_root=tmp_path)
    assert q.apply_qualitative_deep_dive({}, snapshot_rows=rows, llm_provider=first)["qualitative_agent_execution"]["status"] == "completed"
    load = q._load_qualitative_stage_cache

    def old_cache(path):
        cached = load(path)
        if cached is not None and cached.group_label.startswith(group_prefix):
            cached.dimensions[dimension_index].evidence[0].finding = finding
        return cached

    second = CapturingProvider(urls=urls, workspace_root=tmp_path)
    with monkeypatch.context() as scoped:
        scoped.setattr(q, "_load_qualitative_stage_cache", old_cache)
        result = q.apply_qualitative_deep_dive({}, snapshot_rows=rows, llm_provider=second)
    assert result["qualitative_agent_execution"]["status"] == "completed"
    assert len(second.calls) == 1
    assert second.calls[0]["group"].startswith(group_prefix)
