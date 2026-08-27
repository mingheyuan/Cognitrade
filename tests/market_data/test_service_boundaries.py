from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace

from sqlalchemy import func, select

from research_system.market_data.models import (
    MarketDataBundle,
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataConflict,
    MarketDataSnapshot,
)
from research_system.market_data.instruments import SecurityIdentityResolver
from research_system.market_data.service import (
    MarketDataCenterService,
    MarketDataServiceError,
)
from research_system.runtime.platform.settings import Settings
from research_system.strategies.database import StrategyDatabase

REPO_ROOT = Path(__file__).resolve().parents[2]


class _CollectionSpy:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.list_calls: list[dict] = []
        self.closed = False

    def create_job(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        return {
            "id": f"job-{len(self.create_calls)}",
            "status": "queued",
            "symbols": list(kwargs["symbols"]),
            "dimensions": kwargs["dimensions"],
        }

    def list_jobs(self, **kwargs):
        self.list_calls.append(dict(kwargs))
        return {"items": [], "total_count": 0, "limit": 50, "offset": 0}

    def get_job(self, job_id, **kwargs):
        return {"id": job_id, "status": "queued", **kwargs}

    def retry_failed(self, job_id, **kwargs):
        return {"id": job_id, "status": "queued", **kwargs}

    def cancel(self, job_id, **kwargs):
        return {"id": job_id, "status": "cancelled", **kwargs}

    def close(self) -> None:
        self.closed = True


class _FundCollectionSpy:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.closed = False

    def create_job(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        return {
            "id": f"fund-job-{len(self.create_calls)}",
            "job_id": f"fund-job-{len(self.create_calls)}",
            "job_type": "fund_holding_expansion",
            "status": "queued",
            "asset_family": "fund",
            "top_n": 10,
            "expanded_holdings": [],
            "child_collection_job_ids": [],
        }

    def get_job(self, job_id, **kwargs):
        return {
            "id": job_id,
            "job_type": "fund_holding_expansion",
            "status": "queued",
            "asset_family": "fund",
            "top_n": 10,
            "expanded_holdings": [],
            "child_collection_job_ids": [],
            **kwargs,
        }

    def retry_failed(self, job_id, **kwargs):
        return self.get_job(job_id, **kwargs)

    def cancel(self, job_id, **kwargs):
        return {**self.get_job(job_id, **kwargs), "status": "cancelled"}

    def resume_pending(self, **kwargs):
        return []

    def close(self) -> None:
        self.closed = True


class _ProviderCatalogSpy:
    def __init__(self) -> None:
        self.list_calls = 0

    def list(self):
        self.list_calls += 1
        return [
            {
                "provider_id": "fixture-provider",
                "display_name": "Fixture Provider",
                "dependency_installed": True,
                "credential_configured": True,
                "policy_enabled": True,
                "configured": True,
                "domains": ["market"],
            }
        ]

    def test_configuration(self, provider_id: str):
        return {
            "provider_id": provider_id,
            "status": "configuration_ready",
            "ok": True,
            "network_test_performed": False,
            "dependency_installed": True,
            "credential_configured": True,
            "policy_enabled": True,
            "configured": True,
        }


class _CollisionFundMaster:
    def resolve(self, query, *, supplied_codes, allow_network=True):
        del query
        code = supplied_codes[0]
        names = {
            "000001": "华夏成长混合",
            "110011": "易方达优质精选混合",
        }
        matches = (
            [{"fund_code": code, "fund_name": names[code], "fund_type": "混合型"}]
            if allow_network and code in names
            else []
        )
        return SimpleNamespace(
            fund_codes=[code] if matches else [],
            matches=matches,
        )


class MarketDataServiceBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings.from_env(REPO_ROOT).model_copy(
            update={
                "workspace_root": root,
                "runs_root": root / "runs",
                "memory_root": root / "memory",
                "market_data_auto_collection_enabled": False,
            }
        )
        self.database = StrategyDatabase("sqlite://")
        self.database.create_schema()
        self.collection = _CollectionSpy()
        self.fund_collection = _FundCollectionSpy()
        self.providers = _ProviderCatalogSpy()
        self.service = MarketDataCenterService(
            self.database,
            self.settings,
            collection=self.collection,
            fund_collection=self.fund_collection,
            providers=self.providers,
        )
        self.as_of_date = datetime.now(timezone.utc).date() - timedelta(days=2)
        self.published_at = datetime.combine(
            self.as_of_date, time(hour=2), tzinfo=timezone.utc
        )
        self.cutoff_at = datetime.combine(
            self.as_of_date, time(hour=15, minute=59), tzinfo=timezone.utc
        )

    def tearDown(self) -> None:
        self.service.close()
        self.database.close()
        self.temp_dir.cleanup()

    def _manual_request(
        self,
        *,
        dimension: str = "0_basic",
        status: str = "available",
        payload: dict | None = None,
        query_manifest: dict | None = None,
        evidence_published_at: datetime | None = None,
    ) -> dict:
        default_payload = {
            "symbol": "600519.SH",
            "market": "CN_EQUITY",
            "industry": "白酒",
            "currency": "CNY",
            "price": 1500.0,
            "market_cap": 1_000_000.0,
            "shares_outstanding": 1000.0,
            "observed_at": self.published_at.isoformat(),
        }
        return {
            "symbol": "600519.SH",
            "market": "cn_equity",
            "dimension_id": dimension,
            "as_of_date": self.as_of_date,
            "cutoff_at": self.cutoff_at,
            "effective_at": self.published_at,
            "published_at": self.published_at,
            "status": status,
            "payload": default_payload if payload is None else payload,
            "field_sources": {"$": "manual immutable proof"},
            "query_manifest": query_manifest or {},
            "reason": "补录截止日前已经公开且可复核的不可变材料",
            "evidence": [
                {
                    "provider": "manual-fixture",
                    "source_url": "https://example.test/immutable-proof",
                    # The facade must cap manual evidence at B even if a caller
                    # bypasses the HTTP Literal and asks for A.
                    "grade": "A",
                    "immutable_proof": True,
                    "is_manual": False,
                    "published_at": evidence_published_at or self.published_at,
                    "observed_at": datetime.now(timezone.utc),
                }
            ],
        }

    def _seed_invalidated_bundle(self) -> str:
        with self.database.session() as session:
            row = MarketDataBundle(
                symbol="600519.SH",
                market="CN_EQUITY",
                exchange_timezone="Asia/Shanghai",
                as_of_date=self.as_of_date,
                cutoff_at=self.cutoff_at,
                contract_version=self.service.registry.contract_version,
                status="invalidated",
                revision=1,
                expected_dimension_count=22,
                ready_dimension_count=0,
                available_count=0,
                verified_empty_count=0,
                coverage_json={},
                manifest_hash="a" * 64,
                published_by="fixture",
                invalidated_at=datetime.now(timezone.utc),
                invalidation_reason="fixture only",
            )
            session.add(row)
            session.flush()
            return row.id

    def test_all_read_facades_are_database_only(self) -> None:
        snapshot = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=self._manual_request(),
        )
        bundle_id = self._seed_invalidated_bundle()
        self.assertEqual(self.collection.create_calls, [])

        contract = self.service.get_contract()
        coverage = self.service.get_coverage(
            symbol="600519.SH",
            market="cn_equity",
            as_of_date=self.as_of_date,
            cutoff_at=self.cutoff_at,
        )
        snapshots = self.service.list_snapshots(symbol="600519.SH")
        detail = self.service.get_snapshot(snapshot["snapshot_id"])
        bundles = self.service.list_bundles(symbol="600519.SH")
        bundle = self.service.get_bundle(bundle_id)
        providers = self.service.list_providers()

        self.assertEqual(contract["expected_dimension_count"], 22)
        self.assertEqual(coverage["counts"]["available"], 1)
        self.assertEqual(snapshots["total_count"], 1)
        self.assertEqual(detail["evidence_grade"], "B")
        self.assertTrue(detail["evidence"][0]["is_manual"])
        self.assertEqual(bundles["total_count"], 1)
        self.assertEqual(bundle["status"], "invalidated")
        self.assertEqual(providers["items"][0]["provider_id"], "fixture-provider")
        self.assertEqual(self.collection.create_calls, [])

    def test_collection_job_list_is_scoped_and_returns_compact_diagnostics(self) -> None:
        now = datetime.now(timezone.utc)
        holdings = [
            {
                "rank": index + 1,
                "symbol": f"60000{index}.SH",
                "name": f"持仓 {index + 1}",
                "missing_dimensions": ["12_capital_flow"] if index == 0 else [],
            }
            for index in range(10)
        ]
        with self.database.session() as session:
            parent = MarketDataCollectionJob(
                tenant_id="tenant",
                user_id="user",
                idempotency_key="fund-parent",
                job_type="fund_holding_expansion",
                mode="missing_or_stale",
                status="partial",
                symbols_json=["020464"],
                dimensions_json=["fund_top_holdings"],
                start_date=self.as_of_date,
                end_date=self.as_of_date,
                max_concurrency=5,
                publish_when_complete=True,
                requested_by="actor",
                progress_json={"completed": 1, "total": 1, "child_complete": 9, "child_total": 10},
                checkpoint_json={},
                created_at=now,
            )
            session.add(parent)
            session.flush()
            child = MarketDataCollectionJob(
                tenant_id="tenant",
                user_id="user",
                idempotency_key="fund-child",
                parent_job_id=parent.id,
                job_type="historical_backfill",
                mode="missing_or_stale",
                status="failed",
                symbols_json=["600000.SH"],
                dimensions_json=["12_capital_flow"],
                start_date=self.as_of_date,
                end_date=self.as_of_date,
                max_concurrency=5,
                publish_when_complete=True,
                requested_by="actor",
                progress_json={"completed": 0, "failed": 1, "total": 1},
                checkpoint_json={},
                created_at=now,
            )
            session.add(child)
            session.flush()
            parent.checkpoint_json = {
                "holdings": holdings,
                "child_total_count": 10,
                "child_complete_count": 9,
                "child_missing_by_symbol": {"600000.SH": ["12_capital_flow"]},
                "child_job_ids": [child.id],
                "last_child_runs": [{"job_id": child.id, "attempt": 1}],
            }
            session.add_all(
                [
                    MarketDataCollectionTask(
                        job_id=parent.id,
                        symbol="020464",
                        market="CN_FUND",
                        exchange_timezone="Asia/Shanghai",
                        dimension="fund_top_holdings",
                        start_date=self.as_of_date,
                        end_date=self.as_of_date,
                        status="partial",
                        retryable=True,
                        attempt_count=1,
                    ),
                    MarketDataCollectionTask(
                        job_id=child.id,
                        symbol="600000.SH",
                        market="CN_EQUITY",
                        exchange_timezone="Asia/Shanghai",
                        dimension="12_capital_flow",
                        start_date=self.as_of_date,
                        end_date=self.as_of_date,
                        status="failed",
                        retryable=True,
                        attempt_count=1,
                        last_error_code="provider_degraded",
                    ),
                ]
            )

        page = self.service.list_collection_jobs(
            tenant_id="tenant",
            user_id="user",
            symbol="020464.OF",
            asset_family="fund",
            as_of_date=self.as_of_date,
            limit=20,
            offset=0,
        )

        self.assertEqual(page["total_count"], 1)
        self.assertEqual(len(page["items"]), 2)
        parent_payload = page["items"][0]
        self.assertEqual(parent_payload["tasks"], [])
        self.assertEqual(parent_payload["task_summary"]["retryable_failed"], 1)
        self.assertEqual(parent_payload["portfolio_summary"]["ready_slots"], 219)
        self.assertEqual(parent_payload["portfolio_summary"]["retryable_slots"], 1)

    def test_fund_is_one_holding_slot_then_top_ten_equity_workflow(self) -> None:
        contract = self.service.get_contract(market="CN_FUND")
        self.assertEqual(contract["asset_family"], "fund")
        self.assertEqual(contract["expected_dimension_count"], 1)
        self.assertEqual(contract["dimensions"][0]["dimension"], "fund_top_holdings")

        resolved = self.service.resolve_instrument(
            query="020464",
            asset_family="auto",
            allow_network=False,
        )
        self.assertEqual(resolved["resolved"]["asset_type"], "otc_fund")
        self.assertEqual(resolved["resolved"]["market"], "CN_FUND")

        created = self.service.create_collection_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request={
                "symbols": ["020464"],
                "start_date": self.as_of_date,
                "end_date": self.as_of_date,
                "dimensions": ["fund_top_holdings"],
                "mode": "missing_or_stale",
                "max_concurrency": 5,
                "publish_when_complete": True,
                "asset_family": "auto",
                "top_n": 10,
            },
        )
        self.assertEqual(created["asset_family"], "fund")
        self.assertEqual(len(self.fund_collection.create_calls), 1)
        self.assertEqual(self.fund_collection.create_calls[0]["fund_code"], "020464")
        self.assertEqual(self.fund_collection.create_calls[0]["equity_dimensions"], "all")
        self.assertEqual(self.collection.create_calls, [])

        with self.assertRaises(MarketDataServiceError) as captured:
            self.service.create_collection_job(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                request={
                    "symbols": ["020464"],
                    "start_date": self.as_of_date,
                    "end_date": self.as_of_date,
                    "dimensions": "all",
                    "asset_family": "equity",
                },
            )
        self.assertEqual(captured.exception.code, "invalid_stock_symbol")
        self.assertEqual(len(self.fund_collection.create_calls), 1)

    def test_auto_collection_rejects_bare_stock_fund_code_collision(self) -> None:
        self.service.identity_resolver = SecurityIdentityResolver(_CollisionFundMaster())

        resolved = self.service.resolve_instrument(
            query="000001",
            asset_family="auto",
            allow_network=True,
        )
        self.assertTrue(resolved["ambiguous"])
        self.assertIsNone(resolved["resolved"])
        self.assertEqual(
            {item["asset_type"] for item in resolved["candidates"]},
            {"stock", "otc_fund"},
        )
        with self.assertRaises(MarketDataServiceError) as captured:
            self.service.create_collection_job(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                request={
                    "symbols": ["000001"],
                    "start_date": self.as_of_date,
                    "end_date": self.as_of_date,
                    "dimensions": "all",
                    "asset_family": "auto",
                },
            )
        self.assertEqual(captured.exception.code, "security_identity_ambiguous")
        self.assertEqual(self.collection.create_calls, [])

        explicit = self.service.create_collection_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request={
                "symbols": ["000001.SZ"],
                "start_date": self.as_of_date,
                "end_date": self.as_of_date,
                "dimensions": "all",
                "asset_family": "equity",
            },
        )
        self.assertEqual(explicit["symbols"], ["000001.SZ"])

        resolved_fund = self.service.create_collection_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request={
                "symbols": ["110011"],
                "start_date": self.as_of_date,
                "end_date": self.as_of_date,
                "dimensions": "all",
                "asset_family": "auto",
            },
        )
        self.assertEqual(resolved_fund["asset_family"], "fund")
        self.assertEqual(self.fund_collection.create_calls[-1]["fund_code"], "110011")

    def test_fund_report_kind_cannot_bypass_parent_dependency(self) -> None:
        common = {
            "symbols": ["600519.SH"],
            "market_by_symbol": {"600519.SH": "CN_EQUITY"},
            "as_of_date": self.as_of_date,
            "workflow_pins": {"research_profile": "lite"},
        }
        with self.assertRaises(MarketDataServiceError) as captured:
            self.service.create_report_manifest(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                request={
                    **common,
                    "asset_family": "equity",
                    "report_kind": "fund_top_holdings",
                },
            )
        self.assertEqual(captured.exception.code, "fund_report_dependency_required")

        with self.assertRaises(MarketDataServiceError) as captured:
            self.service.create_report_manifest(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                request={
                    **common,
                    "asset_family": "fund",
                    "report_kind": "single_stock",
                    "fund_bundle_id": "missing",
                },
            )
        self.assertEqual(captured.exception.code, "fund_report_kind_mismatch")

    def test_only_explicit_collection_job_creation_reaches_collection_coordinator(self) -> None:
        self.service.get_contract()
        self.service.get_coverage(
            symbol="600519.SH",
            market="CN_EQUITY",
            as_of_date=self.as_of_date,
        )
        self.service.list_snapshots()
        self.service.list_bundles()
        self.service.list_providers()
        self.assertEqual(self.collection.create_calls, [])

        job = self.service.create_collection_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request={
                "symbols": ["600519.SH"],
                "start_date": self.as_of_date,
                "end_date": self.as_of_date,
                "dimensions": ["0_basic"],
                "mode": "missing_or_stale",
                "max_concurrency": 5,
                "publish_when_complete": True,
            },
        )

        self.assertEqual(job["status"], "queued")
        self.assertEqual(len(self.collection.create_calls), 1)
        self.assertEqual(self.collection.create_calls[0]["max_concurrency"], 5)

    def test_enabled_subscription_still_does_nothing_while_global_switch_is_off(self) -> None:
        subscription = self.service.upsert_subscription(
            symbol="600519.SH",
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request={
                "market": "cn_equity",
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "local_time": "00:00",
                "dimensions": ["0_basic"],
                "max_concurrency": 5,
            },
        )
        result = self.service.subscriptions.tick(
            now=datetime.now(timezone.utc) + timedelta(days=2)
        )

        self.assertTrue(subscription["enabled"])
        self.assertFalse(subscription["scheduler_enabled"])
        self.assertFalse(result["scheduler_enabled"])
        self.assertEqual(result["created_jobs"], [])
        self.assertEqual(self.collection.create_calls, [])

    def test_subscription_timezone_is_owned_by_the_market_contract(self) -> None:
        inferred = self.service.upsert_subscription(
            symbol="00700.HK",
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request={"market": "hk_equity", "enabled": False},
        )
        self.assertEqual(inferred["exchange_timezone"], "Asia/Hong_Kong")

        with self.assertRaises(MarketDataServiceError) as mismatch:
            self.service.upsert_subscription(
                symbol="AAPL.US",
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                request={
                    "market": "us_equity",
                    "enabled": True,
                    "timezone": "Asia/Shanghai",
                },
            )
        self.assertEqual(
            mismatch.exception.code,
            "subscription_timezone_market_mismatch",
        )

    def test_manual_b_evidence_enforces_cutoff_and_strict_verified_empty(self) -> None:
        available = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=self._manual_request(),
        )
        self.assertEqual(available["status"], "available")
        self.assertEqual(available["evidence_grade"], "B")

        with self.assertRaises(MarketDataServiceError) as future_context:
            self.service.create_manual_snapshot(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                request=self._manual_request(
                    evidence_published_at=self.cutoff_at + timedelta(seconds=1)
                ),
            )
        self.assertEqual(future_context.exception.code, "manual_snapshot_future_leak")
        self.assertEqual(future_context.exception.status_code, 409)

        valid_manifest = {
            "query_window": {
                "start": (self.as_of_date - timedelta(days=30)).isoformat(),
                "end": self.as_of_date.isoformat(),
            },
            "required_sources": ["official-lhb"],
            "sources": [
                {"source": "official-lhb", "status": "success", "result_count": 0}
            ],
            "result_count": 0,
        }
        empty = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=self._manual_request(
                dimension="16_lhb",
                status="verified_empty",
                payload={},
                query_manifest=valid_manifest,
            ),
        )
        self.assertEqual(empty["status"], "verified_empty")

        invalid_manifest = {
            **valid_manifest,
            "sources": [
                {"source": "official-lhb", "status": "timeout", "result_count": 0}
            ],
        }
        with self.assertRaises(MarketDataServiceError) as invalid_context:
            self.service.create_manual_snapshot(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                request=self._manual_request(
                    dimension="16_lhb",
                    status="verified_empty",
                    payload={},
                    query_manifest=invalid_manifest,
                ),
            )
        self.assertEqual(invalid_context.exception.code, "manual_snapshot_validation_failed")
        self.assertEqual(invalid_context.exception.status_code, 422)
        with self.database.session() as session:
            snapshot_count = int(
                session.scalar(select(func.count()).select_from(MarketDataSnapshot)) or 0
            )
        self.assertEqual(snapshot_count, 2)

    def test_detected_conflict_requires_valid_whole_snapshot_decision(self) -> None:
        first = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=self._manual_request(),
        )
        changed = self._manual_request()
        changed["payload"] = {**changed["payload"], "price": 1499.0}
        second = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=changed,
        )
        with self.database.session() as session:
            conflict = session.scalar(select(MarketDataConflict))
            self.assertIsNotNone(conflict)
            conflict_id = conflict.id
            self.assertEqual(conflict.field_path, "price")
            self.assertEqual(
                {item["candidate_id"] for item in conflict.candidate_values_json},
                {first["snapshot_id"], second["snapshot_id"]},
            )

        blocked = self.service.get_coverage(
            symbol="600519.SH",
            market="CN_EQUITY",
            as_of_date=self.as_of_date,
            cutoff_at=self.cutoff_at,
        )
        basic = next(
            row for row in blocked["dimensions"] if row["dimension_id"] == "0_basic"
        )
        self.assertTrue(basic["conflict"])
        self.assertTrue(basic["eligible"])
        self.assertFalse(basic["audit_eligible"])

        with self.assertRaises(MarketDataServiceError) as invalid_candidate:
            self.service.resolve_conflict(
                conflict_id,
                actor_id="actor",
                request={
                    "resolution": "select_candidate",
                    "selected_snapshot_id": "not-a-candidate",
                    "reason": "无效候选不应改变任何快照",
                },
            )
        self.assertEqual(invalid_candidate.exception.code, "conflict_candidate_invalid")
        with self.database.session() as session:
            self.assertIsNone(session.get(MarketDataSnapshot, first["snapshot_id"]).revoked_at)
            self.assertIsNone(session.get(MarketDataSnapshot, second["snapshot_id"]).revoked_at)
            self.assertEqual(session.get(MarketDataConflict, conflict_id).status, "open")

        with self.assertRaises(MarketDataServiceError) as manual_override:
            self.service.resolve_conflict(
                conflict_id,
                actor_id="actor",
                request={
                    "resolution": "manual_override",
                    "manual_value": 1501.0,
                    "reason": "禁止无证据的裸值覆盖",
                },
            )
        self.assertEqual(
            manual_override.exception.code, "conflict_manual_snapshot_required"
        )

        resolved = self.service.resolve_conflict(
            conflict_id,
            actor_id="actor",
            request={
                "resolution": "select_candidate",
                "selected_snapshot_id": first["snapshot_id"],
                "reason": "第一份材料是最终可复核版本",
            },
        )
        self.assertEqual(resolved["status"], "manually_resolved")
        with self.database.session() as session:
            winner = session.get(MarketDataSnapshot, first["snapshot_id"])
            loser = session.get(MarketDataSnapshot, second["snapshot_id"])
            self.assertIsNone(winner.revoked_at)
            self.assertIsNotNone(loser.revoked_at)
            self.assertEqual(loser.superseded_by_id, winner.id)

        unblocked = self.service.get_coverage(
            symbol="600519.SH",
            market="CN_EQUITY",
            as_of_date=self.as_of_date,
            cutoff_at=self.cutoff_at,
        )
        basic = next(
            row for row in unblocked["dimensions"] if row["dimension_id"] == "0_basic"
        )
        self.assertFalse(basic["conflict"])
        self.assertTrue(basic["eligible"])
        self.assertEqual(basic["snapshot_id"], first["snapshot_id"])

    def test_conflict_repository_value_error_is_mapped_and_rolled_back(self) -> None:
        first = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=self._manual_request(),
        )
        unrelated_request = self._manual_request()
        unrelated_request["symbol"] = "000001.SZ"
        unrelated_request["payload"] = {
            **unrelated_request["payload"],
            "symbol": "000001.SZ",
        }
        unrelated = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=unrelated_request,
        )
        with self.database.session() as session:
            conflict = MarketDataConflict(
                symbol="600519.SH",
                market="CN_EQUITY",
                dimension="0_basic",
                as_of_date=self.as_of_date,
                cutoff_at=self.cutoff_at,
                snapshot_id=unrelated["snapshot_id"],
                field_path="price",
                candidate_values_json=[
                    {"candidate_id": first["snapshot_id"], "value": 1500.0},
                    {"candidate_id": unrelated["snapshot_id"], "value": 1500.0},
                ],
                candidate_evidence_json=[],
            )
            session.add(conflict)
            session.flush()
            conflict_id = conflict.id

        with self.assertRaises(MarketDataServiceError) as context:
            self.service.resolve_conflict(
                conflict_id,
                actor_id="actor",
                request={
                    "resolution": "select_candidate",
                    "selected_snapshot_id": first["snapshot_id"],
                    "reason": "畸形跨标的候选必须被拒绝",
                },
            )
        self.assertEqual(context.exception.code, "conflict_resolution_invalid")
        self.assertEqual(context.exception.status_code, 409)
        with self.database.session() as session:
            self.assertIsNone(session.get(MarketDataSnapshot, first["snapshot_id"]).revoked_at)
            self.assertIsNone(
                session.get(MarketDataSnapshot, unrelated["snapshot_id"]).revoked_at
            )
            self.assertEqual(session.get(MarketDataConflict, conflict_id).status, "open")

    def test_multi_field_conflicts_choose_one_winner_atomically(self) -> None:
        first = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=self._manual_request(),
        )
        changed = self._manual_request()
        changed["payload"] = {
            **changed["payload"],
            "price": 1499.0,
            "market_cap": 999_000.0,
        }
        second = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=changed,
        )
        with self.database.session() as session:
            conflicts = list(
                session.scalars(select(MarketDataConflict).order_by(MarketDataConflict.field_path))
            )
            self.assertEqual(
                [item.field_path for item in conflicts], ["market_cap", "price"]
            )
            conflict_ids = [item.id for item in conflicts]

        resolved = self.service.resolve_conflict(
            conflict_ids[0],
            actor_id="actor",
            request={
                "resolution": "select_candidate",
                "selected_snapshot_id": first["snapshot_id"],
                "reason": "同一事实的字段必须共用一个整快照赢家",
            },
        )
        self.assertEqual(
            set(resolved["resolution"]["resolved_conflict_ids"]), set(conflict_ids)
        )
        with self.database.session() as session:
            rows = [session.get(MarketDataConflict, item) for item in conflict_ids]
            self.assertTrue(all(row.status == "manually_resolved" for row in rows))
            self.assertTrue(
                all(
                    row.resolution_json["selected_snapshot_id"] == first["snapshot_id"]
                    for row in rows
                )
            )
            self.assertIsNone(session.get(MarketDataSnapshot, first["snapshot_id"]).revoked_at)
            self.assertIsNotNone(
                session.get(MarketDataSnapshot, second["snapshot_id"]).revoked_at
            )

    def test_mark_invalid_resolves_all_fields_and_preserves_the_opponent(self) -> None:
        first = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=self._manual_request(),
        )
        changed = self._manual_request()
        changed["payload"] = {
            **changed["payload"],
            "price": 1499.0,
            "market_cap": 999_000.0,
        }
        second = self.service.create_manual_snapshot(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            request=changed,
        )
        with self.database.session() as session:
            conflicts = list(session.scalars(select(MarketDataConflict)))
            self.assertEqual(len(conflicts), 2)
            self.assertTrue(
                all(item.snapshot_id == second["snapshot_id"] for item in conflicts)
            )
            conflict_ids = [item.id for item in conflicts]

        resolved = self.service.resolve_conflict(
            conflict_ids[0],
            actor_id="actor",
            request={
                "resolution": "mark_invalid",
                "reason": "新到材料口径错误，整条淘汰并保留原快照",
            },
        )

        self.assertEqual(resolved["resolution"]["action"], "mark_invalid")
        self.assertEqual(
            resolved["resolution"]["selected_snapshot_id"], first["snapshot_id"]
        )
        self.assertEqual(
            set(resolved["resolution"]["resolved_conflict_ids"]), set(conflict_ids)
        )
        with self.database.session() as session:
            rows = [session.get(MarketDataConflict, item) for item in conflict_ids]
            self.assertTrue(all(row.status == "manually_resolved" for row in rows))
            winner = session.get(MarketDataSnapshot, first["snapshot_id"])
            invalid = session.get(MarketDataSnapshot, second["snapshot_id"])
            self.assertIsNone(winner.revoked_at)
            self.assertIsNotNone(invalid.revoked_at)
            self.assertEqual(invalid.superseded_by_id, winner.id)

        coverage = self.service.get_coverage(
            symbol="600519.SH",
            market="CN_EQUITY",
            as_of_date=self.as_of_date,
            cutoff_at=self.cutoff_at,
        )
        basic = next(
            row for row in coverage["dimensions"] if row["dimension_id"] == "0_basic"
        )
        self.assertTrue(basic["eligible"])
        self.assertFalse(basic["conflict"])
        self.assertEqual(basic["snapshot_id"], first["snapshot_id"])

        with self.assertRaises(MarketDataServiceError) as reversed_decision:
            self.service.resolve_conflict(
                conflict_ids[1],
                actor_id="actor",
                request={
                    "resolution": "select_candidate",
                    "selected_snapshot_id": second["snapshot_id"],
                    "reason": "不允许在另一字段反向选择已撤销 loser",
                },
            )
        self.assertEqual(
            reversed_decision.exception.code, "conflict_winner_immutable"
        )
        self.assertEqual(reversed_decision.exception.status_code, 409)
        with self.database.session() as session:
            self.assertIsNone(session.get(MarketDataSnapshot, first["snapshot_id"]).revoked_at)
            self.assertIsNotNone(
                session.get(MarketDataSnapshot, second["snapshot_id"]).revoked_at
            )

    def test_file_sqlite_concurrent_opposite_decisions_leave_one_winner(self) -> None:
        database_path = Path(self.temp_dir.name) / "conflict-concurrency.sqlite3"
        database_url = f"sqlite:///{database_path}"
        first_database = StrategyDatabase(database_url)
        first_database.create_schema()
        first_service = MarketDataCenterService(
            first_database,
            self.settings,
            collection=_CollectionSpy(),
            providers=_ProviderCatalogSpy(),
        )
        second_database = StrategyDatabase(database_url)
        second_service = MarketDataCenterService(
            second_database,
            self.settings,
            collection=_CollectionSpy(),
            providers=_ProviderCatalogSpy(),
        )
        try:
            first = first_service.create_manual_snapshot(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                request=self._manual_request(),
            )
            changed = self._manual_request()
            changed["payload"] = {
                **changed["payload"],
                "price": 1499.0,
                "market_cap": 999_000.0,
            }
            second = first_service.create_manual_snapshot(
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                request=changed,
            )
            with first_database.session() as session:
                conflicts = list(
                    session.scalars(
                        select(MarketDataConflict).order_by(MarketDataConflict.id)
                    )
                )
                self.assertEqual(len(conflicts), 2)
                conflict_ids = [item.id for item in conflicts]

            start = Barrier(3)

            def decide(service, conflict_id: str, selected_snapshot_id: str) -> str:
                start.wait(timeout=5)
                try:
                    service.resolve_conflict(
                        conflict_id,
                        actor_id="concurrent-actor",
                        request={
                            "resolution": "select_candidate",
                            "selected_snapshot_id": selected_snapshot_id,
                            "reason": "并发相反决议只能有一个整快照赢家",
                        },
                    )
                except MarketDataServiceError as exc:
                    return exc.code
                return f"ok:{selected_snapshot_id}"

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        decide,
                        first_service,
                        conflict_ids[0],
                        first["snapshot_id"],
                    ),
                    executor.submit(
                        decide,
                        second_service,
                        conflict_ids[1],
                        second["snapshot_id"],
                    ),
                ]
                start.wait(timeout=5)
                results = [future.result(timeout=10) for future in futures]

            self.assertEqual(sum(item.startswith("ok:") for item in results), 1, results)
            self.assertTrue(
                any(
                    item
                    in {
                        "conflict_resolution_invalid",
                        "conflict_resolution_concurrent",
                        "conflict_winner_immutable",
                    }
                    for item in results
                ),
                results,
            )
            with first_database.session() as session:
                snapshots = [
                    session.get(MarketDataSnapshot, first["snapshot_id"]),
                    session.get(MarketDataSnapshot, second["snapshot_id"]),
                ]
                active = [item for item in snapshots if item.revoked_at is None]
                self.assertEqual(len(active), 1)
                winner_id = active[0].id
                rows = list(session.scalars(select(MarketDataConflict)))
                self.assertTrue(all(row.status == "manually_resolved" for row in rows))
                self.assertTrue(
                    all(
                        row.resolution_json["selected_snapshot_id"] == winner_id
                        for row in rows
                    )
                )
        finally:
            second_service.close()
            second_database.close()
            first_service.close()
            first_database.close()


if __name__ == "__main__":
    unittest.main()
