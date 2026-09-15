"""The pinned search allowance belongs to a stock workflow, not each child."""
from importlib import import_module
from time import monotonic
from unittest.mock import patch

import pytest

from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime


def budgeted_recorded_source(dim_key, ticker, call):
    """Real native cache/budget code, fake network leaf in a real child."""
    web = import_module("lib.web_search")
    requested = []

    def fake_search(query, **kwargs):
        requested.append(query)
        return [{"title": "recorded", "body": query, "url": "https://example.test/recorded"}]

    def forbidden(*args, **kwargs):
        raise AssertionError("Budget regression must not request business data")

    with patch.object(web, "_ddg_search", fake_search), patch("socket.socket.connect", forbidden):
        results = [web.search(f"{ticker}:{dim_key}:{i}", max_results=1) for i in range(3)]
    return dim_key, {"data": {"requested": len(requested), "results": results,
                              "budget": web.get_budget_state()}, "source": "offline"}, {}


def test_one_native_search_budget_survives_multiple_source_processes(tmp_path, monkeypatch):
    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host
    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_schedule as schedule

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    web, cache = import_module("lib.web_search"), import_module("lib.cache")
    monkeypatch.setenv("UZI_DDG_BUDGET", "2")
    monkeypatch.setattr(web, "_BUDGET_STATE", {"used": 0, "skipped": 0})
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "reference")
    reference = [budgeted_recorded_source(dim, "fixture", ()) for dim in ("3_macro", "17_sentiment")]
    expected = [row[1]["data"]["requested"] for row in reference]
    assert expected == [2, 0]
    monkeypatch.setattr(web, "_BUDGET_STATE", {"used": 0, "skipped": 0})
    monkeypatch.setattr(schedule, "run_source_job", budgeted_recorded_source)
    pipeline = import_module("lib.pipeline.collect")
    with host._pipeline_symbol_collection_scope(pipeline, cache_root=tmp_path / "host",
            policy={}, timeout_overrides={}, native_skill_inputs=True):
        boundary = schedule.ProcessSourceBoundary()
        try:
            actual = [boundary(source, ("fixture",), {}, monotonic() + 15)
                      for source in ("fetch_macro", "fetch_sentiment")]
            assert boundary.search_budget.read() == {"used": 2, "skipped": 4}
        finally:
            boundary.close()
    assert [row["data"]["requested"] for row in actual] == expected
    assert web.get_budget_state() == {"used": 0, "skipped": 0}


def die_holding_budget(path):
    import os

    from domain_packs.finance_ashare.vendor.uzi_3_9_2.search_budget import NativeCounter

    counter = NativeCounter(path)
    with counter:
        counter["used"] = 999
        os._exit(23)


def test_crashed_worker_cannot_leave_budget_locked_or_partially_written(tmp_path):
    from pathlib import Path

    from domain_packs.finance_ashare.vendor.uzi_3_9_2.search_budget import (
        StockSearchBudget,
    )

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    runner = import_module("lib.pipeline.process_runner")
    budget = StockSearchBudget()
    try:
        result = runner.run_process_jobs([
            runner.ProcessJob("offline_crash", die_holding_budget, (budget.path,), timeout_sec=5),
        ], max_workers=1, overall_timeout=6)
        assert result[0].error and not result[0].timed_out
        assert budget.read() == {"used": 0, "skipped": 0}
    finally:
        budget.close()
    assert not Path(budget.path).exists()


