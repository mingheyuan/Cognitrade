"""Two actual profile revisions must retain unsettled dependency obligations.

All snapshots/receipts are synthetic, in-memory and offline. The original
red run is retained in the create-only lineage audit directory.
"""
from copy import deepcopy
from datetime import timedelta
import json
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from research_system.market_data.bundles import CompanyProfileDependencyError, _require_company_profile_dependencies
from research_system.market_data.company_profile_dependencies import check_company_profile_dependencies, _native_result_hash
from research_system.market_data.company_profile_revision import CompanyProfileRefusal, _evidence_input, persist_company_profile_revisions
from research_system.market_data.contracts import canonical_hash
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY, DIMENSION_IDS
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import MarketDataSnapshot
from tests.market_data.test_company_profile_dependencies import receipt
from tests.market_data.test_company_profile_revision import proposal_for, scope_for, seed
from tests.market_data.test_pit_core import NOW, _input
from tests.market_data.test_pit_core import session as session  # noqa: PLC0414


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("lineage audit must not call sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def start(session, tmp_path):
    state = seed(session, tmp_path)
    rows = {"0_basic": state.basic, "7_industry": state.source}
    ingest = SnapshotIngestionService(state.repo)
    rows.update({key: ingest.ingest(_input(key)) for key in DIMENSION_IDS if key not in rows})
    return state, rows


def revise(state, field, generation, value=None):
    scope = scope_for(state)
    result = persist_company_profile_revisions(
        state.repo, proposals=[proposal_for(state, scope, field, value=value)], scope=scope,
        authorized_basic_snapshot_ids={state.basic.id}, registry=DEFAULT_DIMENSION_REGISTRY,
        job_id=f"offline-lineage-generation-{generation}", now=NOW + timedelta(seconds=generation),
    )
    assert result["rejected"] == [] and len(result["revisions"]) == 1
    state.basic = state.repo.require_snapshot(result["revisions"][0]["snapshot_id"])
    return state.basic


def bind_receipts(state, rows, basic, dimensions):
    """Synthetic producer attestations, not a claimed native/source execution."""
    result = dict(rows)
    ingest = SnapshotIngestionService(state.repo)
    for key in DIMENSION_IDS:
        if key not in dimensions:
            continue
        old = rows[key]
        manifest = deepcopy(old.query_manifest_json)
        manifest["company_profile_dependency_receipt"] = receipt(basic, old)
        if key == "9_futures":
            materials = result["8_materials"]
            manifest["company_profile_materials_input"] = {
                "snapshot_id": materials.id, "snapshot_hash": materials.content_hash,
                "raw_result_hash": _native_result_hash(materials),
            }
        result[key] = ingest.ingest(_input(
            key, payload=deepcopy(old.payload_json), query_manifest=manifest,
            origin="derived", ingested_at=NOW + timedelta(seconds=10),
        ))
    return result


def record(record_property, first, second, before, after):
    record_property("lineage_observation", json.dumps({
        "parent_basic": first.id, "parent_hash": first.content_hash,
        "child_basic": second.id, "child_hash": second.content_hash,
        "parent_required": first.query_manifest_json["company_profile_revision"]["rebuild_required"]["required_dimensions"],
        "child_required": second.query_manifest_json["company_profile_revision"]["rebuild_required"]["required_dimensions"],
        "before": before, "after": after,
    }, ensure_ascii=False, sort_keys=True))


def test_business_revision_cannot_erase_pending_industry_obligations(session, tmp_path, record_property):
    state, rows = start(session, tmp_path)
    first = revise(state, "industry", 1)
    rows["0_basic"] = first
    before = check_company_profile_dependencies(rows)
    assert before["can_freeze"] is False and {"7_industry", "4_peers"} <= set(before["pending_dimensions"])
    first_hash, source_hash = first.content_hash, state.source.content_hash
    second = revise(state, "main_business", 2)
    rows["0_basic"] = second
    after = check_company_profile_dependencies(rows)
    record(record_property, first, second, before, after)
    assert second.payload_json["raw"]["industry"] == first.payload_json["raw"]["industry"]
    assert first.content_hash == first_hash and state.source.content_hash == source_hash
    assert after["can_freeze"] is False
    assert set(after["pending_dimensions"]) == set(before["pending_dimensions"])


def test_actual_bundle_guard_still_rejects_old_industry_and_peers_after_second_revision(session, tmp_path):
    state, rows = start(session, tmp_path)
    first = revise(state, "industry", 1)
    rows["0_basic"] = first
    with pytest.raises(CompanyProfileDependencyError):
        _require_company_profile_dependencies(rows)
    rows["0_basic"] = revise(state, "main_business", 2)
    with pytest.raises(CompanyProfileDependencyError):
        _require_company_profile_dependencies(rows)


def test_partial_parent_receipts_are_reused_without_dropping_remaining_obligations(session, tmp_path, record_property):
    state, rows = start(session, tmp_path)
    first = revise(state, "industry", 1)
    rows["0_basic"] = first
    rows = bind_receipts(state, rows, first, {"7_industry", "4_peers"})
    before = check_company_profile_dependencies(rows)
    assert before["can_freeze"] is False
    assert not {"7_industry", "4_peers"}.intersection(before["pending_dimensions"])
    second = revise(state, "main_business", 2)
    rows["0_basic"] = second
    after = check_company_profile_dependencies(rows)
    record(record_property, first, second, before, after)
    assert set(after["pending_dimensions"]) == set(before["pending_dimensions"])
    assert after["can_freeze"] is False


def test_completed_parent_inputs_do_not_require_new_raw_collection_for_business_only_change(session, tmp_path, record_property):
    state, rows = start(session, tmp_path)
    first = revise(state, "industry", 1)
    rows["0_basic"] = first
    required = first.query_manifest_json["company_profile_revision"]["rebuild_required"]["required_dimensions"]
    rows = bind_receipts(state, rows, first, required)
    before = check_company_profile_dependencies(rows)
    assert before["can_freeze"] is True
    fixed_dependencies = {key: (row.id, row.content_hash) for key, row in rows.items() if key != "0_basic"}
    second = revise(state, "main_business", 2)
    rows["0_basic"] = second
    after = check_company_profile_dependencies(rows)
    record(record_property, first, second, before, after)
    assert after["can_freeze"] is True and after["pending_dimensions"] == []
    assert fixed_dependencies == {key: (row.id, row.content_hash) for key, row in rows.items() if key != "0_basic"}
    assert second.query_manifest_json["company_profile_revision"]["rebuild_required"]["derived_stages"]


def test_completed_parent_receipt_set_cannot_be_swapped_back_to_old_industry(session, tmp_path, record_property):
    state, old_rows = start(session, tmp_path)
    first = revise(state, "industry", 1)
    old_rows["0_basic"] = first
    required = first.query_manifest_json["company_profile_revision"]["rebuild_required"]["required_dimensions"]
    rows = bind_receipts(state, old_rows, first, required)
    assert check_company_profile_dependencies(rows)["can_freeze"] is True
    rows["7_industry"] = old_rows["7_industry"]
    before = check_company_profile_dependencies(rows)
    assert before["pending_dimensions"] == ["7_industry"]
    second = revise(state, "main_business", 2)
    rows["0_basic"] = second
    after = check_company_profile_dependencies(rows)
    record(record_property, first, second, before, after)
    assert after["can_freeze"] is False and after["pending_dimensions"] == ["7_industry"]


def test_third_generation_name_resets_only_name_consumers_and_accepts_intermediate_anchor(session, tmp_path):
    state, rows = start(session, tmp_path)
    first = revise(state, "industry", 1)
    rows["0_basic"] = first
    required = check_company_profile_dependencies(rows)["required_dimensions"]
    rows = bind_receipts(state, rows, first, required)
    second = revise(state, "main_business", 2)
    rows["0_basic"] = second
    rows = bind_receipts(state, rows, second, {"7_industry"})
    third = revise(state, "name", 3)
    rows["0_basic"] = third
    checked = check_company_profile_dependencies(rows)
    assert set(checked["pending_dimensions"]) == {"4_peers", "14_moat", "15_events", "17_sentiment", "18_trap"}
    rows = bind_receipts(state, rows, third, checked["pending_dimensions"])
    assert check_company_profile_dependencies(rows)["can_freeze"]
    fourth = revise(state, "main_business", 4, value="芯片设计及销售")
    rows["0_basic"] = fourth
    assert check_company_profile_dependencies(rows)["can_freeze"]
    # Guard uses only frozen rows. It never asks for a newer DB basic head.
    assert rows["7_industry"].query_manifest_json["company_profile_dependency_receipt"]["input_basic_snapshot_id"] == second.id


def test_nonempty_industry_change_preserves_lhb_truthiness_anchor_only(session, tmp_path):
    state, rows = start(session, tmp_path)
    first = revise(state, "industry", 1)
    rows["0_basic"] = first
    rows = bind_receipts(state, rows, first, check_company_profile_dependencies(rows)["required_dimensions"])
    second = revise(state, "industry", 2, value="数字芯片设计")
    rows["0_basic"] = second
    checked = check_company_profile_dependencies(rows)
    assert "16_lhb" in checked["required_dimensions"] and "16_lhb" not in checked["pending_dimensions"]
    assert set(checked["pending_dimensions"]) == set(checked["required_dimensions"]) - {"16_lhb"}


def test_current_basic_receipts_can_complete_inherited_pending_consumers(session, tmp_path):
    state, rows = start(session, tmp_path)
    revise(state, "industry", 1)
    second = revise(state, "main_business", 2)
    rows["0_basic"] = second
    rows = bind_receipts(state, rows, second, check_company_profile_dependencies(rows)["required_dimensions"])
    assert check_company_profile_dependencies(rows)["can_freeze"]


@pytest.mark.parametrize("damage", ["missing", "wrong_id", "wrong_hash", "wrong_raw", "new_materials", "missing_materials"])
def test_futures_binds_exact_fixed_materials_even_when_basic_anchor_matches(session, tmp_path, damage):
    state, rows = start(session, tmp_path)
    rows["0_basic"] = revise(state, "industry", 1)
    rows = bind_receipts(state, rows, state.basic, check_company_profile_dependencies(rows)["required_dimensions"])
    assert check_company_profile_dependencies(rows)["can_freeze"]
    if damage == "new_materials":
        previous = rows["8_materials"]
        rows["8_materials"] = SnapshotIngestionService(state.repo).ingest(_input(
            "8_materials", payload=deepcopy(previous.payload_json),
            query_manifest={**deepcopy(previous.query_manifest_json), "offline_retry": "second-run"},
        ))
        assert rows["8_materials"].id != previous.id
    elif damage == "missing_materials":
        rows.pop("8_materials")
    else:
        row = rows["9_futures"]
        manifest = deepcopy(row.query_manifest_json)
        if damage == "missing":
            manifest.pop("company_profile_materials_input")
        else:
            key = {"wrong_id": "snapshot_id", "wrong_hash": "snapshot_hash", "wrong_raw": "raw_result_hash"}[damage]
            manifest["company_profile_materials_input"][key] = "f" * 64
        rows["9_futures"] = SimpleNamespace(**{**row.__dict__, "query_manifest_json": manifest})
    checked = check_company_profile_dependencies(rows)
    assert "9_futures" in checked["pending_dimensions"]


def refingerprint(payload, marker):
    marker["dependency_fingerprint"] = canonical_hash({
        "version": marker["version"],
        "basic_input": {"snapshot_id": marker["input_snapshot_id"], "snapshot_hash": marker["input_snapshot_hash"]},
        "profile": {field: payload["raw"].get(field) for field in ("name", "industry", "main_business")},
        "source_inputs": sorted((row["snapshot_id"], row["snapshot_hash"]) for row in marker["source_snapshot_inputs"]),
        "facts": marker["facts"], "rebuild_required": marker["rebuild_required"],
    })


@pytest.mark.parametrize("damage,reason", [
    ("quote", "excerpt_mismatch"), ("price", "profile_lineage_payload_changed"),
    ("summary", "profile_lineage_summary_mismatch"),
])
def test_valid_hash_and_fingerprint_do_not_replace_ancestor_fact_validation(session, tmp_path, damage, reason):
    state, _ = start(session, tmp_path)
    first = revise(state, "industry", 1)
    payload, manifest = deepcopy(first.payload_json), deepcopy(first.query_manifest_json)
    marker = manifest["company_profile_revision"]
    if damage == "quote":
        marker["facts"][0]["citations"][0]["quote"] += "伪造"
    elif damage == "price":
        payload["raw"]["price"] = 999999
    else:
        marker["rebuild_required"]["required_dimensions"] = []
        marker["rebuild_required"]["direct_dimensions"] = []
    refingerprint(payload, marker)
    state.basic = SnapshotIngestionService(state.repo).ingest(_input(
        "0_basic", payload=payload, query_manifest=manifest,
        evidence=[_evidence_input(state.proof)],
    ))
    with pytest.raises(CompanyProfileRefusal, match=reason):
        scope_for(state)


def test_scope_rechecks_ancestor_payload_hash_after_freezing(session, tmp_path):
    state, _ = start(session, tmp_path)
    original = state.basic
    revise(state, "industry", 1)
    scope = scope_for(state)
    # Bypass the normal immutable ORM guard only to simulate storage corruption.
    state.repo.session.execute(update(MarketDataSnapshot).where(MarketDataSnapshot.id == original.id)
                               .values(payload_json={**original.payload_json, "tampered": True}))
    state.repo.session.expire(original)
    result = persist_company_profile_revisions(
        state.repo, proposals=[proposal_for(state, scope, "main_business")], scope=scope,
        authorized_basic_snapshot_ids={state.basic.id}, registry=DEFAULT_DIMENSION_REGISTRY,
        job_id="offline-tampered-ancestor", now=NOW,
    )
    assert result["revisions"] == []
    assert result["rejected"][0]["reason"] == "input_content_hash_mismatch"


def test_parent_cycle_is_diagnosed_without_recursive_loop(session, tmp_path, monkeypatch):
    import research_system.market_data.company_profile_revision as profile
    state, _ = start(session, tmp_path)
    first = revise(state, "industry", 1)
    # Exercise the graph check directly; self hashes cannot form a real SHA256
    # fixed point, so the separate full-content check is covered above.
    marker = deepcopy(first.query_manifest_json["company_profile_revision"])
    marker["input_snapshot_id"] = first.id
    for fact in marker["facts"]:
        fact["snapshot_id"] = first.id
    refingerprint(first.payload_json, marker)
    view = SimpleNamespace(**{**first.__dict__, "query_manifest_json": {"company_profile_revision": marker}})
    with pytest.raises(CompanyProfileRefusal, match="profile_lineage_cycle"):
        profile._profile_lineage(state.repo, view, NOW)


def test_cancellation_during_ancestor_validation_produces_no_revision(session, tmp_path, monkeypatch):
    state, _ = start(session, tmp_path)
    original = state.basic
    revise(state, "industry", 1)
    scope = scope_for(state)
    event, real = Event(), state.repo.require_snapshot
    def cancelling(snapshot_id):
        row = real(snapshot_id)
        if snapshot_id == original.id:
            event.set()
        return row
    monkeypatch.setattr(state.repo, "require_snapshot", cancelling)
    result = persist_company_profile_revisions(
        state.repo, proposals=[proposal_for(state, scope, "main_business")], scope=scope,
        authorized_basic_snapshot_ids={state.basic.id}, registry=DEFAULT_DIMENSION_REGISTRY,
        job_id="offline-cancelled-lineage", now=NOW, cancellation=event,
    )
    assert result["revisions"] == [] and result["rejected"][0]["reason"] == "cancelled"


@pytest.mark.parametrize("damage", ["parent", "anchor_hash", "consumer_hash", "range", "duplicate_input"])
def test_guard_rejects_malformed_frozen_lineage_even_with_recomputed_fingerprint(session, tmp_path, damage):
    state, rows = start(session, tmp_path)
    first = revise(state, "industry", 1)
    rows["0_basic"] = first
    rows = bind_receipts(state, rows, first, check_company_profile_dependencies(rows)["required_dimensions"])
    second = revise(state, "main_business", 2)
    manifest = deepcopy(second.query_manifest_json)
    marker = manifest["company_profile_revision"]
    lineage = marker["rebuild_required"]["dependency_lineage"]
    if damage == "parent":
        lineage["parent_basic"]["snapshot_id"] = "unbound-parent"
    elif damage == "anchor_hash":
        lineage["consumer_anchors"]["7_industry"][0]["input_basic_snapshot_hash"] = "f" * 64
    elif damage == "consumer_hash":
        lineage["consumer_anchors"]["7_industry"][0]["profile_input_hash"] = "f" * 64
    elif damage == "range":
        lineage["consumer_anchors"].pop("7_industry")
    else:
        lineage["validated_inputs"].append(deepcopy(lineage["validated_inputs"][0]))
    refingerprint(second.payload_json, marker)
    rows["0_basic"] = SimpleNamespace(**{**second.__dict__, "query_manifest_json": manifest})
    checked = check_company_profile_dependencies(rows)
    assert checked["status"] == "invalid" and not checked["can_freeze"]
    assert checked["issues"][0]["reason"].startswith("profile_lineage_")


def test_scope_lineage_cannot_be_replaced_by_forged_anchor_strings(session, tmp_path):
    state, _ = start(session, tmp_path)
    revise(state, "industry", 1)
    scope = scope_for(state)
    scope["dependency_lineage"]["consumer_anchors"]["7_industry"][0]["dependency_fingerprint"] = "f" * 64
    scope["scope_hash"] = canonical_hash({key: value for key, value in scope.items() if key != "scope_hash"})
    result = persist_company_profile_revisions(
        state.repo, proposals=[proposal_for(state, scope, "main_business")], scope=scope,
        authorized_basic_snapshot_ids={state.basic.id}, registry=DEFAULT_DIMENSION_REGISTRY,
        job_id="offline-forged-lineage", now=NOW,
    )
    assert result["revisions"] == [] and result["rejected"][0]["reason"] == "scope_binding_changed"


def test_revoked_ancestor_after_scope_build_blocks_new_revision(session, tmp_path):
    state, _ = start(session, tmp_path)
    original = state.basic
    revise(state, "industry", 1)
    scope = scope_for(state)
    original.revoked_at = NOW
    result = persist_company_profile_revisions(
        state.repo, proposals=[proposal_for(state, scope, "main_business")], scope=scope,
        authorized_basic_snapshot_ids={state.basic.id}, registry=DEFAULT_DIMENSION_REGISTRY,
        job_id="offline-revoked-ancestor", now=NOW,
    )
    assert result["revisions"] == [] and result["rejected"][0]["reason"] == "input_revoked"


@pytest.mark.parametrize("raw_result", [{"data": [], "source": "empty"}, {"data": None, "error": "offline-error"}])
def test_futures_can_bind_legitimate_empty_or_failed_materials_without_quality_gate(session, tmp_path, raw_result):
    state, rows = start(session, tmp_path)
    rows["0_basic"] = revise(state, "industry", 1)
    old = rows["8_materials"]
    payload = deepcopy(old.payload_json)
    payload["_uzi_raw_result"] = raw_result
    rows["8_materials"] = SnapshotIngestionService(state.repo).ingest(_input("8_materials", payload=payload))
    rows = bind_receipts(state, rows, state.basic, check_company_profile_dependencies(rows)["required_dimensions"])
    assert check_company_profile_dependencies(rows)["can_freeze"]
