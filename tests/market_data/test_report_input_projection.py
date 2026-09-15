from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from research_system.market_data.bundles import BundlePublisher
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import MarketDataEvidence
from research_system.market_data.native_projection import (
    LEGACY_PROJECTION_VERSION,
    PROJECTION_VERSION,
    V2_PROJECTION_VERSION,
    project_native_result,
)
from research_system.market_data.report_inputs import DatabaseReportInputGateway
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.uzi_gate import _native_result
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import _input


def test_provider_failure_projects_identically_and_legacy_keeps_original_envelope():
    snapshot = SimpleNamespace(
        dimension="14_moat",
        status="data_gap",
        source_version="test",
        payload_json={
            "raw": {},
            "_uzi_raw_result": {"data": {}, "_pipeline": {"error": "inner-only"}},
        },
        query_manifest_json={
            "collection_outcome": "retryable_failure",
            "provider_failure": {"message": "upstream connection failed"},
        },
        validation_json={},
    )
    original = deepcopy(snapshot.payload_json)
    legacy = project_native_result(snapshot, version=LEGACY_PROJECTION_VERSION)
    version_two = project_native_result(snapshot, version=V2_PROJECTION_VERSION)
    current = project_native_result(snapshot)
    assert "error" not in legacy
    assert version_two["error"] == "upstream connection failed"
    assert "error" not in current
    assert current == _native_result(snapshot)
    assert current["_pipeline"]["error"] == "inner-only"
    assert snapshot.payload_json == original
    snapshot.payload_json["_uzi_raw_result"]["error"] = "native original error"
    assert project_native_result(snapshot)["error"] == "native original error"
    with pytest.raises(ValueError, match="unsupported snapshot projection"):
        project_native_result(snapshot, version="unknown")


@pytest.mark.parametrize(
    "projection_version", [V2_PROJECTION_VERSION, PROJECTION_VERSION]
)
def test_frozen_manifest_does_not_import_later_evidence_associations(
    projection_version,
):
    database = StrategyDatabase("sqlite:///:memory:")
    database.create_schema()
    try:
        with database.session() as session:
            repository = MarketDataRepository(session)
            ingestion = SnapshotIngestionService(repository)
            snapshots = [
                ingestion.ingest(_input(contract.dimension))
                for contract in DEFAULT_DIMENSION_REGISTRY
            ]
            snapshot_id = snapshots[0].id
            BundlePublisher(repository).publish(
                symbol="600519.SH",
                market="CN_EQUITY",
                as_of_date=date(2026, 8, 20),
                now=datetime(2026, 8, 21, 1, tzinfo=timezone.utc),
            )
        gateway = DatabaseReportInputGateway(
            database,
            clock=lambda: datetime(2026, 8, 21, 1, tzinfo=timezone.utc),
            runtime_settings=SimpleNamespace(
                default_model="fixture-model", provider_name="fixture"
            ),
        )
        args = {
            "symbols": ["600519.SH"],
            "as_of_date": date(2026, 8, 20),
            "tenant_id": "tenant",
            "user_id": "user",
            "workflow_pins": {
                "research_profile": "lite",
                "snapshot_projection_version": projection_version,
            },
            "context": {},
        }
        original = gateway.freeze_report_manifest(**args)
        assert (
            original["workflow_pins"]["snapshot_projection_version"]
            == projection_version
        )
        assert original["workflow_pins"]["agent_workflow_mode"] == "full"
        assert original["workflow_pins"]["model_id"] == "fixture-model"
        assert (
            original["workflow_pins"]["runtime_actual"]["model_id"] == "fixture-model"
        )
        assert original["workflow_pins"]["prompt_hash"]
        assert original["workflow_pins"]["workflow_commit"]
        assert original["items"][0]["context"]["evidence_bindings"]
        with database.session() as session:
            repository = MarketDataRepository(session)
            proof = repository.add_evidence(
                MarketDataEvidence(
                    evidence_key="e" * 64,
                    provider="later",
                    source_url="https://example.test/later",
                    content_hash="a" * 64,
                    source_kind="structured",
                    immutable_proof=True,
                    published_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
                    observed_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
                    grade="A",
                )
            )
            repository.add_snapshot_evidence(
                snapshot_id=snapshot_id,
                evidence_id=proof.id,
                field_path="$.industry",
                purpose="supports",
            )
        loaded = gateway.load_frozen_report_manifest(
            manifest_id=original["manifest_id"],
            manifest_hash=original["manifest_hash"],
            tenant_id="tenant",
            user_id="user",
        )
        assert loaded["items"][0]["snapshots"] == original["items"][0]["snapshots"]
        assert loaded["items"][0]["source_refs"] == original["items"][0]["source_refs"]
        fresh = gateway.freeze_report_manifest(**args)
        assert fresh["manifest_hash"] != original["manifest_hash"]
        assert any(
            row.get("url") == "https://example.test/later"
            for row in fresh["items"][0]["source_refs"]
        )
        mismatched = gateway.freeze_report_manifest(
            **{
                **args,
                "workflow_pins": {
                    "research_profile": "lite",
                    "model_id": "declared-other-model",
                },
            }
        )
        assert mismatched["workflow_pins"]["model_id"] == "declared-other-model"
        assert (
            mismatched["workflow_pins"]["runtime_actual"]["model_id"] == "fixture-model"
        )
        assert "model_id" in mismatched["workflow_pins"]["runtime_pin_mismatches"]
    finally:
        database.close()
