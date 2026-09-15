"""Offline target-basic binding; native peers algorithm/source files stay intact."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import hashlib
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import threading
from types import FunctionType, SimpleNamespace

import pandas as pd
import pytest

from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _dependency(ticker="688041.SH", industry="集成电路", name="修订公司"):
    return {"0_basic": {"ticker": ticker, "source": "bound-fixture-revision", "fallback": False,
                         "data": {"code": ticker.split(".")[0], "name": name,
                                  "industry": industry, "pe_ttm": 18.5, "pb": 2.1,
                                  "roe": 13.0, "nested": {"retained": [1]}}}}


def _configure_native(runtime, monkeypatch):
    peers = importlib.import_module("fetch_peers")
    basic_calls, board_calls, financial_calls = [], [], []

    def old_basic(ti):
        basic_calls.append(ti.full)
        return {"code": ti.code, "name": "旧资料", "industry": "旧行业", "pe_ttm": 3.0, "pb": 1.0}

    def board(*, symbol):
        board_calls.append(symbol)
        return pd.DataFrame([
            {"代码": "688041", "名称": "甲", "总市值": 300, "市盈率-动态": 18, "市净率": 2},
            {"代码": "600519", "名称": "乙", "总市值": 200, "市盈率-动态": 20, "市净率": 3},
            {"代码": "002415", "名称": "丙", "总市值": 100, "市盈率-动态": 22, "市净率": 4},
        ])

    def financial(*, symbol):
        financial_calls.append(symbol)
        return pd.DataFrame([{"REPORT_DATE_NAME": "2025年报", "ROEJQ": 12.0}])

    monkeypatch.setattr(peers.ds, "fetch_basic", old_basic)
    monkeypatch.setattr(peers.ak, "stock_board_industry_cons_em", board)
    monkeypatch.setattr(peers.ak, "stock_financial_analysis_indicator_em", financial)
    monkeypatch.setenv("UZI_DISABLE_GLOBAL_PEERS", "1")
    return peers, basic_calls, board_calls, financial_calls


@pytest.fixture
def offline(tmp_path, monkeypatch):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    import requests

    def no_http(*args, **kwargs):
        raise AssertionError("offline test attempted HTTP")

    monkeypatch.setattr(requests.sessions.Session, "request", no_http)
    configured = _configure_native(runtime, monkeypatch)
    return runtime, *configured


def test_actual_native_main_uses_revised_basic_not_old_basic(offline):
    runtime, peers, basic_calls, board_calls, financial_calls = offline
    dependency = _dependency()
    original_digest = _digest(dependency)
    original_main, original_ds, original_fetch = peers.main, peers.ds, peers.ds.fetch_basic
    output = runtime.collect("4_peers", ticker="688041", dependencies=dependency)
    assert output["data"]["industry"] == "集成电路"
    assert output["data"]["self"] == dependency["0_basic"]["data"]
    assert basic_calls == []
    assert board_calls == ["集成电路"]
    assert set(financial_calls) == {"688041.SH", "600519.SH", "002415.SZ"}
    assert output["data"]["peer_comparison"][0] == {"name": "PE (越低越好)", "self": 18.5, "peer": 20.0}
    assert peers.main is original_main and peers.ds is original_ds and peers.ds.fetch_basic is original_fetch
    output["data"]["self"]["nested"]["retained"].append(2)
    assert _digest(dependency) == original_digest


@pytest.mark.parametrize("dependency", [None, {}, {"unknown_dependency": {"data": {"industry": "ignored"}}}])
def test_absent_basic_dependency_preserves_original_path(offline, dependency):
    runtime, peers, basic_calls, board_calls, _ = offline
    original = peers.main
    output = runtime.collect("4_peers", ticker="688041.SH", dependencies=dependency)
    assert output["data"]["industry"] == "旧行业"
    assert basic_calls == ["688041.SH"] and board_calls == ["旧行业"]
    assert peers.main is original


@pytest.mark.parametrize("bad", [None, [], {}, {"data": None}, {"data": []}, {"data": "not-a-mapping"}])
def test_explicit_malformed_basic_never_silently_reads_old_cache(offline, bad):
    runtime, _, basic_calls, board_calls, _ = offline
    with pytest.raises(ValueError, match="^peers_basic_dependency_invalid$"):
        runtime.collect("4_peers", ticker="688041.SH", dependencies={"0_basic": bad})
    assert basic_calls == [] and board_calls == []


@pytest.mark.parametrize("identity", [{"ticker": "600519.SH"}, {"data": {"code": "600519"}}])
def test_mismatched_basic_identity_rejected_before_source(offline, identity):
    runtime, _, basic_calls, board_calls, _ = offline
    dependency = _dependency()
    dependency["0_basic"].update(identity)
    with pytest.raises(ValueError, match="^peers_basic_dependency_ticker_mismatch$"):
        runtime.collect("4_peers", ticker="688041.SH", dependencies=dependency)
    assert basic_calls == [] and board_calls == []


def test_explicit_empty_basic_stays_empty_not_backfilled(offline):
    runtime, _, basic_calls, board_calls, _ = offline
    output = runtime.collect("4_peers", ticker="688041.SH", dependencies={"0_basic": {"data": {}}})
    assert output["data"]["self"] == {}
    assert output["data"]["industry"] == ""
    assert basic_calls == [] and board_calls == []


def _proxy_probe(ticker, marker="default", *, keyword="kwdefault"):
    # ds and parse_ticker are supplied in this native-shaped function's globals.
    target = ds.fetch_basic(parse_ticker(ticker))  # noqa: F821
    other = ds.fetch_basic(parse_ticker("600519.SH"))  # noqa: F821
    target["nested"]["retained"].append(9)
    again = ds.fetch_basic(parse_ticker(ticker))  # noqa: F821
    return {"data": {"target": target, "again": again, "other": other,
                     "token": ds.token, "marker": marker, "keyword": keyword,  # noqa: F821
                     "module_name": __name__}}


def test_only_target_basic_intercepted_and_function_metadata_preserved(offline, monkeypatch):
    runtime, peers, basic_calls, _, _ = offline
    dependency = _dependency()
    original_digest = _digest(dependency)
    data_source = SimpleNamespace(fetch_basic=peers.ds.fetch_basic, token=object())
    globals_map = dict(_proxy_probe.__globals__, ds=data_source, parse_ticker=peers.parse_ticker)
    probe = FunctionType(_proxy_probe.__code__, globals_map, "native_probe", _proxy_probe.__defaults__, _proxy_probe.__closure__)
    probe.__kwdefaults__ = _proxy_probe.__kwdefaults__.copy()
    probe.__module__ = "pinned.native.identity"
    monkeypatch.setattr(peers, "main", probe)
    original_globals = probe.__globals__
    original_fetch = data_source.fetch_basic
    output = runtime.collect("4_peers", ticker="688041.SH", dependencies=dependency)["data"]
    assert output["target"]["industry"] == "集成电路"
    assert output["again"]["nested"]["retained"] == [1]
    assert output["other"]["industry"] == "旧行业" and basic_calls == ["600519.SH"]
    assert output["token"] is data_source.token
    assert output["marker"] == "default" and output["keyword"] == "kwdefault"
    assert output["module_name"] == _proxy_probe.__module__
    assert probe.__module__ == "pinned.native.identity" and probe.__globals__ is original_globals
    assert data_source.fetch_basic is original_fetch and _digest(dependency) == original_digest


@pytest.mark.parametrize("ticker,code", [("00700.HK", "700"), ("BP.L", "BP"), ("7203.T", "7203"), ("AAPL", "AAPL")])
def test_native_canonical_identity_accepts_its_bare_listing_code(offline, monkeypatch, ticker, code):
    runtime, peers, basic_calls, _, _ = offline
    closure_marker = object()

    def probe(value, positional_default="kept", *, keyword_default="also-kept"):
        return {"data": ds.fetch_basic(parse_ticker(value)),  # noqa: F821
                "closure_marker": closure_marker,
                "defaults": (positional_default, keyword_default)}

    main = FunctionType(probe.__code__, {**probe.__globals__, "ds": peers.ds, "parse_ticker": peers.parse_ticker},
                        probe.__name__, probe.__defaults__, probe.__closure__)
    main.__kwdefaults__ = probe.__kwdefaults__
    monkeypatch.setattr(peers, "main", main)
    dependency = _dependency(ticker)
    dependency["0_basic"]["data"]["code"] = code
    result = runtime.collect("4_peers", ticker=ticker, dependencies=dependency)
    assert result["data"] == dependency["0_basic"]["data"] and basic_calls == []
    assert result["closure_marker"] is closure_marker
    assert result["defaults"] == ("kept", "also-kept")


def test_actual_native_exception_leaves_shared_objects_and_next_call_unchanged(offline, monkeypatch):
    runtime, peers, _, _, _ = offline
    original_main, original_ds, original_fetch = peers.main, peers.ds, peers.ds.fetch_basic
    dependency = _dependency()
    before = _digest(dependency)

    def fail_after_basic(*args, **kwargs):
        raise RuntimeError("offline-native-failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(peers, "_attach_global_peers", fail_after_basic)
        with pytest.raises(RuntimeError, match="offline-native-failure"):
            runtime.collect("4_peers", ticker="688041.SH", dependencies=dependency)
    assert peers.main is original_main and peers.ds is original_ds and peers.ds.fetch_basic is original_fetch
    assert _digest(dependency) == before
    output = runtime.collect("4_peers", ticker="688041.SH")
    assert output["data"]["industry"] == "旧行业"


@pytest.mark.parametrize("second_ticker", ["600519.SH", "688041.SH"])
def test_parallel_native_calls_have_private_basic_even_for_same_ticker(offline, monkeypatch, second_ticker):
    runtime, peers, basic_calls, _, _ = offline
    barrier = threading.Barrier(2)
    native_parse = peers.parse_ticker

    def synchronized_parse(value):
        result = native_parse(value)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(peers, "parse_ticker", synchronized_parse)
    first, second = _dependency(industry="行业甲"), _dependency(second_ticker, "行业乙")
    original_hashes = [_digest(first), _digest(second)]
    original_ds, original_fetch = peers.ds, peers.ds.fetch_basic
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runtime.collect, "4_peers", ticker=ticker, dependencies=dependency)
                   for ticker, dependency in [("688041.SH", first), (second_ticker, second)]]
        outputs = [future.result(timeout=10) for future in futures]
    assert [value["data"]["industry"] for value in outputs] == ["行业甲", "行业乙"]
    assert basic_calls == []
    assert [_digest(first), _digest(second)] == original_hashes
    assert peers.ds is original_ds and peers.ds.fetch_basic is original_fetch


def _spawn_offline_native(workspace, ticker, industry):
    runtime = host.Uzi392FetcherRuntime(workspace_root=Path(workspace))
    with pytest.MonkeyPatch.context() as monkeypatch:
        peers, calls, _, _ = _configure_native(runtime, monkeypatch)
        original_ds, original_fetch = peers.ds, peers.ds.fetch_basic
        output = runtime.collect("4_peers", ticker=ticker, dependencies=_dependency(ticker, industry))
        return {"pid": os.getpid(), "industry": output["data"]["industry"], "old_basic_calls": calls,
                "shared_unchanged": peers.ds is original_ds and peers.ds.fetch_basic is original_fetch}


def test_fresh_spawned_processes_bind_their_own_supplied_basic(tmp_path):
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = [pool.submit(_spawn_offline_native, str(tmp_path / str(index)), ticker, industry)
                   for index, (ticker, industry) in enumerate([("688041.SH", "行业甲"), ("600519.SH", "行业乙")])]
        results = [future.result(timeout=30) for future in futures]
    assert [row["industry"] for row in results] == ["行业甲", "行业乙"]
    assert all(row["pid"] != os.getpid() and row["shared_unchanged"] and row["old_basic_calls"] == [] for row in results)
