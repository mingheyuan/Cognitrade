"""On-demand stock waves keep native search semantics without global leakage."""
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Event
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.tools import snapshot_collector as collector
from domain_packs.finance_ashare.tools import uzi_native_postfetch as postfetch
from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime
from domain_packs.finance_ashare.vendor.uzi_3_9_2.search_budget import (
    ScopedSearchBudgetClosed,
    ThreadStockSearchBudget,
    submit_budget_context,
)


@pytest.fixture
def native(tmp_path, monkeypatch):
    Uzi392FetcherRuntime(workspace_root=tmp_path)
    web = import_module("lib.web_search")
    monkeypatch.setattr(web, "_BUDGET_STATE", {"used": 19, "skipped": 7})
    monkeypatch.setattr("socket.socket.connect", lambda *a, **kw: pytest.fail("live network in budget regression"))
    calls = []

    def network(query, **kwargs):
        calls.append(query)
        return [{"title": "recorded", "body": "business data", "url": "https://example.test/1"}]

    monkeypatch.setattr(web, "_ddg_search", network)
    return SimpleNamespace(web=web, calls=calls, settings=SimpleNamespace(workspace_root=tmp_path))


@pytest.mark.parametrize("profile,expected,cap", [("lite", 0, 0), ("medium", 16, 30), ("deep", 16, 60)])
def test_each_explicit_scoped_stock_has_native_profile_allowance(native, monkeypatch, profile, expected, cap):
    first_finished = Event()

    def source(query, symbol, kind, *args, **kwargs):
        if symbol == "688012.SH":
            assert first_finished.wait(5)
        for index in range(16):
            native.web.search(f"{symbol}:{index}", max_results=1)
        if symbol == "688041.SH":
            first_finished.set()
        return {"snapshot_type": kind, "symbol": symbol, "facts": {"_uzi_raw_result": {"data": {"recorded": True}}}}

    monkeypatch.setattr(collector, "_run_snapshot_skill", source)
    rows = collector.collect_snapshots(query="explicit scoped collection", symbols=["688041.SH", "688012.SH"],
        snapshot_types=["macro"], research_profile=profile, max_workers=2,
        provider_registry=SimpleNamespace(settings=native.settings))
    assert len(rows) == 2
    assert [sum(query.startswith(symbol) for query in native.calls) for symbol in ("688041.SH", "688012.SH")] == [expected, expected]
    budgets = [row["facts"]["_uzi_execution"]["search_budget"] for row in rows]
    assert len({row["scope_id"] for row in budgets}) == 2
    assert [row["used"] for row in budgets] == [expected, expected]
    assert [row["cap"] for row in budgets] == [cap, cap]
    assert native.web.get_budget_state() == {"used": 0, "skipped": 0}


def test_counter_seal_stops_late_thread_without_changing_next_scope_or_cache(native, monkeypatch):
    old = ThreadStockSearchBudget(1)
    waiting, release = Event(), Event()
    before = native.web.get_budget_state()

    def delayed():
        assert native.web.search("old-authorized", max_results=1)
        waiting.set()
        assert release.wait(5)
        return native.web.search("old-too-late", max_results=1)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with old.transport():
            future = submit_budget_context(pool, delayed)
        assert waiting.wait(5)
        old.seal()
        sealed = old.receipt()
        # The process profile can now belong to another request. Its cap must
        # not override either the new explicit scope or the sealed old one.
        monkeypatch.setenv("UZI_DDG_BUDGET", "0")
        new = ThreadStockSearchBudget(2)
        try:
            with new.transport():
                assert native.web.search("new-authorized", max_results=1)
                release.set()
                with pytest.raises(ScopedSearchBudgetClosed):
                    future.result(timeout=5)
                # The rejected late query was not cached as budget-exceeded.
                assert native.web.search("old-too-late", max_results=1)
        finally:
            release.set()
            new.seal()
    assert old.receipt() == sealed
    assert new.receipt()["used"] == 2
    assert native.web.get_budget_state() == before
    assert native.calls == ["old-authorized", "new-authorized", "old-too-late"]


def test_nested_deadline_worker_keeps_scope_and_cannot_spend_after_timeout(native, monkeypatch):
    waiting, release, exited = Event(), Event(), Event()

    def source(*args, **kwargs):
        try:
            assert native.web.search("before-timeout", max_results=1)
            waiting.set()
            assert release.wait(5)
            return native.web.search("after-timeout", max_results=1)
        finally:
            exited.set()

    monkeypatch.setattr(collector, "_run_snapshot_skill", source)
    budget = ThreadStockSearchBudget(2)
    try:
        with budget.transport():
            result = collector._run_snapshot_skill_with_deadline(query="deadline fixture", symbol="688041.SH",
                snapshot_type="macro", provider_registry=SimpleNamespace(settings=native.settings),
                dependencies=None, timeout_seconds=0.1, research_profile="medium")
        assert waiting.is_set()
        assert result["quality_status"] == "data_gap"
    finally:
        budget.seal()
        release.set()
        assert exited.wait(5)
    assert budget.receipt()["used"] == 1
    assert native.calls == ["before-timeout"]


def test_postfetch_uses_original_helper_after_thread_dispatch_installation(native, monkeypatch):
    budget = ThreadStockSearchBudget(2)
    with budget.transport():
        assert native.web.search("structured-query", max_results=1)
    budget.seal()
    monkeypatch.setattr(postfetch.UziWebSearchAdapter, "search", lambda self, **kw: {
        "results": [{"title": "recorded", "body": "business data", "url": "https://example.test/1"}]})
    source = postfetch.PostfetchSources(native.settings).for_stock(budget.receipt())
    assert source.search("postfetch-query", max_results=1, timeout=1, profile_budget=60)
    assert not source.search("postfetch-exhausted", max_results=1, timeout=1, profile_budget=60)
    assert source.search_budget()["used"] == 2
    assert budget.receipt()["used"] == 1
    assert native.web.get_budget_state() == {"used": 19, "skipped": 7}
