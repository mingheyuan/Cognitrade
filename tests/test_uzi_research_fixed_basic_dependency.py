"""Actual pinned research fetchers receive only their own fixed basic revision."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import importlib
import json
import threading
from types import FunctionType, SimpleNamespace

import pandas as pd
import pytest

from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host


FETCHERS = {
    "10_valuation": "fetch_valuation", "14_moat": "fetch_moat", "16_lhb": "fetch_lhb",
    "17_sentiment": "fetch_sentiment", "18_trap": "fetch_trap_signals", "similar_stocks": "fetch_similar_stocks",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def dependency(ticker="688041.SH", *, name="新固定公司", industry="半导体"):
    return {"0_basic": {"ticker": ticker, "source": "offline-fixed-revision", "fallback": False,
                         "data": {"code": ticker.split(".")[0], "name": name, "full_name": name + "股份公司",
                                  "industry": industry, "price": 55, "pe_ttm": 40,
                                  "nested": {"values": [1]}}}}


@pytest.fixture
def offline(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("offline native binding tests must not call sources or models")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    modules = {key: importlib.import_module(name) for key, name in FETCHERS.items()}
    calls, queries, hot_calls, news_calls, source_calls = [], [], [], [], []

    def old_basic(ti, *args, **kwargs):
        calls.append(ti.full)
        return {"code": ti.code, "name": "旧资料" + ti.code, "industry": "钢铁", "price": 20,
                "pe_ttm": 10, "pb": 2, "market_cap": "40亿", "change_pct": 1,
                "nested": {"values": [0]}}

    def search(query, **kwargs):
        queries.append((query, deepcopy(kwargs)))
        return [{"title": query, "body": query + " 自主研发 平台 长期合作 看好", "url": "https://offline.example/source"},
                {"error": "offline-search-gap", "body": "not usable"}]

    def hot(name):
        hot_calls.append(name)
        return {"stock_name": name, "total_hits": 2, "error": "offline-hot-partial", "receipt": {"kept": True}}

    def news(**kwargs):
        news_calls.append(deepcopy(kwargs))
        return {"sources": {"offline": [{"title": kwargs["stock_name"] + " 看好", "body": "自主研发", "url": "https://offline.example/news"}]},
                "total_hits": 1, "sources_ok": 1, "error": "offline-news-partial", "receipt": {"kept": [None]}}

    def valuation_history(**kwargs):
        source_calls.append(("valuation_history", deepcopy(kwargs)))
        return pd.DataFrame({"value": [10, 20, 30]})

    def industry_pe(**kwargs):
        source_calls.append(("industry_pe", deepcopy(kwargs)))
        # Offline source-shaped fixture, not asserted market data. The actual
        # pinned resolver must choose the right row; no local scoring copy.
        return pd.DataFrame([
            {"行业名称": "计算机、通信和其他电子设备制造业", "市盈率-加权平均": 30},
            {"行业名称": "黑色金属冶炼和压延加工业", "市盈率-加权平均": 10},
        ])

    def financials(ticker):
        source_calls.append(("financials", {"ticker": ticker}))
        return {"data": {"net_profit_history": []}, "source": "offline-gap", "fallback": True}

    def lhb_recent(ti, **kwargs):
        source_calls.append(("lhb_recent", {"ticker": ti.full, **kwargs}))
        return [{"证券代码": ti.full, "营业部名称": "机构专用", "买入金额": 100, "卖出金额": 20}]

    def lhb_sector(**kwargs):
        source_calls.append(("lhb_sector", deepcopy(kwargs)))
        return pd.DataFrame([{"代码": "600000", "说明": "offline all-market fixture"}])

    monkeypatch.setattr(modules["14_moat"].ds, "fetch_basic", old_basic)
    monkeypatch.setattr(modules["14_moat"], "search", search)
    monkeypatch.setattr(modules["14_moat"], "search_trusted", search)
    monkeypatch.setattr(modules["17_sentiment"], "search", search)
    monkeypatch.setattr(modules["18_trap"], "search", search)
    monkeypatch.setattr(importlib.import_module("lib.hottrend"), "get_hot_mentions", hot)
    monkeypatch.setattr(importlib.import_module("lib.news_providers"), "get_news_multi_source", news)
    monkeypatch.setattr(modules["10_valuation"].ak, "stock_zh_valuation_baidu", valuation_history)
    monkeypatch.setattr(modules["10_valuation"].ak, "stock_industry_pe_ratio_cninfo", industry_pe)
    monkeypatch.setattr(importlib.import_module("fetch_financials"), "main", financials)
    monkeypatch.setattr(importlib.import_module("lib.mx_api"), "MXClient", lambda: SimpleNamespace(available=False))
    monkeypatch.setattr(modules["16_lhb"].ds, "fetch_lhb_recent", lhb_recent)
    monkeypatch.setattr(modules["16_lhb"].ak, "stock_lhb_stock_statistic_em", lhb_sector)
    return SimpleNamespace(runtime=runtime, modules=modules, calls=calls, queries=queries,
                           hot_calls=hot_calls, news_calls=news_calls, source_calls=source_calls, old_basic=old_basic)


@pytest.mark.parametrize("fetcher", FETCHERS)
def test_actual_native_main_consumes_new_target_basic_and_preserves_whole_result(offline, monkeypatch, fetcher):
    state = offline
    native = state.modules[fetcher]
    fixed = dependency()
    before = digest(fixed)
    original_main, original_ds, original_fetch = native.main, native.ds, native.ds.fetch_basic
    options = {"top_n": 2}
    actual = state.runtime.collect(fetcher, ticker="688041.SH", dependencies=fixed, options=options)
    assert "688041.SH" not in state.calls
    if fetcher == "similar_stocks":
        assert actual["data"]["industry"] == "半导体"
        assert len(actual["data"]["similar_stocks"]) == 2
        assert all(row["name"].startswith("旧资料") for row in actual["data"]["similar_stocks"])
        assert state.calls  # native helper still fetched other stocks
    elif fetcher == "10_valuation":
        assert actual["data"]["industry_pe"] == "30.0"
        assert actual["data"]["pe"] == "40"
        assert any(source == "industry_pe" for source, _ in state.source_calls)
    elif fetcher == "16_lhb":
        assert actual["data"]["lhb_records"][0]["证券代码"] == "688041.SH"
        assert actual["data"]["sector_lhb_top50"][0]["代码"] == "600000"
        assert ("lhb_recent", {"ticker": "688041.SH", "days": 30}) in state.source_calls
    else:
        assert state.queries and all("新固定公司" in query for query, _ in state.queries)
        if fetcher == "17_sentiment":
            assert state.hot_calls == ["新固定公司"]
            assert state.news_calls[0]["stock_name"] == "新固定公司"
            assert actual["data"]["news_multi_source"]["error"] == "offline-news-partial"

    # Comparison uses the unchanged native function and identical offline leaf
    # responses, not a local reimplementation of scores or projections.
    def reference_basic(ti, *args, **kwargs):
        return deepcopy(fixed["0_basic"]["data"]) if ti.full == "688041.SH" else state.old_basic(ti, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(native.ds, "fetch_basic", reference_basic)
        expected = original_main("688041.SH", **({"top_n": 2} if fetcher == "similar_stocks" else {}))
    assert actual == expected
    assert native.main is original_main and native.ds is original_ds and native.ds.fetch_basic is original_fetch
    assert digest(fixed) == before


@pytest.mark.parametrize("fetcher", FETCHERS)
@pytest.mark.parametrize("fixed", [None, {}, {"unrelated": {"data": {"name": "not supplied"}}}])
def test_no_basic_dependency_is_the_unmodified_native_path(offline, fetcher, fixed):
    native = offline.modules[fetcher]
    actual = offline.runtime.collect(fetcher, ticker="688041.SH", dependencies=fixed, options={"top_n": 1})
    assert "688041.SH" in offline.calls
    expected = native.main("688041.SH", **({"top_n": 1} if fetcher == "similar_stocks" else {}))
    assert actual == expected


@pytest.mark.parametrize("fetcher", FETCHERS)
@pytest.mark.parametrize("bad", [None, {}, {"data": None}, {"data": []}])
def test_malformed_explicit_basic_refuses_before_any_source(offline, fetcher, bad):
    with pytest.raises(ValueError, match="^fixed_basic_dependency_invalid$"):
        offline.runtime.collect(fetcher, ticker="688041.SH", dependencies={"0_basic": bad})
    assert offline.calls == offline.queries == offline.hot_calls == offline.news_calls == []
    assert offline.source_calls == []


@pytest.mark.parametrize("fetcher", FETCHERS)
@pytest.mark.parametrize("where", ["ticker", "code", "same_code_foreign_venue"])
def test_foreign_basic_cannot_be_injected_into_target(offline, fetcher, where):
    fixed = dependency()
    if where == "same_code_foreign_venue":
        fixed["0_basic"]["ticker"] = "688041.SZ"
    elif where == "ticker":
        fixed["0_basic"]["ticker"] = "600519.SH"
    else:
        fixed["0_basic"]["data"]["code"] = "600519"
    with pytest.raises(ValueError, match="^fixed_basic_dependency_ticker_mismatch$"):
        offline.runtime.collect(fetcher, ticker="688041.SH", dependencies=fixed)
    assert offline.calls == offline.queries == []
    assert offline.source_calls == []


@pytest.mark.parametrize("fetcher", FETCHERS)
def test_empty_explicit_basic_never_falls_back_to_previous_basic(offline, fetcher):
    result = offline.runtime.collect(fetcher, ticker="688041.SH", dependencies={"0_basic": {"data": {}}})
    assert offline.calls == []
    if fetcher == "similar_stocks":
        assert result["fallback"] is True and result["data"]["industry"] == "未知"
        assert result["data"]["similar_stocks"] == []
    elif fetcher == "10_valuation":
        assert result["data"]["pe"] == "—"
        assert "basic.industry 缺失" in result["data"]["industry_pe_fallback_reason"]
    elif fetcher == "16_lhb":
        assert result["data"]["sector_lhb_top50"] == []
        assert not any(source == "lhb_sector" for source, _ in offline.source_calls)
        assert result["data"]["lhb_count_30d"] == 1  # target LHB source remains unchanged
    else:
        assert offline.queries and all("688041" in query for query, _ in offline.queries)
        assert all("旧资料" not in query for query, _ in offline.queries)


@pytest.mark.parametrize("fetcher", FETCHERS)
@pytest.mark.parametrize("second_ticker", ["600519.SH", "688041.SH"])
def test_actual_native_concurrent_calls_keep_same_ticker_revisions_private(offline, monkeypatch, fetcher, second_ticker):
    native = offline.modules[fetcher]
    original_parse = native.parse_ticker
    barrier = threading.Barrier(2)

    def synchronized_parse(value):
        parsed = original_parse(value)
        if value in {"688041.SH", second_ticker}:
            barrier.wait(timeout=5)
        return parsed

    monkeypatch.setattr(native, "parse_ticker", synchronized_parse)
    inputs = [dependency(name="固定甲公司", industry="半导体"),
              dependency(second_ticker, name="固定乙公司", industry="钢铁")]
    if fetcher == "16_lhb":
        inputs[1]["0_basic"]["data"]["industry"] = None
    before = list(map(digest, inputs))
    original_ds, original_fetch = native.ds, native.ds.fetch_basic
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(offline.runtime.collect, fetcher, ticker=ticker,
                               dependencies=fixed, options={"top_n": 1})
                   for ticker, fixed in zip(["688041.SH", second_ticker], inputs)]
        outputs = [future.result(timeout=10) for future in futures]
    if fetcher == "similar_stocks":
        assert [row["data"]["industry"] for row in outputs] == ["半导体", "钢铁"]
    elif fetcher == "10_valuation":
        assert [row["data"]["industry_pe"] for row in outputs] == ["30.0", "10.0"]
    elif fetcher == "16_lhb":
        assert outputs[0]["data"]["sector_lhb_top50"]
        assert outputs[1]["data"]["sector_lhb_top50"] == []
    elif fetcher == "14_moat":
        assert "固定甲公司" in outputs[0]["data"]["intangible"]
        assert "固定乙公司" in outputs[1]["data"]["intangible"]
    elif fetcher == "17_sentiment":
        assert [row["data"]["hot_trend_mentions"]["stock_name"] for row in outputs] == ["固定甲公司", "固定乙公司"]
    else:
        assert "固定甲公司" in outputs[0]["data"]["snippets"]["signal_1"][0]["body"]
        assert "固定乙公司" in outputs[1]["data"]["snippets"]["signal_1"][0]["body"]
    assert list(map(digest, inputs)) == before
    assert native.ds is original_ds and native.ds.fetch_basic is original_fetch


@pytest.mark.parametrize("fetcher", FETCHERS)
def test_proxy_deepcopies_target_only_and_returns_raw_envelope_without_projection(offline, monkeypatch, fetcher):
    native = offline.modules[fetcher]
    closure = {"source": ["opaque", None], "error": {"native": "unchanged"}, "fallback": "native-value"}

    def probe(ticker, top_n=4):
        first = ds.fetch_basic(parse_ticker(ticker))  # noqa: F821
        first["nested"]["values"].append(9)
        again = ds.fetch_basic(parse_ticker(ticker))  # noqa: F821
        other = ds.fetch_basic(parse_ticker("600519.SH"))  # noqa: F821
        closure["data"] = {"first": first, "again": again, "other": other, "top_n": top_n}
        return closure

    replacement = FunctionType(probe.__code__, {**probe.__globals__, "ds": native.ds,
                                              "parse_ticker": native.parse_ticker},
                               probe.__name__, probe.__defaults__, probe.__closure__)
    monkeypatch.setattr(native, "main", replacement)
    fixed = dependency()
    before = digest(fixed)
    result = offline.runtime.collect(fetcher, ticker="688041", dependencies=fixed, options={"top_n": 2})
    assert result is closure
    assert result["source"] == ["opaque", None] and result["error"] == {"native": "unchanged"}
    assert result["fallback"] == "native-value"
    assert result["data"]["again"]["nested"]["values"] == [1]
    assert result["data"]["other"]["name"] == "旧资料600519"
    assert result["data"]["top_n"] == (2 if fetcher == "similar_stocks" else 4)
    assert offline.calls == ["600519.SH"] and digest(fixed) == before


@pytest.mark.parametrize("fetcher", FETCHERS)
def test_actual_native_exception_does_not_leave_a_shared_proxy(offline, monkeypatch, fetcher):
    native = offline.modules[fetcher]
    original_main, original_ds, original_fetch = native.main, native.ds, native.ds.fetch_basic
    fixed = dependency()
    before = digest(fixed)

    def fail(*_args, **_kwargs):
        raise RuntimeError("offline-native-source-failure")

    with monkeypatch.context() as scoped:
        failure_leaf = ("parse_ticker" if fetcher in {"10_valuation", "16_lhb"}
                        else "_fetch_peer_basics" if fetcher == "similar_stocks" else "search")
        scoped.setattr(native, failure_leaf, fail)
        with pytest.raises(RuntimeError, match="offline-native-source-failure"):
            offline.runtime.collect(fetcher, ticker="688041.SH", dependencies=fixed)
    assert native.main is original_main and native.ds is original_ds and native.ds.fetch_basic is original_fetch
    assert digest(fixed) == before
    offline.calls.clear()
    offline.runtime.collect(fetcher, ticker="688041.SH")
    assert "688041.SH" in offline.calls


@pytest.mark.parametrize("fetcher", FETCHERS)
def test_unknown_native_entry_refuses_instead_of_guessing_a_binding(offline, monkeypatch, fetcher):
    def unrecognized(ticker, **kwargs):
        pytest.fail("unrecognized entry must not execute with an explicit dependency")

    monkeypatch.setattr(offline.modules[fetcher], "main", unrecognized)
    with pytest.raises(ValueError, match="^fixed_native_entry_invalid$"):
        offline.runtime.collect(fetcher, ticker="688041.SH", dependencies=dependency())
