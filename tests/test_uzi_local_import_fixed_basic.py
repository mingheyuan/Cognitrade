"""Pinned local imports get a private source view, never shared module patches."""
import builtins
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib
import threading
from types import FunctionType, SimpleNamespace

import pandas as pd
import pytest

from tests.test_uzi_research_fixed_basic_dependency import dependency, digest
from tests.test_uzi_research_fixed_basic_dependency import offline as offline  # noqa: PLC0414


LOCAL_FETCHERS = {"8_materials": "fetch_materials", "15_events": "fetch_events"}


@pytest.fixture
def local(offline, monkeypatch):
    state = offline
    state.modules.update({key: importlib.import_module(name) for key, name in LOCAL_FETCHERS.items()})
    materials, events = state.modules["8_materials"], state.modules["15_events"]

    def futures(**kwargs):
        state.source_calls.append(("futures", deepcopy(kwargs)))
        return pd.DataFrame({"日期": ["2026-01-01", "2026-02-01", "2026-03-01"], "收盘价": [10, 12, 11]})

    def cninfo(url, **kwargs):
        assert url == "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        state.source_calls.append(("cninfo", deepcopy(kwargs)))
        return SimpleNamespace(status_code=200, json=lambda: {"announcements": [{
            "announcementTime": 0, "announcementTitle": "离线固定公司合同公告", "adjunctUrl": "offline.pdf",
        }]})

    def news(**kwargs):
        state.source_calls.append(("em_news", deepcopy(kwargs)))
        return pd.DataFrame()

    def hk_announcements(code, **kwargs):
        state.source_calls.append(("hk_announcements", {"code": code, **kwargs}))
        return [{"date": "2026-01-01", "title": "离线香港公告", "url": "https://offline.example/hk", "source": "offline-hk"}]

    monkeypatch.setattr(materials.ak, "futures_main_sina", futures)
    monkeypatch.setattr("requests.post", cninfo)
    monkeypatch.setattr(events.ak, "stock_news_em", news)
    monkeypatch.setattr(events, "web_search", state.modules["14_moat"].search)
    monkeypatch.setattr(events, "search_trusted", state.modules["14_moat"].search_trusted)
    monkeypatch.setattr(importlib.import_module("lib.hk_data_sources"), "fetch_hk_announcements_cached", hk_announcements)
    return state


@pytest.mark.parametrize("fetcher,ticker", [("8_materials", "688041.SH"), ("15_events", "688041.SH"), ("15_events", "00700.HK")])
def test_actual_local_import_main_uses_fixed_basic_without_global_ds(local, monkeypatch, fetcher, ticker):
    native = local.modules[fetcher]
    assert "ds" not in native.main.__globals__ and "_ds" not in native.main.__globals__
    original_main, original_builtins = native.main, native.main.__builtins__
    original_import = builtins.__import__
    ds = importlib.import_module("lib.data_sources")
    old_fetch = ds.fetch_basic
    fixed = dependency(ticker)
    before = digest(fixed)
    actual = local.runtime.collect(fetcher, ticker=ticker, dependencies=fixed)
    assert ticker not in local.calls
    if fetcher == "8_materials":
        assert actual["data"]["industry_resolved"] == "半导体"
        assert [args["symbol"] for source, args in local.source_calls if source == "futures"] == ["CU0", "AU0", "AG0"]
    else:
        assert local.queries and all("新固定公司" in query for query, _ in local.queries)
        if ticker.endswith(".HK"):
            assert ("hk_announcements", {"code": "00700", "limit": 20}) in local.source_calls
        else:
            assert local.news_calls[0]["stock_name"] == "新固定公司"
            assert any(source == "cninfo" and args["data"]["stock"] == "688041" for source, args in local.source_calls)

    def reference_basic(ti, *args, **kwargs):
        return deepcopy(fixed["0_basic"]["data"]) if ti.full == ticker else old_fetch(ti, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(ds, "fetch_basic", reference_basic)
        expected = original_main(ticker)
    assert actual == expected
    assert native.main is original_main and native.main.__builtins__ is original_builtins
    assert builtins.__import__ is original_import and ds.fetch_basic is old_fetch
    assert digest(fixed) == before


@pytest.mark.parametrize("fetcher", LOCAL_FETCHERS)
@pytest.mark.parametrize("fixed", [None, {}, {"other": {}}])
def test_absent_fixed_basic_keeps_local_native_path(local, fetcher, fixed):
    native = local.modules[fetcher]
    actual = local.runtime.collect(fetcher, ticker="688041.SH", dependencies=fixed)
    assert "688041.SH" in local.calls
    assert actual == native.main("688041.SH")


@pytest.mark.parametrize("fetcher", LOCAL_FETCHERS)
@pytest.mark.parametrize("kind", ["empty", "null-industry"])
def test_explicit_missing_basic_fields_preserve_native_missing_branch(local, fetcher, kind):
    fixed = {"0_basic": {"data": {}}} if kind == "empty" else dependency(industry=None)
    result = local.runtime.collect(fetcher, ticker="688041.SH", dependencies=fixed)
    assert local.calls == []
    if fetcher == "8_materials":
        assert result["data"]["industry_resolved"] == "综合"
        assert result["data"]["materials_detail"] == []
        assert not local.source_calls
    else:
        expected = "688041" if kind == "empty" else "新固定公司"
        assert local.news_calls[0]["stock_name"] == expected


def test_materials_hk_ticker_branch_is_not_rewritten_to_an_industry(local):
    # Pinned materials only recognizes its own A-code grammar. Keep that
    # limitation: a fixed HK basic does not authorize changing native args.
    actual = local.runtime.collect("8_materials", ticker="00700.HK", dependencies=dependency("00700.HK"))
    assert actual == local.modules["8_materials"].main("00700.HK")
    assert actual["data"]["industry_resolved"] == "00700.HK"
    assert local.calls == local.source_calls == []


@pytest.mark.parametrize("fetcher", LOCAL_FETCHERS)
@pytest.mark.parametrize("bad", [None, {"data": []}, {"data": {} , "ticker": "600519.SH"}])
def test_local_import_bad_binding_refuses_before_main_sources(local, fetcher, bad):
    reason = "fixed_basic_dependency_ticker_mismatch" if isinstance(bad, dict) and "ticker" in bad else "fixed_basic_dependency_invalid"
    with pytest.raises(ValueError, match=f"^{reason}$"):
        local.runtime.collect(fetcher, ticker="688041.SH", dependencies={"0_basic": bad})
    assert local.calls == local.source_calls == local.queries == []


def _with_builtins(function, mapping):
    clone = FunctionType(function.__code__, {**function.__globals__, "__builtins__": mapping},
                         function.__name__, function.__defaults__, function.__closure__)
    clone.__kwdefaults__ = function.__kwdefaults__
    return clone


@pytest.mark.parametrize("fetcher,ticker", [("8_materials", "688041.SH"), ("15_events", "688041.SH"), ("15_events", "00700.HK")])
def test_parallel_actual_local_imports_have_private_same_symbol_revisions(local, monkeypatch, fetcher, ticker):
    native = local.modules[fetcher]
    original = native.main
    original_import = original.__builtins__["__import__"]
    barrier = threading.Barrier(2)

    def synchronized_import(name, globals=None, locals=None, fromlist=(), level=0):
        result = original_import(name, globals, locals, fromlist, level)
        if name == "lib" and "data_sources" in fromlist and level == 0:
            barrier.wait(timeout=5)
        return result

    clone = _with_builtins(original, {**original.__builtins__, "__import__": synchronized_import})
    monkeypatch.setattr(native, "main", clone)
    inputs = [dependency(ticker, name="固定甲公司", industry="半导体"),
              dependency(ticker, name="固定乙公司", industry="钢铁")]
    hashes = list(map(digest, inputs))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(local.runtime.collect, fetcher, ticker=ticker, dependencies=fixed) for fixed in inputs]
        results = [future.result(timeout=10) for future in futures]
    assert local.calls == []
    if fetcher == "8_materials":
        assert [row["data"]["industry_resolved"] for row in results] == ["半导体", "钢铁"]
    else:
        timelines = [" ".join(row["data"]["event_timeline"]) for row in results]
        assert "固定甲公司" in timelines[0] and "固定乙公司" in timelines[1]
        assert "固定乙公司" not in timelines[0] and "固定甲公司" not in timelines[1]
    assert list(map(digest, inputs)) == hashes
    assert native.main is clone and original.__builtins__["__import__"] is original_import


