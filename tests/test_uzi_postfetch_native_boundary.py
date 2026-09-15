"""Compare the real pinned post-fetch function with the host recovery entry."""

from copy import deepcopy
from importlib import import_module
from time import monotonic
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.tools import uzi_data_recovery as recovery
from domain_packs.finance_ashare.tools import uzi_native_postfetch as postfetch
from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime


@pytest.fixture
def native(tmp_path, monkeypatch):
    Uzi392FetcherRuntime(workspace_root=tmp_path)._initialize_import_boundary()
    mx = import_module("lib.mx_api")
    search = import_module("lib.web_search")
    calls = []
    monkeypatch.setattr(mx, "MXClient", lambda: SimpleNamespace(available=False))

    def fetch(query, **kwargs):
        calls.append(query)
        return [{"title": "原始来源标题", "body": "实际返回的研究摘要，包含有依据的公司业务信息。", "url": "https://example.org/source"}]

    monkeypatch.setattr(search, "search", fetch)
    monkeypatch.setattr(recovery.UziWebSearchAdapter, "search", lambda self, **kw: {
        "results": fetch(kw["query"]), "source_trace": [],
    })
    return import_module("lib.pipeline.score_fns"), calls


@pytest.mark.parametrize("dimension,snapshot_type,data", [
    ("3_macro", "macro", {"rate_cycle": "—", "fx_trend": "人民币汇率稳定"}),
    ("7_industry", "industry", {"growth": "—", "cninfo_metrics": {"industry_pe_weighted": 30}}),
    ("9_futures", "futures", {"linked_contract": "无直接关联", "contract_trend": "已知趋势"}),
    ("13_policy", "policy", {"snippets": {"subsidy": [{"title": "已采补贴公告"}]}}),
    ("15_events", "events", {"recent_news": [{"title": "已采新闻"}]}),
])
def test_host_qualitative_selection_matches_native(native, dimension, snapshot_type, data):
    module, _calls = native
    basic = {"name": "海光信息", "industry": "半导体"}
    envelope = {"ticker": "688041.SH", "data": deepcopy(data), "source": "recorded"}
    raw = {"dimensions": {"0_basic": {"data": basic}, dimension: deepcopy(envelope)}}
    module._autofill_qualitative_via_mx(raw, "688041.SH")
    expected = raw["dimensions"][dimension]["data"]
    snapshots = [
        {"snapshot_type": "market", "symbol": "688041.SH", "facts": {**basic, "_uzi_raw_result": {"data": basic}}},
        {"snapshot_type": snapshot_type, "symbol": "688041.SH", "facts": {**deepcopy(data), "_uzi_raw_result": deepcopy(envelope)}},
    ]
    recovery.recover_qualitative_snapshots(snapshots, query="显式补采", symbols=["688041.SH"],
                                         profile="deep", settings=SimpleNamespace(mx_api_key=""))
    actual = snapshots[1]["facts"]
    # Compare actual fields, not host diagnostic metadata.
    for field in set(data) | (set(expected) - {"_autofill", "_autofill_failed"}):
        assert actual.get(field) == expected.get(field)


class Sources:
    mx_available = False
    browser_enabled = True

    def __init__(self, html="", search_rows=None):
        self.html = html
        self.rows = search_rows or []
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append(("search", query, kwargs))
        return deepcopy(self.rows)

    def browser_html(self, source, url, **kwargs):
        self.calls.append((source, url, kwargs))
        return self.html


