"""Current-day fixed identity transport, no live provider/model execution."""
from __future__ import annotations

import hashlib
import importlib
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.tools import snapshot_collector as collector
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import Uzi392FetcherRuntime


def _fixed(symbol="688041.SH", industry="集成电路"):
    return {symbol: {"ticker": symbol, "data": {"code": symbol.split(".")[0], "name": "固定公司",
                     "industry": industry, "nested": []}, "source": "fixture:revision", "fallback": False}}


def _row(symbol, kind, industry):
    return {"symbol": symbol, "snapshot_type": kind, "quality_status": "available",
            "facts": {"availability": "available", "_uzi_raw_result": {
                "ticker": symbol, "data": {"industry": industry}, "source": "fixture:native", "fallback": False}}}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@pytest.fixture
def offline(tmp_path, monkeypatch):
    providers = SimpleNamespace(settings=SimpleNamespace(workspace_root=tmp_path / "native-workspace"))
    calls = []

    def run(query, symbol, snapshot_type, provider_registry=None, dependencies=None, research_profile=None,
            as_of_date=None, cutoff_at=None):
        calls.append({"symbol": symbol, "snapshot_type": snapshot_type,
                      "dependencies": deepcopy(dependencies), "profile": research_profile})
        industry = ((dependencies or {}).get("0_basic", {}).get("data") or {}).get("industry", "旧身份")
        return _row(symbol, snapshot_type, industry)

    monkeypatch.setattr(collector, "_run_snapshot_skill", run)
    monkeypatch.setattr(collector, "initialize_akshare_js_runtime", lambda: None)
    monkeypatch.setattr(collector, "_UZI_MINI_RACER_TIMEOUT_ACTIVE", SimpleNamespace(is_set=lambda: False))
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **kw: pytest.fail("offline HTTP forbidden"))
    return {"query": "显式当前日依赖重建", "symbols": ["688041.SH"],
            "snapshot_types": ["industry", "peers"], "research_profile": "medium",
            "provider_registry": providers, "cache_root": tmp_path / "host-cache"}, calls


def test_fixed_basic_reaches_real_serial_and_parallel_skill_calls_without_market(offline):
    kwargs, calls = offline
    fixed = _fixed()
    rows = collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=fixed)
    assert [row["snapshot_type"] for row in rows] == ["industry", "peers"]
    assert {call["snapshot_type"] for call in calls} == {"industry", "peers"}
    assert all(call["dependencies"] == {"0_basic": fixed["688041.SH"]} for call in calls)
    assert all(call["profile"] == "medium" for call in calls)
    assert all(row["facts"]["_uzi_raw_result"]["data"]["industry"] == "集成电路" for row in rows)


def test_old_numbered_snapshot_cache_never_replaces_fixed_identity_or_gets_overwritten(offline):
    kwargs, calls = offline
    for kind in kwargs["snapshot_types"]:
        collector._save_snapshot_cache(cache_root=kwargs["cache_root"], snapshot_type=kind,
            target="688041.SH", profile="medium", snapshot=_row("688041.SH", kind, "旧行业"))
    cached_paths = sorted(kwargs["cache_root"].rglob("*.json"))
    before = {str(path): path.read_bytes() for path in cached_paths}
    old = collector.collect_snapshots(**kwargs)
    assert calls == []
    assert all(row["facts"]["_uzi_raw_result"]["data"]["industry"] == "旧行业" for row in old)
    rebuilt = collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=_fixed())
    assert len(calls) == 2 and all(call["snapshot_type"] != "market" for call in calls)
    assert all(row["facts"]["_uzi_raw_result"]["data"]["industry"] == "集成电路" for row in rebuilt)
    assert {str(path): path.read_bytes() for path in kwargs["cache_root"].rglob("*.json")} == before


def test_fixed_requests_skip_both_host_cache_read_and_write(offline, monkeypatch):
    kwargs, calls = offline
    monkeypatch.setattr(collector, "_load_snapshot_cache", lambda **kw: pytest.fail("fixed identity read old host cache"))
    monkeypatch.setattr(collector, "_save_snapshot_cache", lambda **kw: pytest.fail("fixed identity overwrote unbound host cache"))
    collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=_fixed())
    assert len(calls) == 2


