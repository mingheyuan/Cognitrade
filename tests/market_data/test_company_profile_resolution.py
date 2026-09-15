"""Choose inputs bound to the selected profile, independently of source quality."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from research_system.market_data.bundles import BundleMaterializer
from research_system.market_data.contracts import SnapshotStatus, canonical_hash
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.resolver import SnapshotResolver
from tests.market_data.test_company_profile_dependencies import (
    publish,
    rebuild,
    receipt,
    revision,
    selected,
)
from tests.market_data.test_pit_core import AS_OF, NOW, _ingest_complete_set, _input
from tests.market_data.test_pit_core import session as session  # noqa: PLC0414


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail("resolution must never access sources/models")
    monkeypatch.setattr("socket.socket.connect", denied)
    monkeypatch.setattr("socket.create_connection", denied)


def failure(ingest, basic, *, mutate=None, with_receipt=True, input_updates=None,
            dimension="7_industry", query_updates=None, **kwargs):
    payload = {"raw": {}, "_uzi_raw_result": {
        "data": {}, "error": "original provider failed", "source": "fixture:native", "_timeout": True,
    }}
    manifest = {"native_quality_status": "error", **(query_updates or {})}
    candidate = SimpleNamespace(payload_json=payload, query_manifest_json=manifest,
        source_version="fixture-v1", validation_json={}, status="data_gap", dimension=dimension)
    if with_receipt:
        manifest["company_profile_dependency_receipt"] = receipt(basic, candidate)
        if mutate:
            manifest["company_profile_dependency_receipt"].update(mutate)
    candidate = _input(dimension, payload=payload, query_manifest=manifest,
        status=SnapshotStatus.DATA_GAP,
        ingested_at=max(NOW, kwargs.get("observed_at", NOW)) + timedelta(seconds=3), **kwargs)
    return ingest.ingest(candidate.model_copy(update=input_updates or {}))


def test_bound_failure_is_selected_over_unrelated_usable_old_industry(session):
    repo, ingest = _ingest_complete_set(session)
    old = selected(repo)["7_industry"]
    old_payload = deepcopy(old.payload_json)
    basic = revision(repo, ingest)
    failed = failure(ingest, basic)
    native_before = project_native_result(failed)

    coverage = SnapshotResolver(repo).coverage("600519.SH", "CN_EQUITY", AS_OF, now=NOW)
    row = next(item for item in coverage.dimensions if item.dimension == "7_industry")
    assert row.snapshot_id == failed.id, "old unrelated success cannot mask the actual new-profile failure"
    assert not row.eligible and row.degraded and row.native_quality == "error"
    native_after = project_native_result(repo.require_snapshot(row.snapshot_id))
    assert native_after == native_before
    assert {key: native_after[key] for key in ("data", "error", "source", "_timeout")} == {
        "data": {}, "error": "original provider failed", "source": "fixture:native", "_timeout": True,
    }
    assert any(item["dimension"] == "7_industry" and item["severity"] == "warning"
               for item in coverage.uzi_integrity["issues"])
    assert repo.require_snapshot(old.id).payload_json == old_payload


@pytest.mark.parametrize("mutation", [
    {"input_basic_snapshot_id": "unrelated-profile"},
    {"input_basic_snapshot_hash": "f" * 64},
    {"dependency_fingerprint": "f" * 64},
    {"raw_result_hash": "f" * 64},
    {"extra": "not-a-valid-receipt"},
])
def test_unverified_receipt_does_not_override_existing_selection(session, mutation):
    repo, ingest = _ingest_complete_set(session)
    old = selected(repo)["7_industry"]
    basic = revision(repo, ingest)
    failure(ingest, basic, mutate=mutation)
    assert selected(repo)["7_industry"].id == old.id


@pytest.mark.parametrize("unsafe", ["revoked", "late", "wrong_zone"])
def test_profile_binding_never_bypasses_pit_or_revocation(session, unsafe):
    repo, ingest = _ingest_complete_set(session)
    old = selected(repo)["7_industry"]
    basic = revision(repo, ingest)
    failed = failure(ingest, basic,
        input_updates={"exchange_timezone": "America/New_York"} if unsafe == "wrong_zone" else {},
        **({"observed_at": NOW + timedelta(days=1)} if unsafe == "late" else {}))
    if unsafe == "revoked":
        failed.revoked_at = NOW
    session.flush()
    assert selected(repo)["7_industry"].id == old.id


def test_old_bundle_remains_exact_even_when_live_resolution_selects_failed_new_dependency(session):
    repo, ingest = _ingest_complete_set(session)
    bundle = publish(repo)
    before = deepcopy(BundleMaterializer(repo).materialize(bundle.id))
    basic = revision(repo, ingest)
    failed = failure(ingest, basic)
    assert selected(repo)["7_industry"].id == failed.id
    assert BundleMaterializer(repo).materialize(bundle.id) == before


def test_no_profile_marker_keeps_existing_usable_preference(session):
    repo, ingest = _ingest_complete_set(session)
    before = selected(repo)
    failure(ingest, before["0_basic"], with_receipt=False)
    assert selected(repo)["7_industry"].id == before["7_industry"].id


def test_business_only_marker_does_not_change_industry_selection(session):
    repo, ingest = _ingest_complete_set(session)
    old = selected(repo)["7_industry"]
    basic = revision(repo, ingest, field="main_business")
    failure(ingest, basic)
    assert selected(repo)["7_industry"].id == old.id


def test_futures_binding_uses_selected_materials_without_promoting_native_quality(session):
    repo, ingest = _ingest_complete_set(session)
    basic = revision(repo, ingest)
    rebuild(repo, ingest, basic, dimensions=["8_materials"])
    materials = selected(repo)["8_materials"]
    failed = failure(ingest, basic, dimension="9_futures", query_updates={
        "company_profile_materials_input": {
            "snapshot_id": materials.id, "snapshot_hash": materials.content_hash,
            "raw_result_hash": canonical_hash(project_native_result(materials, version="uzi-dim-result-v3")),
        },
    })
    coverage = SnapshotResolver(repo).coverage("600519.SH", "CN_EQUITY", AS_OF, now=NOW)
    row = next(item for item in coverage.dimensions if item.dimension == "9_futures")
    assert row.snapshot_id == failed.id
    assert not row.eligible and row.degraded
