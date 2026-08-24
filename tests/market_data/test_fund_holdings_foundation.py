from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

from research_system.market_data.contracts import SnapshotStatus
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.fund_holdings_adapter import (
    FundHoldingsAdapterError,
    FundTopHoldingsSnapshotAdapter,
)
from research_system.market_data.fund_holdings_contracts import (
    FUND_HOLDINGS_CONTRACT_VERSION,
    FUND_TOP_HOLDINGS_COUNT,
    FUND_TOP_HOLDINGS_DIMENSION,
    FUND_TOP_HOLDINGS_REGISTRY,
    FundHoldingsSemanticError,
    FundTopHoldingsPayload,
    validate_fund_top_holdings_ingest,
    validate_persisted_fund_top_holdings,
)
from research_system.market_data.ingestion import validate_snapshot_contract_payload
from research_system.market_data.instruments import (
    AssetType,
    SecurityIdentityError,
    SecurityIdentityResolver,
)

AS_OF = date(2026, 8, 20)
OBSERVED = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)
DISCLOSURE = "2026-06-30"
STOCK_CODES = (
    "600519",
    "000001",
    "300750",
    "688012",
    "002594",
    "601318",
    "000333",
    "600036",
    "300059",
    "601166",
)


class FakeFundMaster:
    def resolve(self, query, *, supplied_codes, allow_network=True):
        del query, allow_network
        code = supplied_codes[0]
        return SimpleNamespace(
            fund_codes=[code],
            matches=[
                {
                    "fund_code": code,
                    "fund_name": "易方达优质精选混合",
                    "fund_type": "混合型",
                }
            ],
        )


class CollisionFundMaster:
    def resolve(self, query, *, supplied_codes, allow_network=True):
        del query
        code = supplied_codes[0]
        if not allow_network or code != "000001":
            return SimpleNamespace(fund_codes=[], matches=[])
        return SimpleNamespace(
            fund_codes=[code],
            matches=[
                {
                    "fund_code": code,
                    "fund_name": "华夏成长混合",
                    "fund_type": "混合型",
                }
            ],
        )


class UnavailableFundMaster:
    def resolve(self, query, *, supplied_codes, allow_network=True):
        del query, supplied_codes, allow_network
        return SimpleNamespace(
            fund_codes=[],
            matches=[],
            source="fund_catalog_unavailable",
            provider_error="TimeoutError",
        )


def direct_skill_output(
    *,
    count: int = FUND_TOP_HOLDINGS_COUNT,
    retrieved_at: datetime = OBSERVED,
    immutable_history: bool = False,
) -> dict:
    source_ref = {
        "type": "fund_direct_equity_holdings",
        "provider": "eastmoney",
        "id": f"eastmoney.fund_archives:020464:{DISCLOSURE}",
    }
    if immutable_history:
        source_ref.update(
            {
                "published_at": "2026-07-20T08:00:00Z",
                "immutable_proof": True,
                "content_hash": "a" * 64,
            }
        )
    return {
        "query": "分析基金 020464",
        "analysis_mode": "single_fund",
        "fund_codes": ["020464"],
        "fund_category": "股票型",
        "funds": [
            {
                "fund_code": "020464",
                "fund_name": "测试股票型基金",
                "fund_type": "股票型",
                "top_holdings": [
                    {
                        "rank": index,
                        "code": code,
                        "name": f"股票{index}",
                        "weight_pct": float(11 - index),
                    }
                    for index, code in enumerate(STOCK_CODES[:count], start=1)
                ],
                "holdings_period": f"2026年2季度股票投资明细 截止至：{DISCLOSURE}",
                "holdings_coverage": {"is_fund_of_funds": False},
                "data_gap": [],
            }
        ],
        "quality_status": "available",
        "data_gap": [],
        "source_refs": [source_ref],
        "retrieved_at": retrieved_at.isoformat(),
    }


