"""Report-side regressions: fixed company context and scoped document reuse.

These tests deliberately exercise the public apply function and a real typed
fake-provider response. They do not patch prompt builders or URL validators.
"""

import json
from copy import deepcopy
from hashlib import sha256
from threading import Lock
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    QualitativeGroupResult,
    apply_qualitative_deep_dive,
)


SYMBOL = "688041.SH"
COMPANY = "海光信息（冻结公司身份测试）"
INDUSTRY = "集成电路设计（冻结行业分类）"
AS_OF = "2026-09-08"
CUTOFF = "2026-09-08T11:56:57.544320+00:00"
GROUPS = {
    "A · Macro-Policy": ("3_macro", "13_policy"),
    "B · Industry-Events": ("7_industry", "15_events"),
    "C · Cost-Transmission": ("8_materials", "9_futures"),
}
TYPES = {
    "3_macro": "macro", "13_policy": "policy", "7_industry": "industry",
    "15_events": "events", "8_materials": "materials", "9_futures": "futures",
}
FINDINGS = {
    "3_macro": ("公司披露境内收入占比为百分之百，统计期间为上半年。", "公司披露主要客户包括服务器厂商，需求存在行业传导关系。"),
    "13_policy": ("公司披露本报告期享受集成电路设计企业税收优惠。", "公司披露研发费用按适用政策享受加计扣除优惠。"),
    "7_industry": ("公司披露主营高端处理器的研发设计与销售。", "公司披露产品包括通用处理器与人工智能协处理器。"),
    "15_events": ("公司披露上半年新增一项对外股权投资。", "公司披露报告期内新设子公司对整体经营无重大影响。"),
    "8_materials": ("公司披露主要采购晶圆以及封装测试服务。", "公司披露原材料涨价使得本期单位采购成本上升。"),
    "9_futures": ("公司披露衍生品投资情况本期不适用。", "公司披露没有已签订的商品期货套期保值合约。"),
}
SHARED_URL = "https://archive.example/688041/half-year-report.pdf"


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("report-context regressions must not call live sources or models")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def document(url, text, *, marker="固定公司原文"):
    content_hash = sha256(text.encode()).hexdigest()
    return {
        "type": "market_data_evidence", "id": f"evidence-{content_hash[:12]}",
        "source_kind": "research_document", "title": marker,
        "url": url, "content": text, "content_hash": content_hash,
        "content_ref": f"archive/{content_hash}.pdf",
        "published_at": "2026-08-14T00:00:00+00:00",
        "observed_at": "2026-09-08T09:44:00+00:00",
        "evidence_mode": "contemporaneous_archive",
        "grade": "A", "immutable_proof": True,
    }


def fixed_rows(*, historical=False):
    # cutoff_at is explicit fixture metadata, not inferred from wall-clock
    # time or a URL. Real callers must propagate their existing manifest's
    # exact cutoff if it is not already carried by materialized rows.
    manifest = {
        "manifest_id": "fixed-company-manifest", "manifest_hash": "a" * 64,
        "requested_as_of_date": AS_OF, "cutoff_at": CUTOFF,
        "pit_safe": True,
        "pit_mode": "historical_reconstruction" if historical else "live_observation",
    }
    rows = [{
        "snapshot_type": "market", "dimension": "0_basic", "symbol": SYMBOL,
        "snapshot_id": "fixed-basic", "snapshot_date": AS_OF,
        "quality_status": "available", "query_manifest": deepcopy(manifest),
        "facts": {"_uzi_raw_result": {"data": {
            "name": COMPANY, "code": SYMBOL, "industry": INDUSTRY,
            "price": 100.0, "business": {"product": "CPU与DCU"},
        }}},
        "source_refs": [],
    }]
    for dimension, snapshot_type in TYPES.items():
        rows.append({
            "snapshot_type": snapshot_type, "dimension": dimension,
            "symbol": SYMBOL, "snapshot_id": f"fixed-{dimension}",
            "snapshot_date": AS_OF, "quality_status": "available",
            "query_manifest": deepcopy(manifest),
            "facts": {"_uzi_raw_result": {"data": {"frozen_dimension": dimension}}},
            "source_refs": [document(
                f"https://archive.example/688041/{dimension}.pdf",
                "\n".join(FINDINGS[dimension]),
            )],
        })
    for row in rows:
        row["content_hash"] = digest(row["facts"])
    return rows