@pytest.mark.parametrize("profile", ["lite", "medium", "deep"])
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("html", ["", '''<a href="/S/SZ000858">五粮液</a>
<div>主营业务：实际公司主营业务和成本信息</div>
<a class="announcement-title">真实业绩披露公告原文</a>
<script>{"title":"来自实际页面的讨论标题"}</script>
<a href="/release/123">国家统计局发布当月经济统计数据</a>
<h3><a>行业景气与增速调查结果</a></h3>
<div class="lemma-summary">实际公司简介与业务介绍</div>
<a title="政策部门发布新的政策说明原文">公告</a>
<script>{"title":"老师推荐消息的原文记录"},{"name":"公开组合测试","total_gain":12.5}</script>'''])
def test_browser_actual_pinned_parsers_and_results_match(native, monkeypatch, profile, html, force):
    module = import_module("lib.playwright_fallback")
    profiles = import_module("lib.analysis_profile")
    selected = profiles.get_profile(profile)
    monkeypatch.setattr(profiles, "get_profile", lambda depth=None: selected)
    monkeypatch.setattr(module, "ensure_playwright_installed", lambda auto: True)
    monkeypatch.setattr(module, "_filter_dims_by_network", lambda dims: (dims, []))
    monkeypatch.setattr(module, "fetch_url", lambda *args, **kwargs: html)
    monkeypatch.setenv("UZI_PLAYWRIGHT_ENABLE", "1")
    monkeypatch.setenv("UZI_PLAYWRIGHT_FORCE", "1" if force else "0")
    original = {"ticker": "688041.SH", "dimensions": {key: {
        "data": {}, "source": "recorded", "error": "original failure", "fallback": True,
    } for key in module.DIM_STRATEGIES}}
    original["dimensions"]["0_basic"] = {"data": {"name": "海光信息", "industry": "半导体"}}
    expected = deepcopy(original)
    expected_summary = module.autofill_via_playwright(expected, "688041.SH")
    sources = Sources(html=html)
    actual = postfetch.run_native_postfetch(original, ticker="688041.SH", dimensions=set(module.DIM_STRATEGIES),
        profile=profile, sources=sources, deadline_at=monotonic() + 60, qualitative=False, force_browser=force)
    assert actual["raw"] == expected
    assert actual["playwright_recovery"] == expected_summary
    assert len(sources.calls) == expected_summary["attempted"]
    assert all(row["error"] == "original failure" for key, row in actual["raw"]["dimensions"].items() if key != "0_basic")
    assert original["dimensions"]["4_peers"]["data"] == {}


def test_active_recovery_does_not_disable_public_browser_sources_with_sensitive_flag(native, monkeypatch):
    calls = []

    def recover(*args, **kwargs):
        calls.append(kwargs)
        return {"delegated_to_per_source_permissions": True}

    monkeypatch.setattr(postfetch, "recover_snapshot_postfetch", recover)
    result = recovery.recover_playwright_snapshots([], query="显式公共来源补采", symbols=["688041.SH"],
        profile="medium", settings=SimpleNamespace(browser_sources_enabled=True, sensitive_sources_enabled=False))
    assert result == {"delegated_to_per_source_permissions": True}
    assert calls[0]["force_browser"] is True


def test_scoped_native_qualitative_does_not_fetch_or_write_other_slots(native):
    sources = Sources(search_rows=[{"title": "原文业务披露", "body": "主要成本构成真实数据", "url": "https://example.test/1"}])
    original = {"dimensions": {"0_basic": {"data": {"name": "海光信息", "industry": "半导体"}},
                               "7_industry": {"data": {}, "error": "keep"},
                               "8_materials": {"data": {"core_material": "—"}, "source": "recorded", "error": "keep original separately"}}}
    output = postfetch.run_native_postfetch(original, ticker="688041.SH", dimensions={"8_materials"},
        profile="deep", sources=sources, deadline_at=monotonic() + 60, browser=False)
    assert len(sources.calls) == 1
    assert sources.calls[0][1] == "海光信息 688041 主营业务 主要原材料 成本构成"
    assert output["raw"]["dimensions"]["7_industry"] == original["dimensions"]["7_industry"]
    assert set(output["raw"]["dimensions"]) == set(original["dimensions"])
    assert set(output["observations"][0]) >= {"request", "response", "observed_at", "dimension"}


@pytest.mark.parametrize("cancelled,expired", [(True, False), (False, True)])
def test_cancel_or_deadline_does_not_call_sources(native, cancelled, expired):
    sources = Sources()
    original = {"dimensions": {"7_industry": {"data": {}}}}
    output = postfetch.run_native_postfetch(original, ticker="688041.SH", dimensions={"7_industry"}, profile="deep",
        sources=sources, deadline_at=monotonic() + (-1 if expired else 60),
        cancellation=SimpleNamespace(is_set=lambda: cancelled))
    assert sources.calls == []
    assert output["raw"] == original
    assert output["stopped_reason"] == ("collection_cancelled" if cancelled else "research_deadline_exceeded")


def test_empty_native_browser_result_is_failure_not_success(native, monkeypatch):
    monkeypatch.setattr(import_module("lib.playwright_fallback"), "_filter_dims_by_network", lambda dims: (dims, []))
    sources = Sources()
    result = postfetch.run_native_postfetch({"dimensions": {"4_peers": {"data": {}}}}, ticker="688041.SH",
        dimensions={"4_peers"}, profile="deep", sources=sources, deadline_at=monotonic() + 60, qualitative=False)
    assert result["attempts"] == [{"dimension": "4_peers", "stage": "playwright_recovery", "status": "data_gap"}]