def feeder_skill_output(*, retrieved_at: datetime = OBSERVED) -> dict:
    parent_weight = 90.0
    lookthrough = []
    for index, code in enumerate(STOCK_CODES, start=1):
        disclosed = float(11 - index)
        lookthrough.append(
            {
                "rank": index,
                "code": code,
                "name": f"股票{index}",
                "weight_pct": disclosed,
                "effective_weight_pct": round(parent_weight * disclosed / 100.0, 6),
            }
        )
    return {
        "query": "分析 ETF 联接基金 020464",
        "analysis_mode": "single_fund",
        "fund_codes": ["020464"],
        "fund_category": "ETF",
        "funds": [
            {
                "fund_code": "020464",
                "fund_name": "测试 ETF 联接",
                "fund_type": "ETF联接",
                "top_holdings": [],
                "holdings_period": None,
                "target_fund_holdings": [
                    {
                        "code": "510300",
                        "name": "目标 ETF",
                        "weight_pct": parent_weight,
                        "report_date": DISCLOSURE,
                    }
                ],
                "target_fund_period": DISCLOSURE,
                "target_fund_published_at": "2026-07-18",
                "lookthrough_holdings": lookthrough,
                "lookthrough_period": f"目标ETF 2026年2季度股票投资明细 截止至：{DISCLOSURE}",
                "holdings_coverage": {"is_fund_of_funds": True},
                "data_gap": [],
            }
        ],
        "quality_status": "available",
        "data_gap": [],
        "source_refs": [
            {
                "type": "fund_target_disclosure",
                "provider": "eastmoney_official_report",
                "id": "https://pdf.example/020464-q2.pdf",
            },
            {
                "type": "fund_target_etf_holdings",
                "provider": "eastmoney",
                "id": f"eastmoney.fund_archives:510300:{DISCLOSURE}",
            },
        ],
        "retrieved_at": retrieved_at.isoformat(),
    }


class SecurityIdentityTests(unittest.TestCase):
    def test_020464_is_otc_fund_not_cn_equity(self) -> None:
        identity = SecurityIdentityResolver().resolve("020464")

        self.assertEqual(identity.asset_type, AssetType.OTC_FUND)
        self.assertEqual(identity.symbol, "020464")
        self.assertEqual(identity.market, "CN_FUND")
        self.assertEqual(identity.exchange, "OTC")

    def test_a_share_symbols_are_canonical_and_exchange_checked(self) -> None:
        resolver = SecurityIdentityResolver()

        self.assertEqual(resolver.resolve("600519").symbol, "600519.SH")
        self.assertEqual(resolver.resolve("SH600519").symbol, "600519.SH")
        self.assertEqual(resolver.resolve("000001.SZ").symbol, "000001.SZ")
        with self.assertRaisesRegex(SecurityIdentityError, "交易所应为 SH"):
            resolver.resolve("600519.SZ")
        with self.assertRaisesRegex(SecurityIdentityError, "不属于受支持的 A 股"):
            resolver.resolve("020464", asset_type="stock")

    def test_exchange_fund_and_etf_feeder_are_distinct(self) -> None:
        resolver = SecurityIdentityResolver()

        exchange = resolver.resolve("510300")
        feeder = resolver.resolve(
            "020464",
            asset_type="otc_fund",
            fund_type="ETF联接",
        )
        self.assertEqual(exchange.asset_type, AssetType.EXCHANGE_FUND)
        self.assertEqual(exchange.symbol, "510300.SH")
        self.assertEqual(feeder.asset_type, AssetType.OTC_FUND)
        self.assertEqual(feeder.security_type, "etf_feeder")

    def test_injected_fund_master_wins_before_bond_prefix_heuristic(self) -> None:
        resolver = SecurityIdentityResolver(fund_master=FakeFundMaster())

        identity = resolver.resolve("110011", allow_network=True)

        self.assertEqual(identity.asset_type, AssetType.OTC_FUND)
        self.assertEqual(identity.name, "易方达优质精选混合")
        self.assertEqual(identity.resolution_source, "fund_security_master")
        with self.assertRaisesRegex(SecurityIdentityError, "可能属于基金或债券"):
            SecurityIdentityResolver().resolve("110011")

        candidates = resolver.resolve_candidates("110011", allow_network=True)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].asset_type, AssetType.OTC_FUND)

    def test_bare_six_digit_stock_fund_collision_requires_explicit_identity(self) -> None:
        resolver = SecurityIdentityResolver(fund_master=CollisionFundMaster())

        candidates = resolver.resolve_candidates("000001", allow_network=True)

        self.assertEqual(
            {identity.asset_type for identity in candidates},
            {AssetType.STOCK, AssetType.OTC_FUND},
        )
        explicit_stock = resolver.resolve_candidates("000001.SZ", allow_network=True)
        self.assertEqual(len(explicit_stock), 1)
        self.assertEqual(explicit_stock[0].asset_type, AssetType.STOCK)

    def test_bare_collision_code_fails_closed_when_fund_catalog_is_unavailable(self) -> None:
        resolver = SecurityIdentityResolver(fund_master=UnavailableFundMaster())

        with self.assertRaises(SecurityIdentityError) as captured:
            resolver.resolve_candidates("000001", allow_network=True)
        self.assertEqual(captured.exception.code, "fund_catalog_unavailable")
        self.assertEqual(
            resolver.resolve_candidates("000001.SZ", allow_network=True)[0].asset_type,
            AssetType.STOCK,
        )

    def test_hk_and_us_stock_symbols_remain_canonical(self) -> None:
        resolver = SecurityIdentityResolver()

        self.assertEqual(resolver.resolve("HK00700").symbol, "00700.HK")
        self.assertEqual(resolver.resolve("AAPL.US").symbol, "AAPL")