def attach_shared_document(rows):
    # Only the materials snapshot owns this archive relationship. Its actual
    # fixed text nevertheless contains facts relevant to all six dimensions.
    shared = document(SHARED_URL, "\n".join(
        finding for values in FINDINGS.values() for finding in values
    ))
    next(row for row in rows if row["dimension"] == "8_materials")["source_refs"].append(shared)
    return shared


class CapturingProvider:
    available = True

    def __init__(self, *, urls=None, unknown_dimension=None, unknown_finding=None, workspace_root=None):
        self.urls = urls or {}
        self.unknown_dimension = unknown_dimension
        self.unknown_finding = unknown_finding
        self.calls = []
        self.lock = Lock()
        settings = {"uzi_llm_transient_retries": 0}
        if workspace_root is not None:
            settings["workspace_root"] = workspace_root
        self.settings = SimpleNamespace(**settings)

    def parse(self, *, system_prompt, user_prompt, schema, **_kwargs):
        assert schema is QualitativeGroupResult
        label = user_prompt.splitlines()[0].split("：", 1)[1]
        with self.lock:
            self.calls.append({"group": label, "system_prompt": system_prompt, "user_prompt": user_prompt})
        dimensions = []
        for dimension in GROUPS[label]:
            url = self.urls.get(dimension, f"https://archive.example/688041/{dimension}.pdf")
            evidence = [
                {"source": "frozen issuer report", "url": url, "finding": finding,
                 "retrieved_at": AS_OF}
                for finding in FINDINGS[dimension]
            ]
            if dimension == self.unknown_dimension:
                evidence[1] = {"source": "unknown", "url": None, "retrieved_at": None,
                               "finding": self.unknown_finding or "冻结原文缺少这一项可核实事实，不能生成第二个来源。"}
            dimensions.append({
                "dimension_id": dimension, "evidence": evidence,
                "associations": [{
                    "link_to": "8_materials" if dimension != "8_materials" else "9_futures",
                    "chain_id": f"fixed-{dimension}-mechanism",
                    "causal_chain": "公司披露的客户需求和采购成本之间存在条件性的传导机制，尚不能归因到确定利润增量。",
                    "estimated_impact": "仅作条件分析，无法量化独立影响。",
                }],
                "conclusion": "本项仅使用已经固定的原文事实与已知条件，不调用外部数据补足缺失部分。",
            })
        return QualitativeGroupResult.model_validate({
            "agent_reviewed": True, "group_label": label, "dimensions": dimensions,
        })


def apply(rows, provider, *, analysis=None):
    if analysis is None:
        analysis = {"symbol": SYMBOL, "as_of_date": AS_OF, "summary": "offline report context",
                    "results": [], "risk_flags": []}
    original_rows, original_analysis = deepcopy(rows), deepcopy(analysis)
    before_rows, before_analysis = digest(rows), digest(analysis)
    result = apply_qualitative_deep_dive(
        analysis, snapshot_rows=rows, llm_provider=provider, research_profile="deep",
    )
    assert rows == original_rows and digest(rows) == before_rows
    assert analysis == original_analysis and digest(analysis) == before_analysis
    return result


@pytest.mark.parametrize("group", list(GROUPS))
def test_every_report_group_receives_fixed_company_identity_and_cutoff(group):
    provider = CapturingProvider()
    result = apply(fixed_rows(), provider)
    assert result["qualitative_agent_execution"]["status"] == "completed"
    assert len(provider.calls) == 3
    call = next(row for row in provider.calls if row["group"] == group)
    visible_prompt = call["system_prompt"] + "\n" + call["user_prompt"]

    assert COMPANY in visible_prompt, f"{group} lost the fixed 0_basic company name"
    for value in (SYMBOL, INDUSTRY, AS_OF, CUTOFF):
        assert value in visible_prompt, f"{group} omitted fixed company/scope value {value}"


