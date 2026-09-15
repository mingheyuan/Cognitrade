"""Native moat computation over a bounded archive, not historical web search."""

from copy import deepcopy
from hashlib import sha256
from threading import Event, Timer
from time import monotonic

import pytest

from research_system.market_data import native_moat


def recording():
    text = (
        "离线甲公司公告正文。专利和核心技术来自自主研发。\n"
        "大客户认证周期较长，合作需经过认证。\n"
        "平台效应和用户生态仍处在建设过程中。\n"
        "市场份额处于追赶阶段，公司不是龙头。\n"
        "研发投入相关技术实力详见原文。"
    )
    return native_moat.archive_recording(
        symbol="600519.SH", basic={"name": "离线甲公司"},
        documents=[{
            "evidence_id": "fixed-document", "document_hash": sha256(text.encode()).hexdigest(),
            "text_hash": sha256(text.encode()).hexdigest(),
            "title": "离线甲公司公告", "url": "https://example.org/disclosure/1",
            "inline_content": text,
        }],
    ), text


def test_archive_responses_are_exact_distinct_spans_not_one_text_in_every_group():
    value, text = recording()
    assert value["basis"] == "bounded_pit_archive_search_not_historical_web_search"
    assert len(value["queries"]) == 5
    selected = []
    for query in value["queries"]:
        assert query["query"].startswith("离线甲公司 上市公司 ")
        assert query["untrusted"] == []
        assert query["trusted"]
        for row in query["trusted"]:
            assert text[row["start"]:row["end"]] == row["body"]
            assert row["evidence_id"] == "fixed-document"
            selected.append(row["body"])
    assert len(set(selected)) > 1


def test_real_spawn_replays_unmodified_native_main_without_parent_module_patches():
    value, _ = recording()
    unchanged = deepcopy(value)
    result = native_moat.replay_moat(value, deadline_at=monotonic() + 30)
    assert result["status"] == "completed"
    assert result["isolation_events"] == []
    assert result["source_sha256"] == native_moat.NATIVE_SOURCE_SHA256
    assert result["recording_hash"] == native_moat.canonical_hash(value)
    assert result["native_result"]["data"]["scores_available"] is True
    assert result["native_result"]["source"] == "web_search:ddgs + keyword scoring"
    assert result["actual_source"] == value["basis"]
    assert value == unchanged


def test_missing_archive_is_not_four_fabricated_neutral_scores():
    value = native_moat.archive_recording(symbol="600519.SH", basic={"name": "离线甲公司"}, documents=[])
    result = native_moat.replay_moat(value, deadline_at=monotonic() + 30)
    assert result["status"] == "completed"
    assert result["native_result"]["data"]["scores"] == {}
    assert result["native_result"]["data"]["scores_available"] is False


@pytest.mark.parametrize("expired", [False, True])
def test_cancel_or_expired_budget_never_starts_worker(monkeypatch, expired):
    cancellation = Event()
    if not expired:
        cancellation.set()
    monkeypatch.setattr(native_moat.mp, "get_context", lambda *_: pytest.fail("worker started"))
    result = native_moat.replay_moat(recording()[0], cancellation=cancellation,
                                   deadline_at=monotonic() - 1 if expired else monotonic() + 30)
    assert result["status"] == ("timed_out" if expired else "cancelled")


def test_archive_requires_exact_company_context_and_verified_text_hash():
    value, _ = recording()
    with pytest.raises(ValueError, match="company_name_required"):
        native_moat.archive_recording(symbol="600519.SH", basic={}, documents=[])
    with pytest.raises(ValueError, match="archive_text_hash_mismatch"):
        native_moat.archive_recording(symbol="600519.SH", basic=value["basic"], documents=[{
            "evidence_id": "x", "document_hash": "a" * 64, "text_hash": "0" * 64,
            "inline_content": "被修改的正文", "url": "https://example.org/a", "title": "离线甲公司",
        }])


def test_active_cancellation_reaps_the_actual_spawned_worker():
    cancellation = Event()
    before = {child.pid for child in native_moat.mp.active_children()}
    timer = Timer(0.02, cancellation.set)
    timer.start()
    try:
        result = native_moat.replay_moat(recording()[0], cancellation=cancellation,
                                       deadline_at=monotonic() + 30)
    finally:
        timer.cancel()
    assert result["status"] == "cancelled"
    assert {child.pid for child in native_moat.mp.active_children()} == before


def test_archive_topic_lookup_recovers_disclosed_customer_ecosystem_language():
    # Native web-search query wording need not occur verbatim in the PDF.
    # Preserve negative statements too; source retrieval must not optimize scores.
    text = "甲公司客户认证后仍容易替换，没有证明存在转换壁垒。\n甲公司生态和平台尚未形成网络优势。"
    value = native_moat.archive_recording(symbol="600519.SH", basic={"name": "甲公司"}, documents=[{
        "evidence_id": "customer-ecosystem", "document_hash": sha256(text.encode()).hexdigest(),
        "text_hash": sha256(text.encode()).hexdigest(), "title": "甲公司公告",
        "url": "https://example.org/2", "inline_content": text,
    }])
    by_key = {row["key"]: row for row in value["queries"]}
    for key in ("switching", "network"):
        assert by_key[key]["trusted"]
        assert value["selection"]["query_expansions"][key]
        assert by_key[key]["query"] == "甲公司 上市公司 " + native_moat.QUERIES[key]
        for row in by_key[key]["trusted"]:
            assert row["body"] == text[row["start"]:row["end"]]
    assert "容易替换" in by_key["switching"]["trusted"][0]["body"]
    assert "尚未形成" in by_key["network"]["trusted"][0]["body"]
    assert not by_key["scale"]["trusted"]


def test_overlapping_windows_do_not_duplicate_one_archived_paragraph():
    text = "甲公司" + "原文。" * 70 + "平台效应" + "原文。" * 76 + "生态" + "原文。" * 76 + "用户"
    value = native_moat.archive_recording(symbol="600519.SH", basic={"name": "甲公司"}, documents=[{
        "evidence_id": "long-paragraph", "document_hash": sha256(text.encode()).hexdigest(),
        "text_hash": sha256(text.encode()).hexdigest(), "title": "甲公司公告",
        "url": "https://example.org/3", "inline_content": text,
    }])
    network = next(row for row in value["queries"] if row["key"] == "network")["trusted"]
    for index, left in enumerate(network):
        for right in network[index + 1:]:
            assert left["end"] <= right["start"] or right["end"] <= left["start"]
