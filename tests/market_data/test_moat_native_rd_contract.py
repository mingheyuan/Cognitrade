"""The pinned moat fetcher returns rd_summary text, not a numeric-only field."""
from copy import deepcopy

import pytest

from research_system.market_data.dimension_contracts import get_dimension_contract
from research_system.market_data.ingestion import _field_value_errors
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from tests.market_data.test_pit_core import AS_OF


def test_native_rd_summary_survives_adapter_and_field_contract():
    native = {"ticker": "600519.SH", "source": "offline-native-shape", "fallback": False,
              "data": {"scores": {"intangible": 5}, "rd_summary": "离线测试公司披露研发投入。"}}
    before = deepcopy(native)
    result = UziSnapshotAdapter().adapt(
        {"snapshot_type": "moat", "symbol": "600519.SH", "quality_status": "available",
         "retrieved_at": "2026-08-20T02:30:00+00:00", "facts": {"_uzi_raw_result": native}},
        symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=AS_OF,
    )
    assert result.payload["research_and_development"] == native["data"]["rd_summary"]
    assert result.payload["_uzi_raw_result"] == before
    assert not _field_value_errors(result.payload, get_dimension_contract("14_moat"))
    assert native == before


@pytest.mark.parametrize("value", [0.1, {"ratio": 0.1}])
def test_existing_numeric_and_object_rd_shapes_remain_valid(value):
    assert not _field_value_errors({"research_and_development": value}, get_dimension_contract("14_moat"))


@pytest.mark.parametrize("value", [True, [], ["untyped"], None])
def test_unrelated_rd_types_are_not_loosened(value):
    assert _field_value_errors({"research_and_development": value}, get_dimension_contract("14_moat"))
