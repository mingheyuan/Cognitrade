from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from research_system.market_data.commodity_pit import (
    commodity_empty_query_errors,
    commodity_fact_date_errors,
)
from research_system.market_data.resolver import _snapshot_pit_visibility_errors

AS_OF = date(2026, 9, 2)


def _empty_manifest():
    return {
        "pit_mode": "historical_reconstruction",
        "collection_outcome": "verified_empty",
        "provider": "uzi_materials_multi",
        "source_trace": [
            {
                "query_scope": "futures_prices",
                "request_executed": True,
                "bounded_query_completed": True,
                "status": "no_data",
                "in_window_count": 0,
                "query_window": {"start": "2025-03-01", "end": "2026-09-02"},
            }
        ],
    }


@pytest.mark.parametrize(
    "fault",
    [
        "missing_trace",
        "not_queried",
        "failed",
        "bad_window",
        "unknown_count",
        "boolean_count",
        "mapping_missing",
        "one_contract_failed",
    ],
)
def test_legacy_false_empty_query_is_rejected_without_mutation(fault):
    manifest = _empty_manifest()
    query = manifest["source_trace"][0]
    if fault == "missing_trace":
        manifest["source_trace"] = []
    elif fault == "not_queried":
        query["request_executed"] = False
    elif fault == "failed":
        query["status"] = "provider_unavailable"
    elif fault == "bad_window":
        query["query_window"]["end"] = "2026-09-03"
    elif fault == "unknown_count":
        query.pop("in_window_count")
    elif fault == "boolean_count":
        query["in_window_count"] = False
    elif fault == "mapping_missing":
        manifest["missing_fields"] = ["industry_material_mapping_missing"]
    else:
        manifest["source_trace"].append({**query, "status": "timeout"})
    original = deepcopy(manifest)
    assert commodity_empty_query_errors(
        manifest, dimension="8_materials", as_of_date=AS_OF
    )
    assert manifest == original


def test_proven_zero_native_not_applicable_and_manual_keep_original_semantics():
    manifest = _empty_manifest()
    assert (
        commodity_empty_query_errors(
            manifest, dimension="8_materials", as_of_date=AS_OF
        )
        == []
    )
    assert (
        commodity_empty_query_errors(
            {"pit_mode": "historical_reconstruction"},
            dimension="9_futures",
            as_of_date=AS_OF,
        )
        == []
    )
    manifest.update(provider="manual", source_trace=[])
    assert (
        commodity_empty_query_errors(
            manifest, dimension="8_materials", as_of_date=AS_OF
        )
        == []
    )
    manifest["provider"] = "uzi_materials_multi"
    assert (
        commodity_empty_query_errors(manifest, dimension="16_lhb", as_of_date=AS_OF)
        == []
    )


@pytest.mark.parametrize("dimension", ["8_materials", "9_futures"])
@pytest.mark.parametrize("wrapper", ["raw", "facts", "_uzi_raw_result"])
def test_legacy_future_quote_cannot_hide_behind_safe_envelope(dimension, wrapper):
    facts = {"contracts": [{"latest_record": {"日期": "2026-09-03", "收盘价": 962}}]}
    payload = {wrapper: {"data": facts} if wrapper == "_uzi_raw_result" else facts}
    original = deepcopy(payload)
    snapshot = SimpleNamespace(
        payload_json=payload,
        dimension=dimension,
        as_of_date=AS_OF,
        exchange_timezone="Asia/Shanghai",
        query_manifest_json={"pit_safe": True, "pit_mode": "historical_reconstruction"},
    )
    repository = Mock()
    errors = _snapshot_pit_visibility_errors(
        repository, snapshot, datetime(2026, 9, 2, 15, 59, tzinfo=timezone.utc)
    )
    assert errors and "2026-09-03" in errors[0]
    repository.list_snapshot_evidence.assert_not_called()
    assert payload == original


@pytest.mark.parametrize(
    "facts",
    [
        {"prices": [{"date": "20260903", "close": 2}]},
        {"contracts": [{"latest_record_date": "2026/09/03"}]},
        {
            "materials_detail": [
                {"price_history_dates_12m": ["2026-09-01", "2026-09-03"]}
            ]
        },
        {"exposure": {"detail": [{"latest_record": {"trade_date": "2026-09-03"}}]}},
    ],
)
def test_all_stored_price_fact_shapes_are_checked(facts):
    assert commodity_fact_date_errors(facts, dimension="8_materials", as_of_date=AS_OF)


def test_safe_prices_and_future_plans_are_not_confused():
    payload = {
        "contracts": [
            {"latest_record": {"日期": "2026-09-02"}, "expiry_date": "2026-12-31"}
        ],
        "price_history_dates_60d": ["2026-09-01", "2026-09-02"],
        "future_events": [
            {"date": "2026-09-30", "title": "previously announced event"}
        ],
        "source_refs": [{"observed_at": "2026-09-07"}],
    }
    assert (
        commodity_fact_date_errors(payload, dimension="9_futures", as_of_date=AS_OF)
        == []
    )
    assert (
        commodity_fact_date_errors(
            {"prices": [{"date": "2026-09-03"}]},
            dimension="15_events",
            as_of_date=AS_OF,
        )
        == []
    )


