from __future__ import annotations

from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.tools.snapshot_collector import (
    _annotate_point_in_time_snapshot,
)
from research_system.market_data.contracts import cutoff_at_for_date
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.market_data.uzi_gate import native_payload
from research_system.strategies.contracts import StrategyDocumentV2
from research_system.strategies.orchestration import (
    ReportOrchestrationService,
    frozen_reference_price_evidence,
)
from research_system.strategies.report_conditions import report_condition_calculation


def fixture_input():
    basic_data = {"code": "688012.SH", "price": 180.25}
    basic = {
        "dimension_id": "0_basic",
        "snapshot_id": "basic-exact",
        "symbol": "688012.SH",
        "snapshot_date": "2026-09-02",
        "content_hash": "a" * 64,
        "facts": {
            "_uzi_raw_result": {"ticker": "688012.SH", "data": basic_data},
            "_market_data_audit": {
                "normalized_payload": {
                    "currency": "CNY",
                    "raw": {
                        "market_hist": {
                            "adjust": "",
                            "latest_bar": {"date": "2026-09-02", "close": 180.25},
                        }
                    },
                }
            },
        },
        "query_manifest": {"provider": "akshare_market", "pit_safe": True},
        "source_refs": [
            {
                "id": "proof-exact",
                "provider": "akshare_market",
                "content_hash": "c" * 64,
            }
        ],
    }
    manifest = {
        "manifest_id": "manifest-exact",
        "manifest_hash": "f" * 64,
        "as_of_date": "2026-09-02",
        "items": [
            {
                "symbol": "688012.SH",
                "bundle_id": "bundle-exact",
                "bundle_manifest_hash": "b" * 64,
                "bundle": {
                    "symbol": "688012.SH",
                    "market": "CN_EQUITY",
                    "as_of_date": "2026-09-02",
                    "manifest_hash": "b" * 64,
                    "items": [
                        {
                            "dimension_id": "0_basic",
                            "snapshot_id": "basic-exact",
                            "content_hash": "a" * 64,
                        }
                    ],
                },
                "snapshots": [basic],
            }
        ],
    }
    content = {
        "results": [
            {
                "symbol": "688012",
                "raw": {"dimensions": {"0_basic": {"data": deepcopy(basic_data)}}},
            }
        ]
    }
    return manifest, content


def test_fixed_basic_original_price_is_verified_without_replacing_native_values():
    manifest, content = fixture_input()
    original = deepcopy((manifest, content))
    proof = frozen_reference_price_evidence(manifest, "688012", content)
    assert proof == {
        "status": "verified",
        "subject_code": "688012",
        "source_field": "0_basic.data.price",
        "bundle_id": "bundle-exact",
        "bundle_manifest_hash": "b" * 64,
        "snapshot_id": "basic-exact",
        "snapshot_content_hash": "a" * 64,
        "report_manifest_id": "manifest-exact",
        "report_manifest_hash": "f" * 64,
        "price": 180.25,
        "native_anchor_price": 180.25,
        "native_source_path": "results[].raw.dimensions.0_basic.data.price",
        "trade_date": "2026-09-02",
        "as_of_date": "2026-09-02",
        "provider": "akshare_market",
        "basis": "raw_unadjusted",
        "currency": "CNY",
        "price_unit": "per_share",
        "currency_basis": "CN_EQUITY_instrument_contract",
        "price_unit_basis": "CN_EQUITY.market_hist.close",
        "source_evidence": [{"evidence_id": "proof-exact", "content_hash": "c" * 64}],
    }
    assert (manifest, content) == original


@pytest.mark.parametrize(
    "content",
    [
        {},
        {"results": []},
        {"failure_phase": "input_preflight", "report_allowed": False},
        {"results": [{"symbol": "688012", "raw": {}, "synthesis": {}}]},
    ],
)
def test_absent_native_report_does_not_invent_a_price_conflict_or_collection(content):
    manifest, _ = fixture_input()
    proof = frozen_reference_price_evidence(manifest, "688012", content)
    assert proof["status"] == "not_evaluated"
    assert proof["reason"] == "missing_native_report"
    assert "suggested_collection" not in proof
    # It is still not a verified executable-price proof.
    assert "price" not in proof and "basis" not in proof


