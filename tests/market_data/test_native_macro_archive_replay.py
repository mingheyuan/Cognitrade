"""Fictional offline archive leaves; these are not historical market evidence."""

from copy import deepcopy
from hashlib import sha256
from threading import Event, Timer
from time import monotonic
import sys

import pytest

from research_system.market_data import native_macro


def document(text, *, url="https://www.pbc.gov.cn/fictional-offline-test", title="离线测试"):
    digest = sha256(text.encode()).hexdigest()
    return {"evidence_id": digest, "document_hash": digest, "text_hash": digest,
            "title": title, "url": url, "inline_content": text}


def recording(documents=None, **kwargs):
    return native_macro.archive_recording(
        symbol="600519.SH", basic={"name": "离线甲公司", "industry": "测试行业"},
        as_of_date="2025-08-20", documents=documents or [], **kwargs,
    )


def test_missing_archive_preserves_native_defaults_but_never_claims_observed_neutral():
    value = recording()
    result = native_macro.replay_macro(value, deadline_at=monotonic() + 30)
    assert result["status"] == "completed", result
    data = result["native_result"]["data"]
    assert data["year"] == 2025
    assert data["rate_cycle"] == "中性（2025 货币政策）"
    assert result["native_result"]["fallback"] is False
    assert all(row["status"] == "unassessed" and row["evidence_count"] == 0
               and row["verified_neutral_fact"] is False for row in result["coverage"].values())
    assert len(result["calls"]) == 6
    assert result["isolation_events"] == []
    assert "fetch_macro" not in sys.modules


def test_exact_six_native_queries_and_history_clock_are_replayed():
    value = recording([document("货币政策讨论降息、宽松、利好、稳定。\n美联储利率周期。\n人民币汇率走势。")])
    before = deepcopy(value)
    result = native_macro.replay_macro(value, deadline_at=monotonic() + 30)
    assert result["status"] == "completed", result
    assert result["source_sha256"] == native_macro.NATIVE_SOURCE_SHA256
    assert result["recording_hash"] == native_macro.canonical_hash(value)
    assert result["native_result"]["data"]["rate_cycle"] == "利好（2025 货币政策）"
    assert [row["key"] for row in result["calls"]] == list(native_macro.QUERIES)
    assert [row["leaf"] for row in result["calls"]] == ["trusted"] * 3 + ["untrusted"] * 3
    assert [row["query"] for row in value["queries"]] == [
        "2025 中国 利率 货币政策 降息 最新", "2025 美联储 利率周期 最新", "2025 人民币 汇率 走势",
        "2025 中美关系 贸易 制裁 测试行业", "2025 大宗商品 周期 CRB指数", "2025 测试行业 宏观 政策 利好 利空",
    ]
    assert value == before


def test_exact_spans_keep_topic_in_native_consumed_200_characters():
    text = "无关序文。" * 80 + "货币政策并未说明任何利好。" + "后文。" * 100
    value = recording([document(text)])
    rows = value["queries"][0]["trusted"]
    assert rows
    for row in rows:
        assert row["body"] == text[row["start"]:row["end"]]
        assert len(row["body"]) <= 200
        assert "货币政策" in row["body"]
    assert value["selection"]["negative_search_coverage_proven"] is False


@pytest.mark.parametrize("url", ["https://pbc.gov.cn.evil.invalid/x", "https://example.invalid/pbc.gov.cn/x"])
def test_trusted_groups_do_not_accept_lookalike_domains_or_topic_titles(url):
    value = recording([document("货币政策", url=url), document("办公地址说明", title="货币政策")])
    assert not value["queries"][0]["trusted"]


def test_company_archive_can_support_generic_geo_without_filling_other_groups():
    value = recording([document("离线甲公司说明出口管制带来的经营风险。", url="https://example.invalid/report")])
    assert value["queries"][3]["untrusted"]
    assert not any(row["trusted"] or row["untrusted"] for row in value["queries"] if row["key"] != "geo_risk")
    result = native_macro.replay_macro(value, deadline_at=monotonic() + 30)
    assert result["coverage"]["geo_risk"]["status"] == "archive_evidence_present"
    assert result["coverage"]["rate_cycle"]["status"] == "unassessed"


def test_trade_statistics_organization_name_is_not_geopolitical_evidence():
    # Actual archived text contains this organization name; the fictional
    # continuation intentionally contains no numeric market claim.
    value = recording([document("世界半导体贸易统计组织（WSTS）资料目录。", url="https://example.invalid/test")])
    assert not value["queries"][3]["untrusted"]


def test_wrapped_pdf_line_keeps_the_contiguous_sentence_not_just_its_first_line():
    text = "不能与供应商保持稳定合作，地缘政治因素可能导致供\n应短缺并影响生产。本段结束。"
    value = recording([document(text, url="https://example.invalid/test")])
    assert value["queries"][3]["untrusted"][0]["body"] == text[:text.index("。") + 1]


def test_unknown_industry_is_explicit_native_default_not_guessed_from_documents():
    value = native_macro.archive_recording(symbol="688041.SH", basic={"name": "离线甲公司", "industry": None},
        as_of_date="2025-08-20", documents=[document("离线甲公司半导体产业政策。", url="https://example.invalid/a")])
    assert value["industry"] == "综合"
    assert value["industry_origin"] == "native_default_unresolved"
    assert not value["queries"][-1]["untrusted"]


@pytest.mark.parametrize("mutation", ["query", "duplicate", "body", "leaf"])
def test_tampered_recording_fails_shut_in_real_worker(mutation):
    value = recording([document("货币政策。")])
    if mutation == "query":
        value["queries"][0]["query"] = "unrecorded query"
    elif mutation == "duplicate":
        value["queries"][1] = deepcopy(value["queries"][0])
    elif mutation == "body":
        value["queries"][0]["trusted"][0]["body"] = "changed"
    else:
        value["queries"][0]["untrusted"] = value["queries"][0]["trusted"]
    result = native_macro.replay_macro(value, deadline_at=monotonic() + 30)
    assert result["status"] == "failed"
    assert "native_result" not in result


@pytest.mark.parametrize("expired", [False, True])
def test_pre_cancel_or_expiry_does_not_start_worker(monkeypatch, expired):
    cancel = Event()
    if not expired:
        cancel.set()
    monkeypatch.setattr(native_macro.mp, "get_context", lambda *_: pytest.fail("worker started"))
    assert native_macro.replay_macro(recording(), cancellation=cancel,
        deadline_at=monotonic() - 1 if expired else monotonic() + 30)["status"] == ("timed_out" if expired else "cancelled")


def test_active_cancellation_reaps_worker():
    cancel = Event()
    before = {child.pid for child in native_macro.mp.active_children()}
    timer = Timer(0.02, cancel.set)
    timer.start()
    try:
        assert native_macro.replay_macro(recording(), cancellation=cancel,
            deadline_at=monotonic() + 30)["status"] == "cancelled"
    finally:
        timer.cancel()
    assert {child.pid for child in native_macro.mp.active_children()} == before
