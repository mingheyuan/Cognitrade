"""Frozen bonus owners must win over stale mirrors, without changing v4 replay."""
from copy import deepcopy
from itertools import permutations
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw


def row(kind, *, data=None, spill=None, version="uzi-dim-result-v5", failure=False, unsafe=False):
    facts = {"_uzi_raw_result": {"data": data or {}, "source": "recorded-native"}}
    if version in {"uzi-dim-result-v5", "uzi-dim-result-v6", "uzi-dim-result-v7", "uzi-dim-result-v8", "uzi-dim-result-v9", "uzi-dim-result-v10"}:
        facts["_uzi_bonus_projection"] = version
    if failure:
        facts["_uzi_raw_result"].update(error="recorded provider error", _timeout=True)
    if spill is not None:
        facts["_uzi_top_level_fields"] = deepcopy(spill)
    if unsafe:
        facts["_uzi_transport_limitations"] = {"bonus_status": "not_fixed_historical"}
    return {"symbol": "688041.SH", "snapshot_type": kind, "snapshot_id": kind,
            "facts": facts}


@pytest.mark.parametrize("bonus,kind", [("similar_stocks", "similar_stocks"), ("fund_managers", "fund_holders")])
@pytest.mark.parametrize("value", [[], None, [{"name": "current", "code": "600001.SH"}]])
def test_selected_owner_beats_unrelated_old_mirror_in_every_order(bonus, kind, value):
    old = {"data": {}, "source": "pipeline:timeout", "_pipeline": {"error": "timeout"}}
    rows = [row("market", spill={bonus: {"present": True, "value": old}}),
            row(kind, data={bonus: value})]
    before = deepcopy(rows)
    for ordered in permutations(rows):
        assert build_uzi_raw(list(ordered), "688041.SH")[bonus] == value
    assert rows == before


def test_old_v4_keeps_frozen_first_write_rule():
    old = {"data": {}, "source": "pipeline:timeout"}
    rows = [row("market", version="uzi-dim-result-v4", spill={"similar_stocks": {"present": True, "value": old}}),
            row("similar_stocks", version="uzi-dim-result-v4", data={"similar_stocks": []})]
    assert build_uzi_raw(rows, "688041.SH")["similar_stocks"] == old


@pytest.mark.parametrize("unsafe", [False, True])
def test_failed_or_unsafe_owner_cannot_borrow_old_success(unsafe):
    rows = [row("market", spill={"similar_stocks": {"present": True, "value": [{"name": "old"}]}}),
            row("similar_stocks", failure=not unsafe, unsafe=unsafe)]
    raw = build_uzi_raw(rows, "688041.SH")
    assert "similar_stocks" not in raw
    assert rows[1]["facts"]["_uzi_raw_result"].get("error") == (None if unsafe else "recorded provider error")


def test_bound_owner_failure_envelope_remains_failure_not_empty_observation():
    failure = {"data": {}, "source": "pipeline:timeout", "_pipeline": {"error": "timeout"}}
    rows = [row("market", spill={"similar_stocks": {"present": True, "value": [{"name": "old"}]}}),
            row("similar_stocks", failure=True, spill={"similar_stocks": {"present": True, "value": failure}})]
    assert build_uzi_raw(rows, "688041.SH")["similar_stocks"] == failure


def test_proven_mirror_remains_usable_when_no_owner_observation_exists():
    rows = [row("market", spill={"fund_managers": {"present": True, "value": None}}), row("fund_holders")]
    raw = build_uzi_raw(rows, "688041.SH")
    assert "fund_managers" in raw and raw["fund_managers"] is None


def test_conflicting_mirrors_without_owner_do_not_choose_by_row_order():
    rows = [row("market", spill={"fund_managers": {"present": True, "value": [{"fund_code": "old"}]}}),
            row("fundamental", spill={"fund_managers": {"present": True, "value": [{"fund_code": "new"}]}})]
    for ordered in permutations(rows):
        assert "fund_managers" not in build_uzi_raw(list(ordered), "688041.SH")


def test_declared_owner_absence_does_not_restore_unrelated_mirror():
    rows = [row("market", spill={"similar_stocks": {"present": True, "value": []}}),
            row("similar_stocks", spill={"similar_stocks": {"present": False}})]
    assert "similar_stocks" not in build_uzi_raw(rows, "688041.SH")