def test_present_native_report_with_missing_basic_price_is_not_called_a_mismatch():
    manifest, content = fixture_input()
    content["results"][0]["raw"]["dimensions"]["0_basic"]["data"].pop("price")
    proof = frozen_reference_price_evidence(manifest, "688012", content)
    assert proof["status"] == "unavailable"
    assert proof["reason"] == "native_basic_price_missing"


def test_real_historical_annotation_adapter_and_projection_keep_original_bar_proof():
    as_of = date(2026, 9, 2)
    snapshot = _annotate_point_in_time_snapshot(
        {
            "symbol": "688012.SH",
            "snapshot_type": "market",
            "source": {"provider_id": "akshare_market"},
            "facts": {
                "availability": "available",
                "symbol": "688012.SH",
                "market_hist": {
                    "adjust": "",
                    "latest_bar": {"date": "2026-09-02", "close": 180.25},
                },
                "market_overview": {
                    "latest_price": 180.25,
                    "security_name": "fixture security",
                    "industry": "半导体",
                    "market_cap": 1000000,
                    "total_shares": 10000,
                },
            },
            "source_trace": [
                {"provider_id": "akshare_market", "status": "available", "count": 1}
            ],
        },
        snapshot_type="market",
        as_of_date=as_of,
        cutoff_at=cutoff_at_for_date(as_of, "Asia/Shanghai"),
    )
    ingested = UziSnapshotAdapter().adapt(
        snapshot,
        symbol="688012.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=as_of,
    )
    assert "market_hist" not in snapshot["facts"]["_uzi_raw_result"]["data"]
    # _raw_data merges public provider facts after preserving the native envelope.
    assert ingested.payload["raw"]["market_hist"]["adjust"] == ""
    projected = SimpleNamespace(
        dimension="0_basic",
        status=ingested.status.value,
        payload_json=ingested.payload,
        query_manifest_json=ingested.query_manifest,
        validation_json=ingested.validation,
        source_version=ingested.source_version,
    )
    manifest, content = fixture_input()
    basic = manifest["items"][0]["snapshots"][0]
    basic["facts"] = {
        **native_payload(projected),
        "_uzi_raw_result": project_native_result(projected),
        "_market_data_audit": {"normalized_payload": ingested.payload},
    }
    basic["query_manifest"] = ingested.query_manifest
    basic["source_refs"] = [
        {
            "id": f"fixture-proof-{i}",
            "provider": item.provider,
            "content_hash": item.content_hash,
        }
        for i, item in enumerate(ingested.evidence)
    ]
    proof = frozen_reference_price_evidence(manifest, "688012", content)
    assert proof["status"] == "verified", proof
    assert proof["price"] == 180.25
    assert proof["currency_basis"] == "CN_EQUITY_instrument_contract"