@pytest.mark.parametrize("historical", [False, True], ids=["same-day", "historical"])
def test_one_fixed_stock_document_can_support_all_research_dimensions(historical):
    rows = fixed_rows(historical=historical)
    shared = attach_shared_document(rows)
    before_shared = deepcopy(shared)
    provider = CapturingProvider(urls={dimension: SHARED_URL for dimension in TYPES})

    result = apply(rows, provider)

    execution = result["qualitative_agent_execution"]
    assert execution["status"] == "completed", execution
    assert execution["completed_group_count"] == 3
    assert len(provider.calls) == 3
    for call in provider.calls:
        visible_prompt = call["system_prompt"] + "\n" + call["user_prompt"]
        assert SHARED_URL in visible_prompt
        for dimension in GROUPS[call["group"]]:
            assert FINDINGS[dimension][0] in visible_prompt
    for row in result["qualitative_deep_dive"].values():
        assert [item["url"] for item in row["evidence"]] == [SHARED_URL, SHARED_URL]
    assert shared == before_shared


def test_changing_fixed_basic_context_invalidates_all_cached_group_prompts(tmp_path):
    rows = fixed_rows()
    provider = CapturingProvider(workspace_root=tmp_path)
    assert apply(rows, provider)["qualitative_agent_execution"]["status"] == "completed"
    assert len(provider.calls) == 3
    new_rows = deepcopy(rows)
    new_rows[0]["facts"]["_uzi_raw_result"]["data"]["industry"] = "更新后的固定行业分类"
    new_rows[0]["content_hash"] = digest(new_rows[0]["facts"])

    assert apply(new_rows, provider)["qualitative_agent_execution"]["status"] == "completed"

    assert len(provider.calls) == 6, "0_basic changes must not reuse company-blind stage cache"
    assert all("更新后的固定行业分类" in row["system_prompt"] + row["user_prompt"] for row in provider.calls[3:])


@pytest.mark.parametrize("url", [
    "https://unrelated.example/unfrozen-report.pdf",
    "https://archive.example/688041/not-in-fixed-manifest.pdf",
])
def test_foreign_or_same_host_unfixed_url_is_still_rejected(url):
    provider = CapturingProvider(urls={"7_industry": url})
    result = apply(fixed_rows(), provider)
    execution = result["qualitative_agent_execution"]
    assert execution["status"] == "incomplete"
    assert "7_industry" not in result["qualitative_deep_dive"]
    assert any(
        reason["code"] == "qualitative_ungrounded_url"
        for failure in execution["failure_records"]
        for reason in failure.get("validation_reasons", [])
    )


def test_unrelated_stock_row_cannot_enter_the_target_company_document_pool():
    rows = fixed_rows()
    foreign = deepcopy(next(row for row in rows if row["dimension"] == "7_industry"))
    foreign.update(symbol="000001.SZ", snapshot_id="other-company-industry")
    url = "https://archive.example/000001/unrelated-stock-report.pdf"
    foreign["source_refs"] = [document(url, "FOREIGN_STOCK_TEXT_MUST_NOT_LEAK", marker="其他股票原文")]
    rows.append(foreign)
    provider = CapturingProvider(urls={"7_industry": url})

    result = apply(rows, provider)

    assert result["qualitative_agent_execution"]["status"] == "incomplete"
    assert "7_industry" not in result["qualitative_deep_dive"]
    assert all(
        "FOREIGN_STOCK_TEXT_MUST_NOT_LEAK" not in call["system_prompt"] + call["user_prompt"]
        for call in provider.calls
    )


@pytest.mark.parametrize("removed", ["content", "content_hash", "content_ref", "evidence_mode"])
def test_historical_cross_dimension_reuse_keeps_archive_proof_requirements(removed):
    rows = fixed_rows(historical=True)
    shared = attach_shared_document(rows)
    shared.pop(removed)
    provider = CapturingProvider(urls={"7_industry": SHARED_URL})

    result = apply(rows, provider)

    assert result["qualitative_agent_execution"]["status"] == "incomplete"
    assert "7_industry" not in result["qualitative_deep_dive"]


def test_historical_search_snippet_is_not_promoted_to_global_archive_evidence():
    rows = fixed_rows(historical=True)
    attach_shared_document(rows)["source_kind"] = "search_snippet"
    provider = CapturingProvider(urls={"7_industry": SHARED_URL})

    result = apply(rows, provider)

    assert result["qualitative_agent_execution"]["status"] == "incomplete"
    assert "7_industry" not in result["qualitative_deep_dive"]