def test_each_fixed_dimension_and_symbol_receives_its_own_copy(offline, monkeypatch):
    kwargs, _ = offline
    kwargs["symbols"] = ["688041.SH", "600519.SH"]
    fixed = {**_fixed(), **_fixed("600519.SH", "白酒")}
    before = _hash(fixed)
    seen = []

    def mutating(query, symbol, snapshot_type, provider_registry=None, dependencies=None, research_profile=None):
        data = dependencies["0_basic"]["data"]
        seen.append((symbol, snapshot_type, data["industry"], list(data["nested"])))
        data["nested"].append(snapshot_type)
        return _row(symbol, snapshot_type, data["industry"])

    monkeypatch.setattr(collector, "_run_snapshot_skill", mutating)
    rows = collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=fixed, max_workers=2)
    assert len(rows) == 4 and len(seen) == 4
    assert all(industry == fixed[symbol]["data"]["industry"] and nested == []
               for symbol, kind, industry, nested in seen)
    assert _hash(fixed) == before
    assert [row["symbol"] for row in rows] == ["688041.SH", "688041.SH", "600519.SH", "600519.SH"]


def test_actual_skill_dispatch_passes_new_industry_and_dependency_to_runtime(offline, monkeypatch):
    kwargs, _ = offline
    # Use the real _run_snapshot_skill from its saved pre-fixture definition.
    monkeypatch.setattr(collector, "_run_snapshot_skill", _ORIGINAL_SKILL_RUN)
    calls = []

    def native(runtime, fetcher_id, **options):
        calls.append((fetcher_id, deepcopy(options)))
        return {"ticker": options["ticker"], "data": {"industry": options["industry"]},
                "source": "fixture:native", "fallback": False}

    monkeypatch.setattr(Uzi392FetcherRuntime, "collect", native)
    collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=_fixed())
    assert {key for key, options in calls} == {"7_industry", "4_peers"}
    assert all(options["industry"] == "集成电路" and options["dependencies"] == {"0_basic": _fixed()["688041.SH"]}
               for key, options in calls)


def test_collector_to_real_native_peers_never_reloads_target_basic(offline, monkeypatch):
    kwargs, _ = offline
    monkeypatch.setattr(collector, "_run_snapshot_skill", _ORIGINAL_SKILL_RUN)
    Uzi392FetcherRuntime(workspace_root=kwargs["provider_registry"].settings.workspace_root)
    peers = importlib.import_module("fetch_peers")
    industry = importlib.import_module("fetch_industry")
    import pandas as pd
    seen = []

    def native_industry(value):
        seen.append(("industry", value))
        return {"data": {"industry": value}, "source": "fixture:industry", "fallback": False}

    def board(*, symbol):
        seen.append(("peers", symbol))
        return pd.DataFrame([{"代码": "688041", "名称": "固定公司", "总市值": 100,
                              "市盈率-动态": 20.0, "市净率": 3.0}])

    monkeypatch.setattr(industry, "main", native_industry)
    monkeypatch.setattr(peers.ds, "fetch_basic", lambda *a, **kw: pytest.fail("native peers reloaded old target basic"))
    monkeypatch.setattr(peers.ak, "stock_board_industry_cons_em", board)
    monkeypatch.setattr(peers.ak, "stock_financial_analysis_indicator_em", lambda **kw: pd.DataFrame())
    monkeypatch.setenv("UZI_DISABLE_GLOBAL_PEERS", "1")
    result = collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=_fixed())
    assert seen == [("industry", "集成电路"), ("peers", "集成电路")]
    peer = next(row for row in result if row["snapshot_type"] == "peers")
    assert peer["facts"]["_uzi_raw_result"]["data"]["self"] == _fixed()["688041.SH"]["data"]


_ORIGINAL_SKILL_RUN = collector._run_snapshot_skill


@pytest.mark.parametrize("override,reason", [
    ({"uzi_root_route": True}, "fixed_basic_root_route_forbidden"),
    ({"as_of_date": date(2026, 9, 11)}, "fixed_basic_historical_route_forbidden"),
    ({"cutoff_at": datetime(2026, 9, 11, tzinfo=timezone.utc)}, "fixed_basic_historical_route_forbidden"),
    ({"snapshot_types": ["market", "industry"]}, "fixed_basic_market_request_conflict"),
    ({"sector": "半导体"}, "fixed_basic_sector_scope_forbidden"),
])
def test_route_conflicts_fail_before_profile_or_provider_actions(offline, monkeypatch, override, reason):
    kwargs, calls = offline
    monkeypatch.setattr(collector, "_uzi_profile_environment", lambda *a: pytest.fail("invalid request entered profile/provider setup"))
    with pytest.raises(ValueError, match=f"^{reason}$"):
        collector.collect_snapshots(**{**kwargs, **override}, fixed_basic_by_symbol=_fixed())
    assert calls == []