@pytest.mark.parametrize(
    "change,reason",
    [
        ("qfq", "basic_price_unadjusted_basis_unproven"),
        ("missing_adjust", "basic_price_unadjusted_basis_unproven"),
        ("missing_trade_date", "basic_price_trade_date_unproven"),
        ("future_trade_date", "basic_price_trade_date_unproven"),
        ("different_close", "basic_native_price_not_backed_by_raw_bar"),
        ("different_native_price", "native_basic_price_differs_from_frozen_input"),
        ("wrong_bundle", "frozen_basic_stock_identity_mismatch"),
        ("wrong_basic_code", "frozen_basic_stock_identity_mismatch"),
        ("different_hash", "frozen_reference_hash_or_binding_missing"),
        ("unproven_provider", "basic_price_provider_or_pit_proof_missing"),
    ],
)
def test_reference_price_fails_closed_instead_of_assuming_a_raw_basis(change, reason):
    manifest, content = fixture_input()
    item = manifest["items"][0]
    basic = item["snapshots"][0]
    history = basic["facts"]["_market_data_audit"]["normalized_payload"]["raw"][
        "market_hist"
    ]
    if change == "qfq":
        history["adjust"] = "qfq"
    elif change == "missing_adjust":
        history.pop("adjust")
    elif change == "missing_trade_date":
        history["latest_bar"].pop("date")
    elif change == "future_trade_date":
        history["latest_bar"]["date"] = "2026-09-03"
    elif change == "different_close":
        history["latest_bar"]["close"] = 180
    elif change == "different_native_price":
        content["results"][0]["raw"]["dimensions"]["0_basic"]["data"]["price"] = 180
    elif change == "wrong_bundle":
        item["bundle"]["symbol"] = "600519.SH"
    elif change == "wrong_basic_code":
        basic["facts"]["_uzi_raw_result"]["data"]["code"] = "600519.SH"
    elif change == "different_hash":
        basic["content_hash"] = "d" * 64
    else:
        basic["source_refs"] = []
    proof = frozen_reference_price_evidence(manifest, "688012", content)
    assert proof["status"] == "unavailable"
    assert proof["reason"] == reason
    assert proof["suggested_collection"]["dimensions"] == ["0_basic"]


def test_uzi_source_and_canonical_reuse_bind_the_same_frozen_input():
    manifest, content = fixture_input()
    service = object.__new__(ReportOrchestrationService)
    service._run_artifacts = lambda *args, **kwargs: [
        {"artifact_type": "uzi_native_analysis", "content": content, "confidence": 0.8}
    ]
    source, *_ = service._uzi_source(
        None,
        {"symbol": "688012", "child_run_id": "run-exact"},
        previous=None,
        report_manifest=manifest,
    )
    assert source["reference_price_evidence"]["status"] == "verified"
    previous = SimpleNamespace(
        source_json=source,
        artifact_id=None,
        content_hash=None,
        confidence=0.8,
        data_gaps_json=[],
    )
    service._run_artifacts = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("reuse must not query external inputs")
    )
    reused, *_ = service._uzi_source(
        None,
        {"symbol": "688012", "canonical_reused": True},
        previous=previous,
        report_manifest=manifest,
    )
    assert reused["reference_price_evidence"] == source["reference_price_evidence"]
    unavailable, *_ = service._uzi_source(
        None,
        {"symbol": "688012", "canonical_reused": True},
        previous=previous,
        report_manifest=None,
    )
    assert unavailable["reference_price_evidence"]["status"] == "unavailable"


def test_fixed_original_price_proof_reaches_native_signal_without_price_rewrite():
    manifest, content = fixture_input()
    anchor = 180.25
    battle_plan = {
        key: f"¥{round(anchor * factor, 2):.2f}"
        for key, factor in (("entry", 0.92), ("stop", 0.85), ("target", 1.25))
    }
    content["results"][0]["synthesis"] = {
        "ticker": "688012.SH",
        "verdict_label": "可以蹲（偏弱）",
        "overall_score": 65,
        "dashboard": {"battle_plan": battle_plan},
    }
    proof = frozen_reference_price_evidence(manifest, "688012", content)
    node = SimpleNamespace(
        id="node",
        subject_code="688012",
        status="completed",
        confidence=0.8,
        disclosed_weight_pct=10,
        data_gaps_json=[],
        artifact_id="native-artifact",
        content_hash="d" * 64,
        source_json={
            **content,
            "report_allowed": True,
            "critical_count": 0,
            "reference_price_evidence": proof,
        },
    )
    report = SimpleNamespace(
        id="report", revision=1, as_of_date=date(2026, 9, 2), data_gaps_json=[]
    )
    result = report_condition_calculation(node, report, StrategyDocumentV2())
    assert result["execution_plan"]["price_basis_verified"] is True, result
    assert result["execution_plan"]["entry_permitted"] is True
    assert result["execution_plan"]["reference_price_evidence"]["price"] == anchor
    assert result["execution_plan"]["native_battle_plan"] == battle_plan
