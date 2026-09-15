"""Offline regressions for the AkShare LHB stock-code boundary."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, call

import pandas as pd
import pytest

from research_system.adapters.vendors import akshare_lhb_adapter as lhb_module
from research_system.adapters.vendors import akshare_utils

AS_OF = date(2026, 9, 11)
EVENT_DATE = "20260910"


def test_outside_window_history_is_disclosed_without_future_leak_or_event_count(vendor):
    vendor.dates.return_value = pd.DataFrame({"交易日": ["20260701", "20261201", "invalid"]})
    result = lhb_module.AkShareLHBAdapter().get_lhb_snapshot(symbol="688041.SH", as_of_date=AS_OF)
    assert result["latest_historical_lhb_date"] == "20260701"
    assert result["latest_lhb_date"] is None
    assert result["lhb_count_30d"] == 0
    assert result["observation_status"] == "verified_empty"
    vendor.detail.assert_not_called()
    vendor.statistics.assert_not_called()


def test_only_future_dates_do_not_invent_historical_date(vendor):
    vendor.dates.return_value = pd.DataFrame({"交易日": ["20261201", "invalid"]})
    result = lhb_module.AkShareLHBAdapter().get_lhb_snapshot(symbol="688041.SH", as_of_date=AS_OF)
    assert result["latest_historical_lhb_date"] is None
    assert result["lhb_count_30d"] == 0


class FrozenDate(date):
    @classmethod
    def today(cls) -> date:
        return AS_OF


@pytest.fixture
def vendor(monkeypatch):
    def forbid_network(*_args, **_kwargs):
        pytest.fail("LHB boundary regressions must remain offline")

    monkeypatch.setattr("socket.socket.connect", forbid_network)
    monkeypatch.setattr("socket.create_connection", forbid_network)
    monkeypatch.setattr(lhb_module, "date", FrozenDate)

    def detail_loader(*, symbol, date, flag):
        return pd.DataFrame(
            [{"side": flag, "买入金额" if flag == "买入" else "卖出金额": 100.0}]
        )

    mocks = SimpleNamespace(
        dates=Mock(return_value=pd.DataFrame({"交易日": [EVENT_DATE]})),
        detail=Mock(side_effect=detail_loader),
        statistics=Mock(return_value=pd.DataFrame()),
        sleep=Mock(),
    )
    monkeypatch.setattr(lhb_module.ak, "stock_lhb_stock_detail_date_em", mocks.dates)
    monkeypatch.setattr(lhb_module.ak, "stock_lhb_stock_detail_em", mocks.detail)
    monkeypatch.setattr(lhb_module.ak, "stock_lhb_stock_statistic_em", mocks.statistics)
    monkeypatch.setattr(akshare_utils, "sleep", mocks.sleep)
    return mocks


@pytest.mark.parametrize(
    ("symbol", "code"),
    [
        ("688041", "688041"),
        ("688041.SH", "688041"),
        ("sh688041", "688041"),
        ("SH.688041", "688041"),
        (" 688041.sh ", "688041"),
        ("000001", "000001"),
        ("000001.SZ", "000001"),
        ("sz000001", "000001"),
        ("SZ.000001", "000001"),
        ("920001", "920001"),
        ("920001.BJ", "920001"),
        ("bj920001", "920001"),
        ("BJ.920001", "920001"),
    ],
)
@pytest.mark.parametrize("as_of_date", [None, AS_OF], ids=["current", "historical"])
def test_all_stock_requests_use_bare_code_and_preserve_entity(
    vendor, symbol, code, as_of_date
):
    vendor.statistics.return_value = pd.DataFrame(
        [{"代码": code, "上榜次数": 3}, {"代码": "600519", "上榜次数": 9}]
    )

    result = lhb_module.AkShareLHBAdapter().get_lhb_snapshot(
        symbol=symbol, as_of_date=as_of_date
    )

    vendor.dates.assert_called_once_with(symbol=code)
    assert vendor.detail.call_args_list == [
        call(symbol=code, date=EVENT_DATE, flag="买入"),
        call(symbol=code, date=EVENT_DATE, flag="卖出"),
    ]
    assert result["symbol"] == symbol
    assert result["latest_lhb_date"] == EVENT_DATE
    assert result["observation_status"] == "verified_non_empty"
    assert result["buy_detail"][0]["side"] == "买入"
    assert result["sell_detail"][0]["side"] == "卖出"
    assert result["buy_top_total"] == result["sell_top_total"] == 100.0
    if as_of_date is None:
        vendor.statistics.assert_called_once_with(symbol="近一月")
        assert result["lhb_statistics"] == [{"代码": code, "上榜次数": 3}]
        assert result["lhb_count_30d"] == 3
    else:
        vendor.statistics.assert_not_called()
        assert result["lhb_statistics"] == []
        assert result["lhb_count_30d"] == 1
    vendor.sleep.assert_not_called()


@pytest.mark.parametrize(
    "symbol",
    [
        "AAPL",
        "00700.HK",
        "HK00700",
        "688041.HK",
        "688041.US",
        "US688041",
        "SH688041.SZ",
        "SH688041.SH",
        "688041.SH.extra",
        "68804",
        "6880411",
        "688O41",
        "６８８０４１",
        "",
        " ",
        None,
    ],
)
def test_invalid_symbols_fail_before_any_vendor_request(vendor, symbol):
    with pytest.raises(ValueError, match="LHB.*six-digit.*SH.*SZ.*BJ"):
        lhb_module.AkShareLHBAdapter().get_lhb_snapshot(symbol=symbol)

    vendor.dates.assert_not_called()
    vendor.detail.assert_not_called()
    vendor.statistics.assert_not_called()
    vendor.sleep.assert_not_called()


@pytest.mark.parametrize("provider_code", [1, 1.0, "000001", "000001.SZ", "sz000001"])
def test_statistics_compare_normalized_codes_without_matching_other_markets(
    vendor, provider_code
):
    vendor.statistics.return_value = pd.DataFrame(
        [
            {"代码": provider_code, "上榜次数": 3},
            {"代码": "000001.HK", "上榜次数": 99},
            {"代码": "600519", "上榜次数": 9},
        ]
    )

    result = lhb_module.AkShareLHBAdapter().get_lhb_snapshot(symbol="000001.SZ")

    assert len(result["lhb_statistics"]) == 1
    assert result["lhb_statistics"][0]["代码"] == provider_code
    assert result["lhb_count_30d"] == 3


@pytest.mark.parametrize("empty_dates", [[], ["2026-07-01"], ["2026-09-12"]])
def test_only_successful_empty_window_is_verified_empty(vendor, empty_dates):
    vendor.dates.return_value = pd.DataFrame({"交易日": empty_dates})

    result = lhb_module.AkShareLHBAdapter().get_lhb_snapshot(
        symbol="688041.SH", as_of_date=AS_OF
    )

    vendor.dates.assert_called_once_with(symbol="688041")
    vendor.detail.assert_not_called()
    vendor.statistics.assert_not_called()
    assert result["symbol"] == "688041.SH"
    assert result["observation_status"] == "verified_empty"
    assert result["lhb_count_30d"] == 0
    assert result["latest_lhb_date"] is None


@pytest.mark.parametrize("stage", ["dates", "buy", "sell", "statistics"])
def test_sdk_null_result_typeerror_is_not_reclassified_as_empty(vendor, stage):
    error = TypeError("'NoneType' object is not subscriptable")
    if stage in {"dates", "statistics"}:
        getattr(vendor, stage).side_effect = error
    else:
        flag_to_fail = "买入" if stage == "buy" else "卖出"
        original_loader = vendor.detail.side_effect

        def detail_loader(**kwargs):
            if kwargs["flag"] == flag_to_fail:
                raise error
            return original_loader(**kwargs)

        vendor.detail.side_effect = detail_loader

    with pytest.raises(TypeError) as raised:
        lhb_module.AkShareLHBAdapter().get_lhb_snapshot(symbol="688041.SH")

    assert raised.value is error
    vendor.sleep.assert_called_once_with(0.5)
    assert all(item.kwargs["symbol"] == "688041" for item in vendor.dates.call_args_list)
    assert all(item.kwargs["symbol"] == "688041" for item in vendor.detail.call_args_list)
    if stage == "dates":
        assert vendor.dates.call_count == 2
        vendor.detail.assert_not_called()
        vendor.statistics.assert_not_called()
    elif stage in {"buy", "sell"}:
        failures = [
            item for item in vendor.detail.call_args_list
            if item.kwargs["flag"] == flag_to_fail
        ]
        assert len(failures) == 2
        vendor.statistics.assert_not_called()
    else:
        assert vendor.statistics.call_args_list == [call(symbol="近一月")] * 2