def test_frozen_reader_rejects_future_quote_without_modifying_manifest():
    from research_system.market_data.bundles import bundle_manifest_hash
    from research_system.market_data.report_inputs import (
        ReportInputError,
        slots_for_manifest,
        validate_bundle_only_input,
    )
    from tests.test_market_data_report_inputs import _manifest

    manifest = _manifest("688041.SH")
    item = manifest["items"][0]
    bundle = item["bundle"]
    row = item["snapshots"][0]
    row.update(
        dimension_id="9_futures",
        snapshot_type="futures",
        facts={
            "contracts": [{"latest_record": {"日期": "2026-09-03", "收盘价": 962}}],
        },
    )
    bundle["items"][0].update(dimension_id="9_futures", snapshot_type="futures")
    bundle["manifest_hash"] = bundle_manifest_hash(
        symbol=bundle["symbol"],
        market=bundle["market"],
        as_of_date=date.fromisoformat(bundle["as_of_date"]),
        cutoff_at=datetime.fromisoformat(bundle["cutoff_at"]),
        contract_version=bundle["contract_version"],
        items=[
            {
                "dimension": r["dimension_id"],
                "ordinal": r["ordinal"],
                "snapshot_id": r["snapshot_id"],
                "content_hash": r["content_hash"],
                "status": r["availability_status"],
                "evidence_grade": r["evidence_grade"],
            }
            for r in bundle["items"]
        ],
    )
    original = deepcopy(manifest)
    slots = slots_for_manifest(manifest)
    with pytest.raises(ReportInputError) as error:
        validate_bundle_only_input(
            snapshots=slots["snapshots"],
            manifest=slots["report_data_manifest"],
            bundle=slots["market_data_bundle"],
        )
    assert error.value.code == "bundle_snapshot_future_price_fact"
    assert manifest == original


def test_restored_legacy_workflow_cannot_skip_future_quote_guard():
    from domain_packs.finance_ashare.subagents.runtime import (
        execute_uzi_native_full_analysis,
    )
    from research_system.market_data.report_inputs import ReportInputError
    from tests.test_market_data_report_inputs import _ArtifactBackedCollectContext

    manifest = {
        "as_of_date": "2026-09-02",
        "items": [
            {
                "snapshots": [
                    {
                        "dimension_id": "9_futures",
                        "facts": {
                            "contracts": [{"latest_record": {"日期": "2026-09-03"}}]
                        },
                    }
                ]
            }
        ],
    }
    context = _ArtifactBackedCollectContext({"report_data_manifest": manifest})
    context.llm_provider = Mock(side_effect=AssertionError("model must not run"))
    with pytest.raises(ReportInputError) as error:
        execute_uzi_native_full_analysis(context)
    assert error.value.code == "bundle_snapshot_future_price_fact"
    context.llm_provider.assert_not_called()


def test_lightweight_reference_validation_checks_available_commodity_payload():
    from sqlalchemy import update
    from research_system.market_data.models import MarketDataSnapshot
    from research_system.market_data.reference_validation import (
        validate_bundle_reference,
    )
    from research_system.market_data.repository import MarketDataRepository
    from research_system.market_data.report_inputs import ReportInputError
    from research_system.strategies.database import StrategyDatabase
    from tests.market_data.test_pit_core import seed_strict_bundle

    database = StrategyDatabase("sqlite:///:memory:")
    database.create_schema()
    try:
        seed_strict_bundle(database, "688041")
        with database.session() as session:
            repository = MarketDataRepository(session)
            from research_system.market_data.dimension_contracts import (
                DEFAULT_DIMENSION_REGISTRY,
            )

            bundle = repository.latest_bundle(
                symbol="688041.SH",
                market="CN_EQUITY",
                as_of_date=date(2026, 8, 20),
                contract_version=DEFAULT_DIMENSION_REGISTRY.contract_version,
            )
            item = next(
                row
                for row in repository.list_bundle_items(bundle.id)
                if row.dimension == "9_futures"
            )
            # Simulate an already frozen legacy row with false envelope proof.
            session.execute(
                update(MarketDataSnapshot)
                .where(MarketDataSnapshot.id == item.snapshot_id)
                .values(
                    payload_json={
                        "raw": {
                            "contracts": [{"latest_record": {"日期": "2026-09-03"}}]
                        },
                    }
                )
            )
            with pytest.raises(ReportInputError, match="PIT"):
                validate_bundle_reference(repository, bundle)
    finally:
        database.engine.dispose()