def test_parallel_stocks_have_separate_native_budgets(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host
    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_schedule as schedule

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    monkeypatch.setenv("UZI_DDG_BUDGET", "2")
    monkeypatch.setattr(schedule, "run_source_job", budgeted_recorded_source)
    pipeline = import_module("lib.pipeline.collect")

    def stock(ticker):
        boundary = schedule.ProcessSourceBoundary()
        try:
            results = [boundary(source, (ticker,), {}, monotonic() + 15)
                       for source in ("fetch_macro", "fetch_sentiment")]
            return [row["data"]["requested"] for row in results], boundary.search_budget.read()
        finally:
            boundary.close()

    with (host._pipeline_symbol_collection_scope(pipeline, cache_root=tmp_path / "host",
            policy={}, timeout_overrides={}, native_skill_inputs=True),
          ThreadPoolExecutor(max_workers=2) as pool):
        results = list(pool.map(stock, ("fixture-left", "fixture-right")))
    assert results == [([2, 0], {"used": 2, "skipped": 4})] * 2


def test_native_cache_hits_do_not_spend_shared_budget(tmp_path, monkeypatch):
    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host
    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_schedule as schedule

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    monkeypatch.setenv("UZI_DDG_BUDGET", "2")
    monkeypatch.setattr(schedule, "run_source_job", budgeted_recorded_source)
    pipeline = import_module("lib.pipeline.collect")
    with host._pipeline_symbol_collection_scope(pipeline, cache_root=tmp_path / "host",
            policy={}, timeout_overrides={}, native_skill_inputs=True):
        boundary = schedule.ProcessSourceBoundary()
        try:
            first = boundary("fetch_macro", ("fixture",), {}, monotonic() + 15)
            second = boundary("fetch_macro", ("fixture",), {}, monotonic() + 15)
            assert first["data"]["requested"] == 2 and second["data"]["requested"] == 0
            assert boundary.search_budget.read() == {"used": 2, "skipped": 1}
        finally:
            boundary.close()


def test_real_source_process_receipt_is_consumed_by_native_postfetch(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from domain_packs.finance_ashare.tools import uzi_native_postfetch as postfetch
    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host
    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_schedule as schedule

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    monkeypatch.setenv("UZI_DDG_BUDGET", "4")
    monkeypatch.setattr(schedule, "run_source_job", budgeted_recorded_source)
    pipeline = import_module("lib.pipeline.collect")
    with host._pipeline_symbol_collection_scope(pipeline, cache_root=tmp_path / "host",
            policy={}, timeout_overrides={}, native_skill_inputs=True):
        boundary = schedule.ProcessSourceBoundary()
        try:
            result = boundary("fetch_macro", ("fixture",), {}, monotonic() + 15)
            assert result["data"]["requested"] == 3
            receipt = {"scope_id": boundary.search_budget.id, "scope": "stock_collection",
                       "cap": "4", **boundary.search_budget.read()}
        finally:
            boundary.close()
    requests = []
    monkeypatch.setattr(postfetch.UziWebSearchAdapter, "search", lambda self, **kw: (
        requests.append(kw["query"]) or {"results": [{"title": "recorded", "body": "source", "url": "https://example.test/1"}]}))
    source = postfetch.PostfetchSources(SimpleNamespace(mx_api_key=None)).for_stock(receipt)
    assert source.search("postfetch-first", max_results=1, timeout=3, profile_budget=60)
    assert not source.search("postfetch-second", max_results=1, timeout=3, profile_budget=60)
    assert requests == ["postfetch-first"]
    assert source.search_budget() == {**receipt, "cap": 4, "used": 4, "skipped": 1}


def test_native_counter_cannot_mutate_parent_module_state(tmp_path):
    from domain_packs.finance_ashare.vendor.uzi_3_9_2.search_budget import (
        bind_native_counter,
    )

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    web = import_module("lib.web_search")
    original = web._BUDGET_STATE, web._BUDGET_LOCK
    with pytest.raises(RuntimeError, match="isolated source child"), bind_native_counter(tmp_path / "unused"):
        pytest.fail("Parent process must not reach native global counter rebinding")
    assert (web._BUDGET_STATE, web._BUDGET_LOCK) == original


@pytest.mark.parametrize("failure", [False, True])
def test_stock_budget_lifecycle_and_receipt(tmp_path, monkeypatch, failure):
    from pathlib import Path

    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_schedule as schedule

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    monkeypatch.setenv("UZI_DEPTH", "lite")
    monkeypatch.setenv("UZI_DDG_BUDGET", "0")

    class RecordedBoundary(schedule.ProcessSourceBoundary):
        def __call__(self, source, args, kwargs, deadline):
            if failure:
                raise RuntimeError("recorded source orchestration failure")
            return {"ticker": "688041.SH", "data": {"name": "fixture", "industry": "fixture"}}

    boundary = RecordedBoundary()
    path = Path(boundary.search_budget.path)
    if failure:
        with pytest.raises(RuntimeError, match="recorded source orchestration failure"):
            schedule.collect("688041.SH", previous={}, max_workers=2, call_source=boundary)
    else:
        raw = schedule.collect("688041.SH", previous={}, max_workers=2, call_source=boundary)
        assert raw["_uzi_search_budget"] == {"scope_id": boundary.search_budget.id,
            "scope": "stock_collection", "cap": "0", "used": 0, "skipped": 0}
    assert not path.exists()


def test_stock_budget_receipt_survives_snapshot_projection():
    from domain_packs.finance_ashare.tools.snapshot_collector import (
        _project_uzi_symbol_snapshot,
    )

    receipt = {"scope_id": "recorded-stock-wave", "scope": "stock_collection",
               "cap": "30", "used": 30, "skipped": 4}
    result = _project_uzi_symbol_snapshot(query="fixture", requested_symbol="688041.SH",
        snapshot_type="macro", symbol_result={"ticker": "688041.SH",
            "dimensions": {"3_macro": {"data": {"macro": "fixture"}, "source": "recorded"}},
            "_uzi_execution": {"lane": "skill_main_process", "search_budget": receipt}})
    assert result["facts"]["_uzi_execution"]["search_budget"] == receipt
    assert result["facts"]["_uzi_raw_result"] == {"data": {"macro": "fixture"}, "source": "recorded"}
