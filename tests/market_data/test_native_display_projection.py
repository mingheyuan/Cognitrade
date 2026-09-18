"""Display mirrors must not become extra native facts; old projections replay."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from research_system.market_data.native_projection import project_native_result


def snapshot(dimension, original, raw):
    return SimpleNamespace(dimension=dimension, status="available", source_version="fixture",
        payload_json={"raw": deepcopy(raw), "_uzi_raw_result": {
            "data": deepcopy(original), "source": "native-source", "fallback": True,
            "error": "original-partial-error", "_timeout": False}},
        query_manifest_json={"pit_safe": True}, validation_json={})


@pytest.mark.parametrize("dimension,alias,data", [
    ("0_basic", "market_overview", {"industry": None, "price": 10}),
    ("1_financials", "financial_abstract", {"roe_history": [10, 12]}),
    ("2_kline", "history", {"close": [9, 10]}),
])
def test_current_projection_excludes_host_only_mirrors_and_preserves_v3(dimension, alias, data):
    row = snapshot(dimension, data, {**data, alias: {"display": "derived"}})
    before = deepcopy(vars(row))
    old = project_native_result(row, version="uzi-dim-result-v3")
    current = project_native_result(row)
    assert current["data"] == data
    assert old["data"] == row.payload_json["raw"]
    assert {k:v for k,v in current.items() if k != "data"} == {k:v for k,v in old.items() if k != "data"}
    assert vars(row) == before


def test_source_recovery_and_research_revision_values_are_not_replaced_by_envelope():
    row = snapshot("0_basic", {"industry": None, "price": None}, {
        "industry": "集成电路设计行业", "price": 123, "main_business": "高端处理器",
        "recovered_disclosures": [{"title": "issuer publication"}],
        "market_overview": {"industry": None, "price": None}})
    projected = project_native_result(row)
    assert projected["data"] == {"industry": "集成电路设计行业", "price": 123,
        "main_business": "高端处理器", "recovered_disclosures": [{"title": "issuer publication"}]}
    assert projected["error"] == "original-partial-error"
    assert projected["fallback"] is True


@pytest.mark.parametrize("dimension,alias", [("0_basic", "market_overview"),
    ("1_financials", "financial_abstract"), ("2_kline", "history")])
def test_same_named_original_source_field_is_preserved(dimension, alias):
    data = {alias: [{"fact": 1}]}
    assert project_native_result(snapshot(dimension, data, data))["data"] == data


def test_unknown_and_other_dimension_fields_are_not_filtered():
    data = {"history": [{"period": "2026", "value": 10}], "vendor_extension": 12}
    assert project_native_result(snapshot("1_financials", {}, data))["data"] == data


def test_provider_only_payload_without_native_envelope_is_not_reclassified():
    row = snapshot("2_kline", {}, {"history": [{"close": 1}]})
    del row.payload_json["_uzi_raw_result"]
    assert project_native_result(row)["data"] == row.payload_json["raw"]


def test_pit_rejection_still_excludes_all_facts():
    row = snapshot("0_basic", {"price": 1}, {"price": 1, "market_overview": {"price": 1}})
    row.query_manifest_json["pit_safe"] = False
    assert project_native_result(row)["data"] == {}


def test_replay_price_sidecar_is_not_an_extra_native_report_input():
    original = {"close": [10], "field_queries": {"native": {"status": "success"}}}
    sidecar = {"provider_version": "baostock-price-evidence-v1",
        "corporate_actions": [{"date": "2026-01-01"}], "adjustment_factors": [1.1],
        "field_queries": {"corporate_actions": {"status": "success"}}}
    row = snapshot("2_kline", original, {**original, **sidecar,
        "price_evidence": sidecar, "field_queries": {**original["field_queries"], **sidecar["field_queries"]}})
    # The provider_version is inside the sidecar in actual adapter output.
    del row.payload_json["raw"]["provider_version"]
    row.payload_json["corporate_actions"] = deepcopy(sidecar["corporate_actions"])
    row.payload_json["adjustment_factors"] = deepcopy(sidecar["adjustment_factors"])
    before = deepcopy(vars(row))
    assert project_native_result(row)["data"] == original
    assert vars(row) == before
    assert row.payload_json["corporate_actions"] == sidecar["corporate_actions"]


def test_unrelated_recovered_price_fields_remain_available():
    row = snapshot("2_kline", {}, {"corporate_actions": [{"fact": 1}]})
    assert project_native_result(row)["data"] == row.payload_json["raw"]


def test_top_level_fund_mirror_is_not_repeated_inside_native_dimension():
    native = {"institution_count": 2}
    managers = [{"fund_code": "000001"}]
    row = snapshot("6_fund_holders", native, {**native, "fund_managers": managers})
    row.payload_json["_uzi_top_level_fields"] = {"fund_managers": {"present": True, "value": managers}}
    before = deepcopy(vars(row))
    assert project_native_result(row)["data"] == native
    assert vars(row) == before
    # Without the recorded root there is no proof this field is a spill mirror.
    del row.payload_json["_uzi_top_level_fields"]
    assert project_native_result(row)["data"]["fund_managers"] == managers


def test_new_pin_is_versioned_while_v3_remains_supported():
    from research_system.market_data.native_projection import pin_native_workflow
    from research_system.strategies.runtime_pins import deployed_workflow_pins
    assert pin_native_workflow({})["snapshot_projection_version"] == "uzi-dim-result-v10"
    assert deployed_workflow_pins()["snapshot_projection_version"] == "uzi-dim-result-v10"
    assert pin_native_workflow({"snapshot_projection_version": "uzi-dim-result-v6"})["snapshot_projection_version"] == "uzi-dim-result-v6"
    assert pin_native_workflow({"snapshot_projection_version": "uzi-dim-result-v5"})["snapshot_projection_version"] == "uzi-dim-result-v5"
    assert pin_native_workflow({"snapshot_projection_version": "uzi-dim-result-v4"})["snapshot_projection_version"] == "uzi-dim-result-v4"
    assert pin_native_workflow({"snapshot_projection_version": "uzi-dim-result-v3"})["snapshot_projection_version"] == "uzi-dim-result-v3"


def test_profile_lineage_keeps_its_fixed_v3_payload_across_new_report_projection(session, tmp_path):
    from research_system.market_data.ingestion import SnapshotIngestionService
    from tests.market_data.test_company_profile_revision import scope_for, seed
    from tests.market_data.test_company_profile_revision_lineage import revise
    from tests.market_data.test_pit_core import _input

    state = seed(session, tmp_path)
    payload = deepcopy(state.basic.payload_json)
    payload["raw"]["market_overview"] = {"industry": None}
    state.basic = SnapshotIngestionService(state.repo).ingest(_input("0_basic", payload=payload))
    original = deepcopy(state.basic.payload_json)
    original_hash = state.basic.content_hash
    assert scope_for(state)["target"]["raw"] == original["raw"]
    first = revise(state, "industry", 1)
    second = revise(state, "main_business", 2)
    assert "market_overview" in first.payload_json["raw"]
    assert "market_overview" in second.payload_json["raw"]
    assert "market_overview" not in project_native_result(second)["data"]
    assert project_native_result(second, version="uzi-dim-result-v3")["data"] == second.payload_json["raw"]
    assert scope_for(state)["dependency_lineage"]["consumer_anchors"]
    assert state.repo.require_snapshot(first.query_manifest_json["company_profile_revision"]["input_snapshot_id"]).content_hash == original_hash


# Share the real, isolated database fixture; no production database is used.
from tests.market_data.test_pit_core import session as session  # noqa: PLC0414
