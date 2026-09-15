"""Frozen input consistency after profile revision, without source/model calls."""

import importlib
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from research_system.market_data.bundles import (
    BundleIntegrityError,
    BundleMaterializer,
    BundlePublisher,
    bundle_manifest_hash,
)
from research_system.market_data.contracts import canonical_hash
from research_system.market_data.models import MarketDataBundle, MarketDataBundleItem
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.resolver import SnapshotResolver, _database_utc
from tests.market_data.test_pit_core import AS_OF, NOW, _ingest_complete_set, _input
from tests.market_data.test_pit_core import session as session  # noqa: PLC0414

DIRECT = [
    "3_macro",
    "4_peers",
    "7_industry",
    "8_materials",
    "9_futures",
    "10_valuation",
    "13_policy",
    "similar_stocks",
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*_args, **_kwargs):
        pytest.fail("profile dependency checks must remain offline")

    monkeypatch.setattr("socket.socket.connect", denied)
    monkeypatch.setattr("socket.create_connection", denied)


def check(rows):
    return importlib.import_module(
        "research_system.market_data.company_profile_dependencies"
    ).check_company_profile_dependencies(rows)


def selected(repo):
    coverage = SnapshotResolver(repo).coverage(
        "600519.SH", "CN_EQUITY", AS_OF, now=NOW + timedelta(minutes=2)
    )
    return {
        item.dimension: repo.require_snapshot(item.snapshot_id)
        for item in coverage.dimensions
    }


def revision(repo, ingest, *, bad_fingerprint=False, field="industry", review=()):
    rows = selected(repo)
    old, source = rows["0_basic"], rows["7_industry"]
    payload = deepcopy(old.payload_json)
    new_value = "集成电路设计" if field == "industry" else "集成电路芯片设计及销售"
    payload["raw"][field] = new_value
    if field in payload:
        payload[field] = new_value
    facts = [
        {
            "field": field,
            "snapshot_id": old.id,
            "snapshot_hash": old.content_hash,
            "new_value": new_value,
            "old_exists": field in old.payload_json["raw"],
            "old_value": old.payload_json["raw"].get(field),
        }
    ]
    source_inputs = [{"snapshot_id": source.id, "snapshot_hash": source.content_hash}]
    rebuild = {
        "version": "native-profile-fetch-dependencies-v1",
        "status": "pending",
        "required_dimensions": DIRECT if field == "industry" else [],
        "direct_dimensions": DIRECT if field == "industry" else [],
        "review_dimensions": list(review),
        "derived_stages": ["qualitative_research", "native_panel", "native_review"],
    }
    fingerprint = canonical_hash(
        {
            "version": "company-profile-revision-v1",
            "basic_input": {"snapshot_id": old.id, "snapshot_hash": old.content_hash},
            "profile": {
                field: payload["raw"].get(field)
                for field in ("name", "industry", "main_business")
            },
            "source_inputs": [(source.id, source.content_hash)],
            "facts": facts,
            "rebuild_required": rebuild,
        }
    )
    marker = {
        "version": "company-profile-revision-v1",
        "input_snapshot_id": old.id,
        "input_snapshot_hash": old.content_hash,
        "facts": facts,
        "source_snapshot_inputs": source_inputs,
        "dependency_fingerprint": "f" * 64 if bad_fingerprint else fingerprint,
        "rebuild_required": rebuild,
        "ready_to_freeze": False,
    }
    return ingest.ingest(
        _input(
            "0_basic",
            payload=payload,
            origin="derived",
            ingested_at=NOW + timedelta(seconds=1),
            query_manifest={
                **old.query_manifest_json,
                "company_profile_revision": marker,
            },
        )
    )


def receipt(basic, dependency):
    return {
        "version": "company-profile-dependency-v1",
        "input_basic_snapshot_id": basic.id,
        "input_basic_snapshot_hash": basic.content_hash,
        "dependency_fingerprint": basic.query_manifest_json["company_profile_revision"][
            "dependency_fingerprint"
        ],
        "raw_result_hash": canonical_hash(
            project_native_result(dependency, version="uzi-dim-result-v3")
        ),
    }