@pytest.mark.parametrize("fixed,reason", [
    ([], "fixed_basic_mapping_invalid"),
    ({}, "fixed_basic_symbol_scope_mismatch"),
    (_fixed("600519.SH"), "fixed_basic_symbol_scope_mismatch"),
    ({**_fixed(), **_fixed("600519.SH")}, "fixed_basic_symbol_scope_mismatch"),
    ({"688041.SH": None}, "fixed_basic_dimresult_invalid"),
    ({"688041.SH": {}}, "fixed_basic_dimresult_invalid"),
    ({"688041.SH": {"data": []}}, "fixed_basic_dimresult_invalid"),
])
def test_invalid_fixed_mapping_is_not_silently_ignored(offline, fixed, reason):
    kwargs, calls = offline
    with pytest.raises(ValueError, match=f"^{reason}$"):
        collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=fixed)
    assert calls == []


def test_omitted_parameter_keeps_market_wave_and_original_dependencies(offline):
    kwargs, calls = offline
    kwargs["snapshot_types"] = ["market", "industry", "peers"]
    collector.collect_snapshots(**kwargs)
    assert calls[0]["snapshot_type"] == "market" and calls[0]["dependencies"] is None
    assert {call["snapshot_type"] for call in calls} == {"market", "industry", "peers"}
    assert all(call["dependencies"]["0_basic"]["data"]["industry"] == "旧身份" for call in calls[1:])


def _materials():
    return {"688041.SH": {"ticker": "688041.SH", "data": {"materials_detail": [{"code": "CU0", "name": "铜"}]},
                           "source": "fixed:materials", "fallback": False}}


def test_fixed_materials_reaches_single_futures_without_recollecting_8(offline):
    kwargs, calls = offline
    kwargs["snapshot_types"] = ["futures"]
    collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=_fixed(), fixed_materials_by_symbol=_materials())
    assert len(calls) == 1 and calls[0]["snapshot_type"] == "futures"
    assert calls[0]["dependencies"]["8_materials"] == _materials()["688041.SH"]


@pytest.mark.parametrize("changes,reason", [
    ({"fixed_basic_by_symbol": None}, "fixed_materials_basic_required"),
    ({"snapshot_types": ["materials", "futures"]}, "fixed_materials_request_conflict"),
    ({"snapshot_types": ["industry"]}, "fixed_materials_futures_required"),
    ({"fixed_materials_by_symbol": {}}, "fixed_materials_symbol_scope_mismatch"),
    ({"fixed_materials_by_symbol": {"688041.SH": {"data": []}}}, "fixed_materials_dimresult_invalid"),
])
def test_fixed_materials_scope_validation(offline, changes, reason):
    kwargs, calls = offline
    args = {**kwargs, "snapshot_types": ["futures"], "fixed_basic_by_symbol": _fixed(),
            "fixed_materials_by_symbol": _materials(), **changes}
    with pytest.raises(ValueError, match=f"^{reason}$"):
        collector.collect_snapshots(**args)
    assert calls == []


def test_fixed_futures_without_same_revision_materials_is_rejected(offline):
    kwargs, calls = offline
    with pytest.raises(ValueError, match="^fixed_materials_dependency_required$"):
        collector.collect_snapshots(**{**kwargs, "snapshot_types": ["futures"]}, fixed_basic_by_symbol=_fixed())
    assert calls == []


def test_same_wave_executed_materials_without_detail_keeps_native_futures_fallback(offline):
    kwargs, calls = offline
    rows = collector.collect_snapshots(**{**kwargs, "snapshot_types": ["materials", "futures"]}, fixed_basic_by_symbol=_fixed())
    assert [call["snapshot_type"] for call in calls] == ["materials", "futures"]
    assert calls[1]["dependencies"]["8_materials"]["data"]["industry"] == "集成电路"
    assert not any(row.get("dependency_execution") for row in rows)


@pytest.mark.parametrize("detail", [None, [], [{"code": "CU0", "name": "铜"}]])
def test_fixed_8_matches_actual_native_9_none_empty_and_detail_semantics(offline, monkeypatch, detail):
    kwargs, _ = offline
    kwargs["snapshot_types"] = ["futures"]
    monkeypatch.setattr(collector, "_run_snapshot_skill", _ORIGINAL_SKILL_RUN)
    Uzi392FetcherRuntime(workspace_root=kwargs["provider_registry"].settings.workspace_root)
    native = importlib.import_module("fetch_futures")
    monkeypatch.setattr(native, "_pull_price", lambda code: {"latest": 10, "trend_60d_pct": 2, "history_60d": [9, 10]})
    expected = native.main("钢铁", detail or None)
    materials = {"688041.SH": {"data": {"materials_detail": detail}, "error": "earlier partial source",
                                "source": "fixed:8", "fallback": True}}
    rows = collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=_fixed(industry="钢铁"),
                                       fixed_materials_by_symbol=materials)
    assert rows[0]["facts"]["_uzi_raw_result"] == expected