class FundTopHoldingsContractTests(unittest.TestCase):
    def test_registry_is_independent_one_slot_contract(self) -> None:
        self.assertEqual(FUND_TOP_HOLDINGS_REGISTRY.expected_dimension_count, 1)
        self.assertEqual(
            FUND_TOP_HOLDINGS_REGISTRY.dimension_ids,
            (FUND_TOP_HOLDINGS_DIMENSION,),
        )
        self.assertEqual(
            FUND_TOP_HOLDINGS_REGISTRY.contract_version,
            FUND_HOLDINGS_CONTRACT_VERSION,
        )
        self.assertNotIn(
            FUND_TOP_HOLDINGS_DIMENSION,
            DEFAULT_DIMENSION_REGISTRY.dimension_ids,
        )
        self.assertFalse(FUND_TOP_HOLDINGS_REGISTRY[0].permits_verified_empty)

    def test_current_direct_disclosure_builds_strict_available_slot(self) -> None:
        snapshot = FundTopHoldingsSnapshotAdapter().adapt(
            direct_skill_output(),
            fund_code="020464",
            as_of_date=AS_OF,
            observed_at=OBSERVED,
        )

        self.assertEqual(snapshot.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(snapshot.symbol, "020464")
        self.assertEqual(snapshot.market, "CN_FUND")
        self.assertEqual(snapshot.contract_version, FUND_HOLDINGS_CONTRACT_VERSION)
        self.assertEqual(len(snapshot.payload["holdings"]), 10)
        self.assertEqual(snapshot.payload["holdings"][0]["symbol"], "600519.SH")
        self.assertEqual(snapshot.payload["holdings"][1]["symbol"], "000001.SZ")
        self.assertEqual(snapshot.payload["cutoff_at"], OBSERVED.isoformat().replace("+00:00", "Z"))
        self.assertEqual(snapshot.evidence[0].grade.value, "A")
        payload = validate_fund_top_holdings_ingest(snapshot)
        self.assertIsInstance(payload, FundTopHoldingsPayload)
        self.assertEqual(
            validate_snapshot_contract_payload(
                status=snapshot.status,
                payload=snapshot.payload,
                query_manifest=snapshot.query_manifest,
                field_sources=snapshot.field_sources,
                contract=FUND_TOP_HOLDINGS_REGISTRY[0],
            ),
            [],
        )

    def test_current_feeder_uses_two_leg_lineage_and_effective_weights(self) -> None:
        snapshot = FundTopHoldingsSnapshotAdapter().adapt(
            feeder_skill_output(),
            fund_code="020464",
            as_of_date=AS_OF,
            observed_at=OBSERVED,
        )

        self.assertEqual(snapshot.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(snapshot.payload["asset_type"], "otc_fund")
        self.assertTrue(snapshot.payload["is_fund_of_funds"])
        self.assertEqual(snapshot.payload["parent_allocation_weight_pct"], 90.0)
        self.assertEqual(
            {row["role"] for row in snapshot.payload["lineage"]},
            {"parent_target_allocation", "target_holdings"},
        )
        first = snapshot.payload["holdings"][0]
        self.assertEqual(first["disclosed_weight_pct"], 10.0)
        self.assertEqual(first["fund_weight_pct"], 9.0)
        self.assertEqual(len(snapshot.evidence), 2)
        validate_fund_top_holdings_ingest(snapshot)

    def test_current_output_with_fewer_than_ten_a_shares_is_data_gap(self) -> None:
        snapshot = FundTopHoldingsSnapshotAdapter().adapt(
            direct_skill_output(count=9),
            fund_code="020464",
            as_of_date=AS_OF,
            observed_at=OBSERVED,
        )

        self.assertEqual(snapshot.status, SnapshotStatus.DATA_GAP)
        self.assertTrue(
            any(
                "supported_a_share_count:9;expected:10" in error
                for error in snapshot.validation["semantic_errors"]
            )
        )

    def test_historical_current_page_never_backfills_the_past(self) -> None:
        snapshot = FundTopHoldingsSnapshotAdapter().adapt(
            direct_skill_output(),
            fund_code="020464",
            as_of_date=date(2026, 8, 1),
            observed_at=OBSERVED,
        )

        self.assertEqual(snapshot.status, SnapshotStatus.DATA_GAP)
        self.assertIn(
            "historical_pit_reconstruction_not_proven",
            snapshot.validation["semantic_errors"],
        )
        self.assertNotEqual(snapshot.payload["disclosure_date"], snapshot.payload["availability_at"][:10])

    def test_historical_immutable_publication_evidence_is_grade_b(self) -> None:
        snapshot = FundTopHoldingsSnapshotAdapter().adapt(
            direct_skill_output(immutable_history=True),
            fund_code="020464",
            as_of_date=date(2026, 8, 1),
            observed_at=OBSERVED,
        )

        self.assertEqual(snapshot.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(snapshot.evidence[0].grade.value, "B")
        self.assertEqual(
            snapshot.payload["availability_at"],
            "2026-07-20T08:00:00Z",
        )
        self.assertEqual(
            snapshot.payload["cutoff_at"],
            "2026-08-01T15:59:59.999999Z",
        )
        validate_fund_top_holdings_ingest(snapshot)

    def test_semantic_validator_rejects_weight_and_parent_identity_tampering(self) -> None:
        snapshot = FundTopHoldingsSnapshotAdapter().adapt(
            feeder_skill_output(),
            fund_code="020464",
            as_of_date=AS_OF,
            observed_at=OBSERVED,
        )
        tampered = snapshot.model_copy(deep=True)
        tampered.payload["holdings"][0]["fund_weight_pct"] = 8.5
        with self.assertRaises(FundHoldingsSemanticError):
            validate_fund_top_holdings_ingest(tampered)

        wrong_parent = snapshot.model_copy(update={"symbol": "020465"})
        with self.assertRaisesRegex(FundHoldingsSemanticError, "fund_code"):
            validate_fund_top_holdings_ingest(wrong_parent)

    def test_persisted_validator_requires_bound_lineage_evidence(self) -> None:
        ingest = FundTopHoldingsSnapshotAdapter().adapt(
            direct_skill_output(),
            fund_code="020464",
            as_of_date=AS_OF,
            observed_at=OBSERVED,
        )
        persisted = SimpleNamespace(
            symbol=ingest.symbol,
            market=ingest.market,
            exchange_timezone=ingest.exchange_timezone,
            dimension=ingest.dimension,
            contract_version=ingest.contract_version,
            as_of_date=ingest.as_of_date,
            status=ingest.status.value,
            published_at=ingest.published_at,
            effective_at=ingest.effective_at,
            payload_json=ingest.payload,
            query_manifest_json=ingest.query_manifest,
        )

        validate_persisted_fund_top_holdings(persisted, evidence=ingest.evidence)
        with self.assertRaisesRegex(FundHoldingsSemanticError, "no bound evidence"):
            validate_persisted_fund_top_holdings(persisted, evidence=[])

    def test_persisted_validator_accepts_sqlite_naive_utc_and_metadata_json(self) -> None:
        ingest = FundTopHoldingsSnapshotAdapter().adapt(
            direct_skill_output(),
            fund_code="020464",
            as_of_date=AS_OF,
            observed_at=OBSERVED,
        )
        persisted = SimpleNamespace(
            symbol=ingest.symbol,
            market=ingest.market,
            exchange_timezone=ingest.exchange_timezone,
            dimension=ingest.dimension,
            contract_version=ingest.contract_version,
            as_of_date=ingest.as_of_date,
            status=ingest.status.value,
            published_at=ingest.published_at.replace(tzinfo=None),
            effective_at=ingest.effective_at.replace(tzinfo=None),
            payload_json=ingest.payload,
            query_manifest_json=ingest.query_manifest,
        )
        persisted_evidence = []
        for proof in ingest.evidence:
            persisted_evidence.append(
                SimpleNamespace(
                    metadata=object(),
                    metadata_json=proof.metadata,
                    grade=proof.grade.value,
                    immutable_proof=proof.immutable_proof,
                    published_at=proof.published_at.replace(tzinfo=None),
                    content_hash=proof.resolved_content_hash(),
                    content_ref=proof.content_ref,
                    inline_content=proof.inline_content,
                )
            )

        validated = validate_persisted_fund_top_holdings(
            persisted,
            evidence=persisted_evidence,
        )

        self.assertEqual(validated.fund_code, "020464")

    def test_adapter_rejects_non_contract_top_n(self) -> None:
        with self.assertRaisesRegex(FundHoldingsAdapterError, "固定要求前 10 只"):
            FundTopHoldingsSnapshotAdapter().adapt(
                direct_skill_output(),
                fund_code="020464",
                as_of_date=AS_OF,
                top_n=9,
                observed_at=OBSERVED,
            )


if __name__ == "__main__":
    unittest.main()