def rebuild(repo, ingest, basic, dimensions=None, *, with_receipt=True):
    before = selected(repo)
    required = basic.query_manifest_json["company_profile_revision"][
        "rebuild_required"
    ]["required_dimensions"]
    for dimension in required if dimensions is None else dimensions:
        old = before[dimension]
        payload = deepcopy(old.payload_json)
        payload["industry"] = "集成电路设计"
        # An offline synthetic rebuild result, not a claimed native execution.
        payload["raw"]["profile_dependency_test_generation"] = 2
        manifest = deepcopy(old.query_manifest_json)
        if with_receipt:
            projected = SimpleNamespace(
                payload_json=payload,
                query_manifest_json=manifest,
                source_version=old.source_version,
                validation_json=old.validation_json,
                status=old.status,
                dimension=old.dimension,
            )
            manifest["company_profile_dependency_receipt"] = receipt(basic, projected)
            if dimension == "9_futures":
                materials = before["8_materials"]
                manifest["company_profile_materials_input"] = {
                    "snapshot_id": materials.id, "snapshot_hash": materials.content_hash,
                    "raw_result_hash": canonical_hash(project_native_result(materials, version="uzi-dim-result-v3")),
                }
        before[dimension] = ingest.ingest(
            _input(
                dimension,
                payload=payload,
                query_manifest=manifest,
                ingested_at=NOW + timedelta(seconds=2),
                origin="derived",
            )
        )


def publish(repo):
    return BundlePublisher(repo).publish(
        symbol="600519.SH",
        market="CN_EQUITY",
        as_of_date=AS_OF,
        now=NOW + timedelta(minutes=2),
    )


@pytest.mark.parametrize("label_only", [False, True])
def test_new_basic_and_old_or_relabelled_dependencies_cannot_publish(
    session, label_only
):
    repo, ingest = _ingest_complete_set(session)
    basic = revision(repo, ingest)
    if label_only:
        rebuild(repo, ingest, basic, with_receipt=False)
    before = SnapshotResolver(repo).coverage("600519.SH", "CN_EQUITY", AS_OF, now=NOW)
    assert before.report_generatable and before.uzi_integrity["critical_count"] == 0
    with pytest.raises(
        BundleIntegrityError, match="company profile dependenc"
    ) as caught:
        publish(repo)
    assert {"4_peers", "7_industry"}.issubset(
        caught.value.company_profile_dependencies["pending_dimensions"]
    )
    after = SnapshotResolver(repo).coverage("600519.SH", "CN_EQUITY", AS_OF, now=NOW)
    assert after.uzi_integrity == before.uzi_integrity


def test_matching_rebuilt_receipts_accept_publish_and_materialize(session):
    repo, ingest = _ingest_complete_set(session)
    basic = revision(repo, ingest)
    rebuild(repo, ingest, basic)
    state = check(selected(repo))
    assert state["status"] == "ready" and state["pending_dimensions"] == []
    bundle = publish(repo)
    materialized = BundleMaterializer(repo).materialize(bundle.id)
    assert materialized.snapshots[0]["snapshot_id"] == basic.id
    assert (
        basic.query_manifest_json["company_profile_revision"]["ready_to_freeze"]
        is False
    )


def test_old_bundle_keeps_original_inputs_after_new_profile_revision(session):
    repo, ingest = _ingest_complete_set(session)
    old_bundle = publish(repo)
    before = BundleMaterializer(repo).materialize(old_bundle.id)
    old_hash = old_bundle.manifest_hash
    basic = revision(repo, ingest)
    assert selected(repo)["0_basic"].id == basic.id
    after = BundleMaterializer(repo).materialize(old_bundle.id)
    assert after == before and old_bundle.manifest_hash == old_hash
    assert after.snapshots[0]["snapshot_id"] != basic.id


