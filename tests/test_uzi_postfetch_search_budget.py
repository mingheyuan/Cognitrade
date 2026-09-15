"""Real native cache/counter functions; only the network leaf is recorded."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.tools import uzi_native_postfetch as postfetch
from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime


@pytest.fixture
def transport(tmp_path, monkeypatch):
    Uzi392FetcherRuntime(workspace_root=tmp_path)._initialize_import_boundary()
    web = import_module("lib.web_search")
    parent = {"used": 19, "skipped": 7}
    monkeypatch.setattr(web, "_BUDGET_STATE", parent.copy())
    calls = []

    def fetch(self, **kwargs):
        calls.append(kwargs["query"])
        return {"results": [{"title": "实际来源标题", "body": "实际业务资料", "url": "https://example.test/source"}]}

    monkeypatch.setattr(postfetch.UziWebSearchAdapter, "search", fetch)
    yield SimpleNamespace(settings=SimpleNamespace(mx_api_key=None, browser_sources_enabled=False),
                          calls=calls, web=web)
    assert web.get_budget_state() == parent


def receipt(scope="structured-stock-a", used=2, cap=3):
    return {"scope_id": scope, "scope": "stock_collection", "cap": str(cap), "used": used, "skipped": 0}


def test_structured_consumption_continues_through_real_native_cache(transport):
    seed = receipt()
    original = deepcopy(seed)
    source = postfetch.PostfetchSources(transport.settings).for_stock(seed)
    first = source.search("材料成本一", max_results=3, timeout=4, profile_budget=60)
    assert first
    assert source.search("材料成本一", max_results=3, timeout=4, profile_budget=60) == first
    assert source.search("材料成本二", max_results=3, timeout=4, profile_budget=60) == []
    assert transport.calls == ["材料成本一"]
    assert source.search_budget()["used"] == 3
    assert source.search_budget()["skipped"] == 1
    assert source.search_budget()["cap"] == 3
    assert source.search_budget()["scope_id"] == seed["scope_id"]
    assert seed == original


def test_parallel_stocks_do_not_share_the_parent_or_each_other(transport):
    template = postfetch.PostfetchSources(transport.settings)

    def stock(index):
        source = template.for_stock(receipt(f"stock-{index}", used=0, cap=1))
        assert source.search(f"股票{index}主营", max_results=3, timeout=4, profile_budget=60)
        assert not source.search(f"股票{index}政策", max_results=3, timeout=4, profile_budget=60)
        return source.search_budget()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(stock, [1, 2]))
    assert len(transport.calls) == 2
    assert [row["used"] for row in results] == [1, 1]
    assert len({row["scope_id"] for row in results}) == 2


def test_manual_recovery_does_not_inherit_arbitrary_old_snapshot_budget(transport):
    old = receipt(used=60, cap=60)
    snapshots = [{"snapshot_type": "materials", "symbol": "688041.SH", "facts": {
        "_uzi_execution": {"search_budget": old}, "_uzi_raw_result": {"data": {}}}}]
    result = postfetch.recover_snapshot_postfetch(snapshots, symbols=["688041.SH"], profile="medium",
        settings=transport.settings, browser=False)
    budget = result["search_budgets"]["688041.SH"]
    assert budget["scope_id"] != old["scope_id"]
    assert budget["used"] == 1 and budget["cap"] == 30
    assert len(transport.calls) == 1


def test_collection_seed_is_explicit_per_symbol_and_retained_even_without_data_change(transport):
    rows = [{"snapshot_type": "materials", "symbol": symbol, "facts": {"_uzi_raw_result": {"data": {}}}}
            for symbol in ["688041.SH", "688012.SH"]]
    seeds = {"688041.SH": receipt("one", used=1, cap=1), "688012.SH": receipt("two", used=0, cap=1)}
    result = postfetch.recover_snapshot_postfetch(rows, symbols=list(seeds), profile="deep",
        settings=transport.settings, browser=False, collection_search_budgets=seeds)
    assert len(transport.calls) == 1
    assert result["search_budgets"]["688041.SH"]["skipped"] == 1
    assert result["search_budgets"]["688012.SH"]["used"] == 1
    for row in rows:
        assert row["facts"]["_uzi_postfetch"]["search_budget"] == result["search_budgets"][row["symbol"]]


@pytest.mark.parametrize("invalid", [{"cap": "bad"}, {"used": -1}, {"used": True}, {"scope_id": ""}])
def test_invalid_collection_receipt_disables_only_new_ddgs_calls(transport, invalid):
    source = postfetch.PostfetchSources(transport.settings).for_stock({**receipt(), **invalid})
    assert not source.search("无有效消费记录", max_results=3, timeout=4, profile_budget=60)
    assert transport.calls == []
    assert source.search_budget()["reason_code"] == "collection_search_budget_invalid"
