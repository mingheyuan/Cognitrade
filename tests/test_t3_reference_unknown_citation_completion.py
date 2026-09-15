"""Task2.5 §2 disclosures and §5 URL-backed unknown source are distinct.

Synthetic completion classification only; no source authenticity or model
execution is claimed. Native §5 explicitly permits source=unknown with URL
and explanation, while §2 permits URL-less disclosures that cannot pad counts.
"""

from copy import deepcopy

import pytest

from scripts import run_uzi_agent_comparison as t3


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("completion tests must not call sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def complete(source="unknown"):
    entry = {
        "evidence": [
            {"source": source, "url": "https://frozen.example/company.pdf",
             "finding": finding, "retrieved_at": "2026-09-11"}
            for finding in ("固定正文列出公司主营产品甲。", "固定正文披露产品乙收入结构。")
        ],
        "associations": [{"link_to": "7_industry", "chain_id": "offline",
                          "causal_chain": "仅用于离线计数，不声称验证因果。", "estimated_impact": "未量化"}],
        "conclusion": "仅用于结构测试，不是公司研究结论。",
    }
    return {dim: deepcopy(entry) for _, dimensions in t3.RESEARCH for dim in dimensions}


@pytest.mark.parametrize("source", ["unknown", "UNKNOWN", " unknown "])
def test_unknown_source_with_url_and_date_counts_distinct_findings_without_authenticity_claim(source):
    value = complete(source)
    for _, dimensions in t3.RESEARCH:
        assert t3.qualitative_issues({dim: value[dim] for dim in dimensions}, list(dimensions)) == []
    result = t3.qualitative_completion(value)
    assert result["passed"] is True
    assert all(row["unique_factual_findings"] == 2 for row in result["dimensions"].values())
    assert all(row["unknown_evidence_count"] == 2 for row in result["dimensions"].values())
    assert result["citation_authenticity_audited"] is False
    assert result["scope"] == "task25_workflow_minimum_not_native_CHECKS"


def test_one_unknown_cited_source_and_one_named_source_can_share_same_document():
    value = complete("annual_report")
    value["9_futures"]["evidence"][1]["source"] = "unknown"
    result = t3.qualitative_completion(value)
    assert result["passed"] is True
    assert result["dimensions"]["9_futures"]["unique_factual_findings"] == 2


@pytest.mark.parametrize("field,value", [
    ("url", None), ("url", ""), ("url", "file:///archive/company.pdf"),
    ("url", "https://"), ("url", "https://[bad"),
    ("retrieved_at", None), ("retrieved_at", " "), ("finding", " "),
])
def test_unknown_disclosure_or_missing_factual_field_still_cannot_pad_count(field, value):
    data = complete("annual_report")
    item = data["9_futures"]["evidence"][1]
    item["source"] = "unknown"
    item[field] = value
    result = t3.qualitative_completion(data)
    assert result["passed"] is False
    assert result["dimensions"]["9_futures"]["unique_factual_findings"] == 1
    assert result["dimensions"]["9_futures"]["unknown_evidence_count"] == 1
    assert result["citation_authenticity_audited"] is False


def test_unknown_url_less_disclosure_remains_accepted_structure_but_zero_factual_findings():
    entry = {"evidence": [{"source": "unknown", "finding": "材料未披露套保名义本金。"}],
             "associations": [], "conclusion": "保留缺失，不编造本金。"}
    assert t3.qualitative_issues({"9_futures": entry}, ["9_futures"]) == []
    result = t3.qualitative_completion({"9_futures": entry})
    assert result["passed"] is False
    assert result["dimensions"]["9_futures"]["unique_factual_findings"] == 0


def test_unknown_repeated_finding_cannot_count_twice_under_different_urls():
    value = complete()
    first, second = value["3_macro"]["evidence"]
    first["finding"] = "FACT alpha"
    second.update(finding="  fact   ALPHA  ", url="https://frozen.example/second.pdf")
    result = t3.qualitative_completion(value)
    assert result["passed"] is False
    assert result["dimensions"]["3_macro"]["unique_factual_findings"] == 1


def test_count_fix_does_not_remove_native_global_association_minimum():
    value = complete()
    for row in value.values():
        row["associations"] = []
    result = t3.qualitative_completion(value)
    assert result["passed"] is False
    assert result["association_count"] == 0
    assert result["issues"] == [{"field": "qualitative_deep_dive.associations", "code": "insufficient_associations"}]