def test_materializer_rejects_immutable_mixed_bundle_even_with_valid_hashes(session):
    repo, ingest = _ingest_complete_set(session)
    previous = publish(repo)
    basic = revision(repo, ingest)
    rows = selected(repo)
    items = [
        {
            "dimension": old.dimension,
            "ordinal": old.ordinal,
            "snapshot_id": rows[old.dimension].id,
            "content_hash": rows[old.dimension].content_hash,
            "status": rows[old.dimension].status,
            "evidence_grade": rows[old.dimension].evidence_grade,
        }
        for old in repo.list_bundle_items(previous.id)
    ]
    attributes = {
        key: getattr(previous, key)
        for key in (
            "symbol",
            "market",
            "exchange_timezone",
            "as_of_date",
            "cutoff_at",
            "contract_version",
            "status",
            "expected_dimension_count",
            "ready_dimension_count",
            "available_count",
            "verified_empty_count",
            "coverage_json",
            "published_by",
            "published_at",
        )
    }
    digest = bundle_manifest_hash(
        symbol=previous.symbol,
        market=previous.market,
        as_of_date=AS_OF,
        cutoff_at=_database_utc(previous.cutoff_at),
        contract_version=previous.contract_version,
        items=items,
    )
    # This memory-only fixture represents an immutable old-writer artifact;
    # no production Bundle is rewritten to manufacture this test.
    mixed = repo.add_bundle(
        MarketDataBundle(**attributes, manifest_hash=digest, revision=2)
    )
    for item in items:
        repo.add_bundle_item(
            MarketDataBundleItem(
                bundle_id=mixed.id,
                dimension=item["dimension"],
                ordinal=item["ordinal"],
                snapshot_id=item["snapshot_id"],
                snapshot_content_hash=item["content_hash"],
                snapshot_status=item["status"],
                evidence_grade=item["evidence_grade"],
            )
        )
    assert basic.id == items[0]["snapshot_id"]
    with pytest.raises(BundleIntegrityError, match="company profile dependenc"):
        BundleMaterializer(repo).materialize(mixed.id)


@pytest.mark.parametrize(
    "bad",
    [
        "wrong_fingerprint",
        "wrong_basic_id",
        "wrong_basic_hash",
        "wrong_version",
        "other_symbol",
        "other_day",
    ],
)
def test_receipt_and_snapshot_scope_must_match_exact_fixed_basic(session, bad):
    repo, ingest = _ingest_complete_set(session)
    basic = revision(repo, ingest)
    rebuild(repo, ingest, basic)
    original = selected(repo)
    rows = {
        key: SimpleNamespace(
            **{
                field: deepcopy(getattr(value, field))
                for field in (
                    "id",
                    "content_hash",
                    "symbol",
                    "market",
                    "as_of_date",
                    "exchange_timezone",
                    "contract_version",
                    "dimension",
                    "payload_json",
                    "query_manifest_json",
                    "source_version",
                    "validation_json",
                    "status",
                )
            }
        )
        for key, value in original.items()
    }
    changes = {
        "wrong_fingerprint": ("dependency_fingerprint", "e" * 64),
        "wrong_basic_id": ("input_basic_snapshot_id", "old-basic"),
        "wrong_basic_hash": ("input_basic_snapshot_hash", "e" * 64),
        "wrong_version": ("version", "future-unknown"),
    }
    if bad in changes:
        key, value = changes[bad]
        rows["7_industry"].query_manifest_json["company_profile_dependency_receipt"][
            key
        ] = value
    elif bad == "other_symbol":
        rows["7_industry"].symbol = "000001.SZ"
    else:
        rows["7_industry"].as_of_date -= timedelta(days=1)
    state = check(rows)
    assert state["can_freeze"] is False and "7_industry" in state["pending_dimensions"]
    assert check(original)["status"] == "ready"


def test_matching_receipts_do_not_hide_corrupt_profile_fingerprint(session):
    repo, ingest = _ingest_complete_set(session)
    basic = revision(repo, ingest, bad_fingerprint=True)
    rebuild(repo, ingest, basic)
    with pytest.raises(BundleIntegrityError, match="company profile dependenc"):
        publish(repo)
    assert check(selected(repo))["status"] == "invalid"


def test_review_only_dimensions_are_not_a_hard_recollection_gate(session):
    repo, ingest = _ingest_complete_set(session)
    basic = revision(repo, ingest, review=("5_chain", "15_events"))
    rebuild(repo, ingest, basic, DIRECT)
    state = check(selected(repo))
    assert state["status"] == "ready" and state["pending_dimensions"] == []