def test_global_document_reuse_does_not_turn_unknown_into_a_second_finding():
    provider = CapturingProvider(unknown_dimension="9_futures")
    result = apply(fixed_rows(), provider)
    execution = result["qualitative_agent_execution"]
    assert execution["status"] == "incomplete"
    assert execution["completed_group_count"] == 3
    futures = result["qualitative_deep_dive"]["9_futures"]
    assert len(futures["evidence"]) == 1
    assert len(futures["disclosures"]) == 1
    assert futures["disclosures"][0]["source"] == "unknown"
    assert futures["disclosures"][0]["url"] is None
    assert "9_futures:grounded_citations_below_two_after_analysis:1" in execution["errors"]


@pytest.mark.parametrize("finding", [
    "未能在所给档案中查到该公司的套保名义本金",
    "提供的材料没有给出这一字段",
])
def test_native_unknown_explanations_do_not_require_host_keyword_vocabulary(finding):
    provider = CapturingProvider(unknown_dimension="9_futures", unknown_finding=finding)
    result = apply(fixed_rows(), provider)
    execution = result["qualitative_agent_execution"]
    assert execution["completed_group_count"] == 3, execution
    assert execution["status"] == "incomplete"
    futures = result["qualitative_deep_dive"]["9_futures"]
    assert len(futures["evidence"]) == 1
    assert futures["disclosures"][0]["finding"] == finding


@pytest.mark.parametrize("finding", ["", "   \n\t"])
def test_empty_unknown_explanation_is_still_rejected(finding):
    from pydantic import ValidationError
    from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import QualitativeEvidence

    with pytest.raises(ValidationError):
        QualitativeEvidence(source="unknown", finding=finding)


def test_unknown_structural_acceptance_does_not_verify_or_count_a_claim():
    # Native schema acceptance is not a semantic content audit. A non-empty
    # explanation cannot be judged from a finite vocabulary. An unsupported
    # claim still stays a disclosure and cannot satisfy factual evidence.
    provider = CapturingProvider(unknown_dimension="9_futures", unknown_finding="公司毛利率将提高")
    result = apply(fixed_rows(), provider)
    assert result["qualitative_agent_execution"]["completed_group_count"] == 3
    assert result["qualitative_agent_execution"]["status"] == "incomplete"
    assert len(result["qualitative_deep_dive"]["9_futures"]["evidence"]) == 1


def company_context(call):
    return json.loads(call["system_prompt"].split("FROZEN_COMPANY_CONTEXT=", 1)[1].split("\nFROZEN_STOCK_DOCUMENTS=", 1)[0])


def test_real_caller_empty_analysis_uses_fixed_row_identity_and_never_invents_cutoff():
    rows = fixed_rows()
    for row in rows:
        row["query_manifest"].pop("cutoff_at")
    provider = CapturingProvider()
    assert apply(rows, provider, analysis={})["qualitative_agent_execution"]["status"] == "completed"
    for call in provider.calls:
        context = company_context(call)
        assert context["symbol"] == SYMBOL
        assert context["as_of_date"] == AS_OF
        assert context["as_of_date_source"] == "fixed_snapshot_metadata"
        assert context["cutoff_at"] is None
        assert context["cutoff_status"] == "exact_cutoff_unavailable_or_conflicting"
        assert COMPANY in call["system_prompt"] and INDUSTRY in call["system_prompt"]


@pytest.mark.parametrize("change", ["cutoff", "hidden_fact"])
def test_cache_identity_binds_cutoff_and_unprojected_context_facts(tmp_path, change):
    rows = fixed_rows()
    rows[0]["facts"]["long_context"] = "正文" * 500 + "旧版事实"
    provider = CapturingProvider(workspace_root=tmp_path)
    assert apply(rows, provider)["qualitative_agent_execution"]["status"] == "completed"
    updated = deepcopy(rows)
    if change == "cutoff":
        for row in updated:
            row["query_manifest"]["cutoff_at"] = "2026-09-08T11:57:00+00:00"
    else:
        # The stored metadata hash and visible prefix intentionally remain the
        # same. Prompt cache identity must bind the full fixed source view.
        updated[0]["facts"]["long_context"] = "正文" * 500 + "新版事实"
    assert apply(updated, provider)["qualitative_agent_execution"]["status"] == "completed"
    assert len(provider.calls) == 6