@pytest.mark.parametrize("version", ["uzi-dim-result-v4", "uzi-dim-result-v5", "uzi-dim-result-v6", "uzi-dim-result-v7", "uzi-dim-result-v8", "uzi-dim-result-v9", "uzi-dim-result-v10"])
def test_quant_and_report_use_the_same_owner_with_legacy_version_preserved(version):
    from research_system.market_data.bundles import native_transport_facts
    from research_system.market_data.contracts import cutoff_at_for_date
    from research_system.market_data.native_projection import project_native_result
    from research_system.market_data.quant_auxiliary import raw_quant_inputs
    from research_system.market_data.uzi_adapter import UziSnapshotAdapter
    from tests.market_data.test_native_symbol_transport import _source
    from tests.market_data.test_pit_core import AS_OF

    old, fresh = [{"fund_code": "old"}], [{"fund_code": "fresh"}]
    _, source = _source({"fund_managers": old})
    value = UziSnapshotAdapter().adapt(source, symbol="600519.SH", market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai", as_of_date=AS_OF)
    common = {"as_of_date": AS_OF, "exchange_timezone": "Asia/Shanghai", "status": "available"}
    mirror = SimpleNamespace(**common, id="mirror", dimension="0_basic", payload_json=value.payload,
                             query_manifest_json=value.query_manifest)
    owner = SimpleNamespace(**common, id="owner", dimension="6_fund_holders",
                            payload_json={"raw": {"fund_managers": fresh}}, query_manifest_json={})
    repo = SimpleNamespace(list_snapshot_evidence=lambda identity: value.evidence if identity == "mirror" else [])
    cutoff = cutoff_at_for_date(AS_OF, "Asia/Shanghai")
    snapshots = [mirror, owner]
    rows = [{"symbol": "600519.SH", "snapshot_type": kind, "snapshot_id": s.id,
        "facts": {"_uzi_raw_result": project_native_result(s, version=version),
                  **native_transport_facts(s, evidence=repo.list_snapshot_evidence(s.id), cutoff_at=cutoff,
                                           projection_version=version)}}
        for s, kind in zip(snapshots, ("market", "fund_holders"), strict=True)]
    raw = build_uzi_raw(rows, "600519.SH")
    quant = raw_quant_inputs(repo, symbol="600519.SH", snapshots=snapshots, cutoff_at=cutoff,
                             projection_version=version)
    expected = old if version == "uzi-dim-result-v4" else fresh
    assert raw["fund_managers"] == quant["fund_managers"] == expected


@pytest.mark.parametrize("version", ["uzi-dim-result-v5", "uzi-dim-result-v6", "uzi-dim-result-v7", "uzi-dim-result-v8", "uzi-dim-result-v9", "uzi-dim-result-v10"])
def test_v6_uses_skill_bonus_envelope_without_rewriting_v5(version):
    failure = {"data": {}, "source": "pipeline:timeout", "_pipeline": {"error": "timeout"}}
    rows = [row("market", version=version, data={"name": "recorded"}),
            row("fund_holders", version=version, data={"fund_managers": [], "optional": ""}),
            row("similar_stocks", version=version, failure=True,
                spill={"similar_stocks": {"present": True, "value": failure}})]
    before = deepcopy(rows)
    raw = build_uzi_raw(rows, "688041.SH")
    assert ("6_fund_holders" in raw["dimensions"]) is (version == "uzi-dim-result-v5")
    assert ("code" in raw) is (version == "uzi-dim-result-v5")
    assert ("full" in raw) is (version == "uzi-dim-result-v5")
    assert raw["similar_stocks"] == (failure if version == "uzi-dim-result-v5" else [])
    assert rows == before
    assert rows[2]["facts"]["_uzi_raw_result"]["error"] == "recorded provider error"


def test_v6_does_not_normalize_a_real_native_bonus_value_or_borrow_unsafe_values():
    value = {"native_extension": "not a pipeline envelope"}
    actual = build_uzi_raw([row("similar_stocks", version="uzi-dim-result-v6", data={"similar_stocks": value})], "688041.SH")
    assert actual["similar_stocks"] == value
    unsafe = row("similar_stocks", version="uzi-dim-result-v6", failure=True, unsafe=True,
                 spill={"similar_stocks": {"present": True, "value": {"data": {}, "source": "pipeline:timeout"}}})
    assert "similar_stocks" not in build_uzi_raw([unsafe], "688041.SH")
