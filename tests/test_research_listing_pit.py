from datetime import date
from unittest.mock import Mock

import pandas as pd
import pytest

from research_system.adapters.vendors.akshare_catalyst_adapter import AkShareCatalystAdapter


def test_current_research_strips_exchange_before_akshare(monkeypatch):
    fetch = Mock(return_value=pd.DataFrame())
    monkeypatch.setattr("research_system.adapters.vendors.akshare_catalyst_adapter.ak.stock_research_report_em", fetch)
    assert AkShareCatalystAdapter().get_symbol_research_reports(symbol="688012.SH") == []
    fetch.assert_called_once_with(symbol="688012")


def test_historical_request_is_bounded_and_retains_document_not_current_forecast(monkeypatch):
    response = Mock()
    response.json.return_value = {"hits": 1, "data": [{
        "stockCode": "688012", "title": "测试研报", "infoCode": "AP20260825001",
        "publishDate": "2026-08-25 00:00:00.000", "predictThisYearEps": 999,
    }]}
    get = Mock(return_value=response)
    monkeypatch.setattr("research_system.adapters.vendors.akshare_catalyst_adapter.requests.get", get)
    rows = AkShareCatalystAdapter().get_symbol_research_reports(symbol="688012.SH", as_of_date=date(2026, 9, 2))
    params = get.call_args.kwargs["params"]
    assert params["code"] == "688012"
    assert params["beginTime"] == "2025-09-02" and params["endTime"] == "2026-09-02"
    assert rows[0]["source_document_id"] == "AP20260825001"
    assert rows[0]["日期"] == "2026-08-25"
    assert rows[0]["published_at"] == "2026-08-25"
    assert "predictThisYearEps" not in rows[0]
    response.close.assert_called_once()


def test_source_skill_keeps_real_historical_reports_after_date_filter(monkeypatch):
    from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import ResearchSnapshotSkill

    response = Mock()
    response.json.return_value = {"hits": 1, "data": [{
        "stockCode": "688012", "title": "真实日期字段", "infoCode": "AP20260825001",
        "publishDate": "2026-08-25 00:00:00.000",
    }]}
    monkeypatch.setattr("research_system.adapters.vendors.akshare_catalyst_adapter.requests.get", Mock(return_value=response))
    facts = ResearchSnapshotSkill()._collect(query="中微公司", symbol="688012.SH", as_of_date=date(2026, 9, 2))
    assert len(facts["reports"]) == 1
    assert facts["reports"][0]["source_document_id"] == "AP20260825001"
    assert facts["availability"] == "available"
    assert facts["_source_trace"][0]["count"] == 1


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": []}, {"data": [], "hits": 1},
    {"data": [{"stockCode": "688012", "publishDate": "2026-09-03", "infoCode": "future", "title": "future"}]},
    {"data": [{"stockCode": "600519", "publishDate": "2026-08-25", "infoCode": "other", "title": "other"}]}])
def test_bad_or_out_of_scope_listing_is_not_a_verified_zero(monkeypatch, payload):
    response = Mock()
    response.json.return_value = payload
    monkeypatch.setattr("research_system.adapters.vendors.akshare_catalyst_adapter.requests.get", Mock(return_value=response))
    with pytest.raises(ValueError, match="research_listing_"):
        AkShareCatalystAdapter().get_symbol_research_reports(symbol="688012.SH", as_of_date=date(2026, 9, 2))


def test_explicit_zero_response_is_empty(monkeypatch):
    response = Mock()
    response.json.return_value = {"data": [], "hits": 0}
    monkeypatch.setattr("research_system.adapters.vendors.akshare_catalyst_adapter.requests.get", Mock(return_value=response))
    assert AkShareCatalystAdapter().get_symbol_research_reports(symbol="688012.SH", as_of_date=date(2026, 9, 2)) == []


def test_fund_holder_source_uses_bare_code_but_period_does_not_prove_publication(monkeypatch):
    from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import FundHoldersSnapshotSkill
    from research_system.market_data.uzi_adapter import UziSnapshotAdapter
    from datetime import datetime, timezone

    fetch = Mock(return_value=pd.DataFrame([{"基金名称": "测试基金", "截止日期": "2026-06-30"}]))
    monkeypatch.setattr("domain_packs.finance_ashare.skills.uzi_source_snapshot.skill.ak.stock_fund_stock_holder", fetch)
    result = FundHoldersSnapshotSkill().run(query="中微公司", symbol="688012.SH", as_of_date=date(2026, 9, 2))
    fetch.assert_called_once_with(symbol="688012")
    assert result["facts"]["data_gap"] == ["fund_holders_publication_dates_missing"]
    assert result["source_trace"][0]["status"] == "historical_version_unavailable"
    result.update(retrieved_at=datetime.now(timezone.utc).isoformat(),
                  historical_reconstruction={"safe": True, "strategy": "published_archive",
                      "requested_as_of_date": "2026-09-02", "window_start": "2021-09-02", "window_end": "2026-09-02"})
    snapshot = UziSnapshotAdapter().adapt(result, symbol="688012.SH", market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai", as_of_date=date(2026, 9, 2))
    assert snapshot.status != "verified_empty"
    assert snapshot.query_manifest["degradation"]["reason_code"] == "fund_holders_publication_dates_missing"
    assert snapshot.query_manifest["degradation"]["retryable"] is False