def test_default_search_uses_native_cache_budget_and_unrestricted_query(tmp_path, monkeypatch):
    Uzi392FetcherRuntime(workspace_root=tmp_path)._initialize_import_boundary()
    web = import_module("lib.web_search")
    monkeypatch.setattr(web, "_BUDGET_STATE", {"used": 0, "skipped": 0})
    monkeypatch.setenv("UZI_DDG_BUDGET", "2")
    calls = []
    def search(self, **kwargs):
        calls.append(kwargs)
        return {"results": [{"title": "真实来源", "body": "实际来源内容", "url": "https://example.test/record"}]}
    monkeypatch.setattr(postfetch.UziWebSearchAdapter, "search", search)
    source = postfetch.PostfetchSources(SimpleNamespace(mx_api_key=None))
    first = source.search("实际搜索查询", max_results=3, timeout=4, profile_budget=1)
    assert source.search("实际搜索查询", max_results=3, timeout=4, profile_budget=1) == first
    assert source.search("下一条查询", max_results=3, timeout=4, profile_budget=1) == []
    assert len(calls) == 1 and calls[0]["trusted"] is False
    assert calls[0]["timeout_seconds"] == 4 and calls[0]["query"] == "实际搜索查询"
    assert {key: source.search_budget()[key] for key in ("used", "skipped")} == {"used": 1, "skipped": 1}
    assert web.get_budget_state() == {"used": 0, "skipped": 0}


def test_default_mx_keeps_native_response_cache_and_request_deadline(tmp_path, monkeypatch):
    Uzi392FetcherRuntime(workspace_root=tmp_path)._initialize_import_boundary()
    mx = import_module("lib.mx_api")
    calls = []
    payload = {"data": {"data": {"entityName": "真实来源返回的业务说明"}}}
    def request(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: deepcopy(payload))
    monkeypatch.setattr(mx.requests, "post", request)
    source = postfetch.PostfetchSources(SimpleNamespace(mx_api_key="offline-test-only"))
    assert source.mx_query("当次测试查询", timeout=4) == payload
    assert source.mx_query("当次测试查询", timeout=4) == payload
    assert len(calls) == 1
    assert 0 < calls[0]["timeout"] <= 4


@pytest.mark.parametrize("enabled,allowed,cookie", [(False, True, True), (True, False, True), (True, True, False)])
def test_native_browser_fetch_cannot_bypass_configured_source_policy(monkeypatch, enabled, allowed, cookie):
    settings = SimpleNamespace(browser_sources_enabled=enabled, sensitive_sources_enabled=True,
        sensitive_source_allowlist=["xueqiu"] if allowed else [], xueqiu_cookie="offline=test" if cookie else None)
    adapter = postfetch.UziBrowserAdapter(settings)
    with pytest.raises(PermissionError):
        adapter.fetch_native_html(source_id="xueqiu_f10", url="https://xueqiu.com/S/SH688041", timeout_seconds=1)


def test_native_browser_rejects_mismatched_source_url_before_launch():
    settings = SimpleNamespace(browser_sources_enabled=True, sensitive_sources_enabled=True,
        sensitive_source_allowlist=["xueqiu"], xueqiu_cookie="offline=test")
    with pytest.raises(ValueError, match="authorized source"):
        postfetch.UziBrowserAdapter(settings).fetch_native_html(
            source_id="xueqiu_f10", url="https://example.test/unauthorized", timeout_seconds=1)


def test_lite_zero_ddgs_budget_does_not_disable_native_mx(native):
    class MxSources(Sources):
        mx_available = True
        def mx_query(self, query, **kwargs):
            self.calls.append(("mx", query))
            return {"data": {"data": {"entityName": "源返回的真实材料与成本信息"}}}
    sources = MxSources()
    result = postfetch.run_native_postfetch({"dimensions": {"8_materials": {"data": {}}}},
        ticker="688041.SH", dimensions={"8_materials"}, profile="lite", sources=sources,
        deadline_at=monotonic() + 60, browser=False)
    assert result["raw"]["dimensions"]["8_materials"]["data"]["_autofill"]["source"] == "mx_api"
    assert len(sources.calls) == 1