def test_private_import_intercepts_only_absolute_lib_data_sources_and_keeps_helpers(local, monkeypatch):
    ds = importlib.import_module("lib.data_sources")
    router = importlib.import_module("lib.market_router")
    sentinel = {"source": [None, "opaque"], "error": {"native": True}, "fallback": "unchanged"}

    def helper(ticker):
        from lib import data_sources as source
        from lib.market_router import parse_ticker
        return source.fetch_basic(parse_ticker(ticker))

    def probe(ticker):
        from lib import data_sources as source, market_router
        import lib.data_sources as ordinary
        from . import data_sources as relative
        import math
        info = market_router.parse_ticker(ticker)
        first = source.fetch_basic(info)
        first["nested"]["values"].append(99)
        sentinel["data"] = {
            "first": first, "again": source.fetch_basic(info),
            "other": source.fetch_basic(market_router.parse_ticker("600519.SH")),
            "ordinary": ordinary.fetch_basic(info), "relative": relative.fetch_basic(info),
            "helper": helper(ticker), "router": market_router, "sqrt": math.sqrt(4),
        }
        return sentinel

    function = FunctionType(probe.__code__, {**probe.__globals__, "__package__": "lib"},
                            probe.__name__, probe.__defaults__, probe.__closure__)
    monkeypatch.setattr(local.modules["15_events"], "main", function)
    original_import, old_fetch = builtins.__import__, ds.fetch_basic
    fixed = dependency()
    before = digest(fixed)
    result = local.runtime.collect("15_events", ticker="688041.SH", dependencies=fixed)
    assert result is sentinel
    assert result["data"]["again"]["name"] == "新固定公司"
    assert result["data"]["again"]["nested"]["values"] == [1]
    assert result["data"]["other"]["name"] == "旧资料600519"
    assert all(result["data"][field]["name"] == "旧资料688041" for field in ("ordinary", "relative", "helper"))
    assert result["data"]["router"] is router and result["data"]["sqrt"] == 2
    assert result["source"] == [None, "opaque"] and result["error"] == {"native": True}
    assert digest(fixed) == before and ds.fetch_basic is old_fetch and builtins.__import__ is original_import


def test_native_hk_error_envelope_is_not_reclassified_by_binding(local, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("offline HK source unavailable")
    monkeypatch.setattr(importlib.import_module("lib.hk_data_sources"), "fetch_hk_announcements_cached", fail)
    result = local.runtime.collect("15_events", ticker="00700.HK", dependencies=dependency("00700.HK"))
    assert local.calls == []
    assert result["source"] == "hkexnews" and result["fallback"] is True
    assert result["data"] == {"_err": "RuntimeError: offline HK source unavailable"}