def test_same_stock_bare_code_alias_keeps_shared_document_scope():
    rows = fixed_rows()
    attach_shared_document(rows)
    for row in rows:
        row["symbol"] = "688041"
    provider = CapturingProvider(urls={dimension: SHARED_URL for dimension in TYPES})
    assert apply(rows, provider)["qualitative_agent_execution"]["status"] == "completed"


def test_global_document_can_originate_outside_the_six_research_dimensions():
    rows = fixed_rows(historical=True)
    shared = deepcopy(next(row for row in rows if row["dimension"] == "8_materials"))
    shared.update(snapshot_type="financial", dimension="1_financial", snapshot_id="fixed-financial")
    shared["source_refs"] = [document(SHARED_URL, "\n".join(finding for values in FINDINGS.values() for finding in values))]
    rows.append(shared)
    provider = CapturingProvider(urls={dimension: SHARED_URL for dimension in TYPES})
    assert apply(rows, provider)["qualitative_agent_execution"]["status"] == "completed"


@pytest.mark.parametrize("invalid", ["future_snapshot", "pit_unsafe"])
def test_global_reuse_does_not_admit_future_or_explicitly_pit_unsafe_rows(invalid):
    rows = fixed_rows(historical=True)
    shared = deepcopy(next(row for row in rows if row["dimension"] == "8_materials"))
    shared["source_refs"] = [document(SHARED_URL, "FUTURE_OR_UNSAFE_SOURCE_MUST_NOT_LEAK")]
    if invalid == "future_snapshot":
        shared["snapshot_date"] = "2026-09-09"
    else:
        shared["query_manifest"]["pit_safe"] = False
    rows.append(shared)
    provider = CapturingProvider(urls={"7_industry": SHARED_URL})
    result = apply(rows, provider)
    assert result["qualitative_agent_execution"]["status"] == "incomplete"
    assert all("FUTURE_OR_UNSAFE_SOURCE_MUST_NOT_LEAK" not in call["system_prompt"] for call in provider.calls)


def test_company_context_is_not_sampled_away_among_many_fixed_snapshots():
    rows = fixed_rows()
    filler = {"symbol": SYMBOL, "snapshot_type": "financial", "snapshot_date": AS_OF, "facts": {}}
    rows = [deepcopy(filler) for _ in range(11)] + rows + [deepcopy(filler) for _ in range(11)]
    provider = CapturingProvider()
    assert apply(rows, provider)["qualitative_agent_execution"]["status"] == "completed"
    for call in provider.calls:
        assert COMPANY in call["system_prompt"]
        assert INDUSTRY in call["system_prompt"]


def test_unrelated_large_position_map_does_not_consume_company_context_budget():
    rows = fixed_rows()
    rows.append({"symbol": SYMBOL, "snapshot_type": "fund_holders", "snapshot_date": AS_OF,
                 "snapshot_id": "fixed-large-holdings", "content_hash": "b" * 64,
                 "facts": {f"position-{index}": "NON_COMPANY_BACKGROUND" * 35 for index in range(1000)}})
    provider = CapturingProvider()
    assert apply(rows, provider)["qualitative_agent_execution"]["status"] == "completed"
    for call in provider.calls:
        assert COMPANY in call["system_prompt"]
        assert "fixed-large-holdings" in call["system_prompt"]
        assert "NON_COMPANY_BACKGROUND" not in call["system_prompt"]


def test_related_link_inside_archived_document_is_not_a_new_fixed_document():
    rows = fixed_rows()
    ref = attach_shared_document(rows)
    unfixed_url = "https://archive.example/688041/related-but-not-archived.pdf"
    ref["related_links"] = [{"url": unfixed_url, "finding": "此相关链接没有本次固定归档的正文及内容哈希。"}]
    provider = CapturingProvider(urls={"7_industry": unfixed_url})
    result = apply(rows, provider)
    assert result["qualitative_agent_execution"]["status"] == "incomplete"
    assert "7_industry" not in result["qualitative_deep_dive"]