def test_copied_receipt_does_not_validate_changed_native_output(session):
    repo, ingest = _ingest_complete_set(session)
    basic = revision(repo, ingest)
    rebuild(repo, ingest, basic)
    original = selected(repo)
    rows = dict(original)
    previous = rows["7_industry"]
    rows["7_industry"] = SimpleNamespace(
        **{
            field: deepcopy(getattr(previous, field))
            for field in (
                "id",
                "content_hash",
                "symbol",
                "market",
                "as_of_date",
                "exchange_timezone",
                "contract_version",
                "dimension",
                "payload_json",
                "query_manifest_json",
                "source_version",
                "validation_json",
                "status",
            )
        }
    )
    rows["7_industry"].payload_json["raw"]["industry_pe"] = 98765
    status = check(rows)
    assert (
        status["can_freeze"] is False and "7_industry" in status["pending_dimensions"]
    )
    assert check(original)["status"] == "ready"


def test_main_business_only_does_not_force_raw_recollection(session):
    repo, ingest = _ingest_complete_set(session)
    before = selected(repo)
    basic = revision(repo, ingest, field="main_business")
    status = check(selected(repo))
    assert status["status"] == "ready" and status["required_dimensions"] == []
    bundle = publish(repo)
    frozen = BundleMaterializer(repo).materialize(bundle.id)
    assert frozen.snapshots[0]["snapshot_id"] == basic.id
    assert all(
        row["snapshot_id"] == before[row["dimension"]].id
        for row in frozen.snapshots[1:]
    )
    assert basic.query_manifest_json["company_profile_revision"]["rebuild_required"][
        "derived_stages"
    ]


@pytest.mark.parametrize(
    "change",
    [
        "missing_version",
        "unsupported_version",
        "changed_required_range",
        "alias_mismatch",
    ],
)
def test_unreleased_or_tampered_dependency_protocol_cannot_freeze(session, change):
    repo, ingest = _ingest_complete_set(session)
    basic = revision(repo, ingest)
    rows = selected(repo)
    clone = SimpleNamespace(
        **{
            field: deepcopy(getattr(basic, field))
            for field in (
                "id",
                "content_hash",
                "dimension",
                "payload_json",
                "query_manifest_json",
            )
        }
    )
    required = clone.query_manifest_json["company_profile_revision"]["rebuild_required"]
    if change == "missing_version":
        required.pop("version")
    elif change == "unsupported_version":
        required["version"] = "future-unconfirmed-dependency-rules"
    elif change == "changed_required_range":
        required["required_dimensions"] = required["direct_dimensions"] = []
    else:
        required["direct_dimensions"] = []
    rows["0_basic"] = clone
    status = check(rows)
    assert status["status"] == "invalid" and status["can_freeze"] is False


def test_partial_7_4_rebuild_keeps_other_actual_consumers_pending(session):
    repo, ingest = _ingest_complete_set(session)
    basic = revision(repo, ingest)
    rebuild(repo, ingest, basic, ["7_industry", "4_peers"])
    status = check(selected(repo))
    assert status["status"] == "pending"
    assert set(status["pending_dimensions"]) == set(DIRECT) - {"7_industry", "4_peers"}
    assert (
        "5_chain" not in status["pending_dimensions"]
        and "15_events" not in status["pending_dimensions"]
    )


def test_old_snapshots_without_profile_marker_are_fully_compatible(session):
    repo, _ = _ingest_complete_set(session)
    rows = selected(repo)
    before = [
        (row.id, row.content_hash, deepcopy(row.query_manifest_json))
        for row in rows.values()
    ]
    assert check(rows)["status"] == "not_required"
    assert check(list(rows.values()))["can_freeze"] is True
    assert before == [
        (row.id, row.content_hash, row.query_manifest_json) for row in rows.values()
    ]


def test_real_profile_revision_marker_is_accepted_as_pending(session, tmp_path):
    from tests.market_data.test_company_profile_revision import (
        persist,
        proposal_for,
        scope_for,
        seed,
    )

    state = seed(session, tmp_path)
    scope = scope_for(state)
    result = persist(state, scope, [proposal_for(state, scope)])
    assert result["rejected"] == []
    basic = state.repo.require_snapshot(result["revisions"][0]["snapshot_id"])
    status = check([basic, state.source])
    assert (
        status["status"] == "pending"
        and status["dependency_fingerprint"] == result["dependency_fingerprint"]
    )
    assert set(status["pending_dimensions"]) == set(
        result["rebuild_required"]["required_dimensions"]
    )
