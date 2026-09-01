from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.market_data.service import MarketDataServiceError
from research_system.runtime.platform.settings import Settings
from tests.api_test_support import API_TOKEN, auth_headers

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Memory:
    @staticmethod
    def expire_records():
        return []


class _AgentOS:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.memory = _Memory()


class _RunService:
    def __init__(self) -> None:
        self.report_inputs = None
        self.closed = False

    def bind_report_inputs(self, service) -> None:
        self.report_inputs = service

    def close(self) -> None:
        self.closed = True


class _Lifecycle:
    def __init__(self) -> None:
        self.resume_calls = 0

    def resume_pending(self) -> None:
        self.resume_calls += 1


class _MarketDataCenter:
    def __init__(self) -> None:
        self.collection = _Lifecycle()
        self.read_calls: list[str] = []
        self.explicit_collection_calls: list[dict] = []
        self.manual_snapshot_calls: list[dict] = []
        self.agent_recovery_calls: list[dict] = []
        self.confirm_freeze_calls: list[dict] = []
        self.start_calls = 0
        self.closed = False

    def start(self) -> None:
        self.start_calls += 1
        self.collection.resume_pending()

    def get_contract(self, **kwargs):
        self.read_calls.append("contract")
        return {
            "route_owner": "market_data_center",
            "expected_dimension_count": 22,
            "query": kwargs,
        }

    def get_coverage(self, **kwargs):
        self.read_calls.append("coverage")
        return {
            "route_owner": "market_data_center",
            "expected_dimension_count": 22,
            "counts": {
                "available": 0,
                "verified_empty": 0,
                "data_gap": 22,
                "conflict": 0,
                "stale": 0,
            },
            "dimensions": [],
            "query": kwargs,
        }

    def resolve_instrument(self, **kwargs):
        self.read_calls.append("instrument_resolve")
        return {
            "query": kwargs["query"],
            "resolved": {
                "symbol": "020464",
                "code": "020464",
                "asset_type": "otc_fund",
                "market": "CN_FUND",
            },
            "candidates": [],
            "ambiguous": False,
        }

    def list_snapshots(self, **kwargs):
        self.read_calls.append("snapshots")
        return {"items": [], "total_count": 0, **kwargs}

    def list_collection_jobs(self, **kwargs):
        self.read_calls.append("collection_jobs")
        return {"items": [], "total_count": 0, **kwargs}

    def get_snapshot(self, snapshot_id: str):
        self.read_calls.append("snapshot")
        raise MarketDataServiceError(
            "market_data_snapshot_not_found",
            f"未找到行情快照：{snapshot_id}",
            status_code=404,
        )

    def list_bundles(self, **kwargs):
        self.read_calls.append("bundles")
        return {"items": [], "total_count": 0, **kwargs}

    def get_bundle(self, bundle_id: str):
        self.read_calls.append("bundle")
        return {"bundle_id": bundle_id, "status": "published"}

    def list_providers(self):
        self.read_calls.append("providers")
        return {"items": [], "total_count": 0}

    def create_collection_job(self, **kwargs):
        self.explicit_collection_calls.append(dict(kwargs))
        return {"id": "job-explicit-1", "status": "queued"}

    def start_collection_agent_recovery(self, job_id: str, **kwargs):
        self.agent_recovery_calls.append({"job_id": job_id, **kwargs})
        return {
            "id": job_id,
            "agent_recovery": {"status": "queued"},
            "freeze_status": "awaiting_recovery",
        }

    def confirm_collection_freeze(self, job_id: str, **kwargs):
        self.confirm_freeze_calls.append({"job_id": job_id, **kwargs})
        return {
            "id": job_id,
            "publication_status": "published",
            "freeze_status": "frozen",
        }

    def create_manual_snapshot(self, **kwargs):
        self.manual_snapshot_calls.append(dict(kwargs))
        return {
            "snapshot_id": "snapshot-manual-1",
            "status": kwargs["request"]["status"],
            "evidence_grade": "B",
        }

    def resolve_conflict(self, conflict_id: str, **kwargs):
        raise MarketDataServiceError(
            "conflict_resolution_invalid",
            f"冲突决议与候选快照范围不一致：{conflict_id}",
            status_code=409,
        )

    def close(self) -> None:
        self.closed = True


class _LegacyFinance:
    def __init__(self) -> None:
        self.bound_center = None
        self.get_market_data_calls = 0

    def bind_market_data_center(self, service) -> None:
        self.bound_center = service

    def get_market_data(self, symbol: str, *, refresh: bool = False):
        self.get_market_data_calls += 1
        return {"route_owner": "legacy_dynamic", "symbol": symbol, "refresh": refresh}


class _Closable:
    def close(self) -> None:
        return None


class MarketDataApiAppIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        workspace = Path(self.temp_dir.name)
        self.settings = Settings.from_env(REPO_ROOT).model_copy(
            update={
                "workspace_root": workspace,
                "runs_root": workspace / "runs",
                "memory_root": workspace / "memory",
                "domain_packs_root": REPO_ROOT / "domain_packs",
                "market_data_auto_collection_enabled": False,
            }
        )
        self.run_service = _RunService()
        self.center = _MarketDataCenter()
        self.legacy = _LegacyFinance()
        self.database_seen = None

        def center_factory(_settings, database):
            self.database_seen = database
            return self.center

        self.app = create_app(
            settings=self.settings,
            api_config=ApiConfig(api_token="ordinary-user-token", admin_tokens=(API_TOKEN,)),
            agent_os_factory=_AgentOS,
            run_service_factory=lambda _agent_os: self.run_service,
            chat_service_factory=lambda _settings: _Closable(),
            event_source_factory=lambda _settings, _config: _Closable(),
            finance_market_data_factory=lambda _settings: self.legacy,
            market_data_center_factory=center_factory,
            paper_trading_factory=lambda _settings, _market: _Closable(),
            signal_engine_factory=lambda _database: _Closable(),
            strategy_backtesting_factory=lambda _settings, _database, _market: _Closable(),
            strategy_verification_factory=lambda _database: _Closable(),
            strategy_paper_execution_factory=lambda _database, _paper: _Closable(),
            repo_root=REPO_ROOT,
        )
        self.client_context = TestClient(self.app, raise_server_exceptions=False)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_app_wires_one_shared_database_and_registers_static_routes_first(self) -> None:
        self.assertIsNotNone(self.database_seen)
        self.assertIs(self.legacy.bound_center, self.center)
        self.assertIsNotNone(self.run_service.report_inputs)
        self.assertEqual(self.center.start_calls, 1)
        self.assertEqual(self.center.collection.resume_calls, 1)

        # FastAPI 0.139 keeps included routers lazy.  Inspect the v2 router's
        # inclusion order instead of relying on the older flattened route list.
        v2_router = next(
            route.original_router
            for route in self.app.routes
            if getattr(getattr(route, "original_router", None), "prefix", None)
            == "/api/v2"
        )
        included_prefixes = [
            getattr(getattr(route, "original_router", None), "prefix", None)
            for route in v2_router.routes
        ]
        self.assertLess(
            included_prefixes.index("/finance/market-data"),
            included_prefixes.index("/finance"),
        )

        response = self.client.get(
            "/api/v2/finance/market-data/contract", headers=auth_headers()
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["route_owner"], "market_data_center")
        self.assertEqual(self.legacy.get_market_data_calls, 0)

    def test_get_endpoints_are_side_effect_free_and_post_job_is_explicit(self) -> None:
        query_date = datetime.now(timezone.utc).date().isoformat()
        requests = [
            ("/api/v2/finance/market-data/contract", None),
            (
                "/api/v2/finance/market-data/coverage",
                {"symbol": "600519.SH", "as_of_date": query_date},
            ),
            ("/api/v2/finance/market-data/snapshots", None),
            ("/api/v2/finance/market-data/bundles", None),
            ("/api/v2/finance/market-data/providers", None),
        ]
        for path, params in requests:
            response = self.client.get(path, params=params, headers=auth_headers())
            self.assertEqual(response.status_code, 200, response.text)

        self.assertEqual(self.center.explicit_collection_calls, [])
        self.assertEqual(self.legacy.get_market_data_calls, 0)

        created = self.client.post(
            "/api/v2/finance/market-data/collection-jobs",
            headers=auth_headers(),
            json={
                "symbols": ["600519.SH"],
                "start_date": query_date,
                "end_date": query_date,
                "dimensions": ["0_basic"],
                "mode": "missing_or_stale",
                "max_concurrency": 5,
                "publish_when_complete": False,
            },
        )
        self.assertEqual(created.status_code, 202, created.text)
        self.assertEqual(created.json()["id"], "job-explicit-1")
        self.assertEqual(len(self.center.explicit_collection_calls), 1)

    def test_collection_job_list_forwards_backtest_scope_filters(self) -> None:
        query_date = datetime.now(timezone.utc).date().isoformat()
        response = self.client.get(
            "/api/v2/finance/market-data/collection-jobs",
            headers=auth_headers(),
            params={
                "symbol": "020464",
                "asset_family": "fund",
                "as_of_date": query_date,
                "status": "partial",
                "limit": 25,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["symbol"], "020464")
        self.assertEqual(payload["asset_family"], "fund")
        self.assertEqual(payload["as_of_date"], query_date)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["limit"], 25)

    def test_collection_agent_recovery_and_confirm_freeze_forward_owner_scope(
        self,
    ) -> None:
        recovery = self.client.post(
            "/api/v2/finance/market-data/collection-jobs/job-1/agent-recovery",
            headers=auth_headers("ordinary-user-token"),
        )
        self.assertEqual(recovery.status_code, 202, recovery.text)
        self.assertEqual(recovery.json()["agent_recovery"]["status"], "queued")
        self.assertEqual(self.center.agent_recovery_calls[0]["job_id"], "job-1")
        self.assertEqual(self.center.agent_recovery_calls[0]["user_id"], "api_token")

        missing_proposal = self.client.post(
            "/api/v2/finance/market-data/collection-jobs/job-1/confirm-freeze",
            headers=auth_headers("ordinary-user-token"),
            json={},
        )
        self.assertEqual(missing_proposal.status_code, 422, missing_proposal.text)
        self.assertEqual(self.center.confirm_freeze_calls, [])

        frozen = self.client.post(
            "/api/v2/finance/market-data/collection-jobs/job-1/confirm-freeze",
            headers=auth_headers("ordinary-user-token"),
            json={
                "proposal_hash": "a" * 64,
                "acknowledge_degraded": True,
            },
        )
        self.assertEqual(frozen.status_code, 200, frozen.text)
        self.assertEqual(frozen.json()["freeze_status"], "frozen")
        forwarded = self.center.confirm_freeze_calls[0]
        self.assertEqual(forwarded["job_id"], "job-1")
        self.assertEqual(forwarded["proposal_hash"], "a" * 64)
        self.assertTrue(forwarded["acknowledge_degraded"])
        self.assertEqual(forwarded["user_id"], "api_token")

    def test_collection_and_subscription_http_contract_rejects_concurrency_above_five(
        self,
    ) -> None:
        query_date = datetime.now(timezone.utc).date().isoformat()
        collection = self.client.post(
            "/api/v2/finance/market-data/collection-jobs",
            headers=auth_headers(),
            json={
                "symbols": ["600519.SH"],
                "start_date": query_date,
                "end_date": query_date,
                "max_concurrency": 6,
            },
        )
        subscription = self.client.put(
            "/api/v2/finance/market-data/subscriptions/600519.SH",
            headers=auth_headers(),
            json={
                "market": "CN_EQUITY",
                "enabled": True,
                "dimensions": "all",
                "max_concurrency": 6,
            },
        )

        self.assertEqual(collection.status_code, 422, collection.text)
        self.assertEqual(subscription.status_code, 422, subscription.text)
        self.assertEqual(collection.json()["error"]["code"], "validation_error")
        self.assertEqual(subscription.json()["error"]["code"], "validation_error")
        self.assertEqual(self.center.explicit_collection_calls, [])

    def test_fund_identity_and_collection_http_contract(self) -> None:
        query_date = datetime.now(timezone.utc).date().isoformat()
        contract = self.client.get(
            "/api/v2/finance/market-data/contract",
            params={"market": "CN_FUND"},
            headers=auth_headers(),
        )
        self.assertEqual(contract.status_code, 200, contract.text)
        self.assertEqual(contract.json()["query"]["market"], "CN_FUND")

        resolved = self.client.post(
            "/api/v2/finance/market-data/instruments/resolve",
            headers=auth_headers(),
            json={"query": "020464", "asset_family": "fund"},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["resolved"]["asset_type"], "otc_fund")

        created = self.client.post(
            "/api/v2/finance/market-data/collection-jobs",
            headers=auth_headers(),
            json={
                "symbols": ["020464"],
                "start_date": query_date,
                "end_date": query_date,
                "dimensions": ["fund_top_holdings"],
                "asset_family": "fund",
                "top_n": 10,
            },
        )
        self.assertEqual(created.status_code, 202, created.text)
        forwarded = self.center.explicit_collection_calls[-1]["request"]
        self.assertEqual(forwarded["asset_family"], "fund")
        self.assertEqual(forwarded["top_n"], 10)

    def test_market_data_service_errors_keep_code_status_and_safe_details(self) -> None:
        response = self.client.get(
            "/api/v2/finance/market-data/snapshots/missing", headers=auth_headers()
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(
            response.json()["error"]["code"], "market_data_snapshot_not_found"
        )
        self.assertIn("未找到行情快照", response.json()["error"]["message"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_manual_snapshot_http_contract_uses_object_provenance_and_manifest(self) -> None:
        query_date = datetime.now(timezone.utc).date().isoformat()
        payload = {
            "symbol": "600519.SH",
            "market": "cn_equity",
            "dimension_id": "16_lhb",
            "as_of_date": query_date,
            "cutoff_at": f"{query_date}T23:59:59+08:00",
            "published_at": f"{query_date}T08:00:00+08:00",
            "evidence_grade": "B",
            "status": "verified_empty",
            "payload": {},
            "field_sources": {"$": "manual.evidence"},
            "query_manifest": {
                "query_window": {"start": query_date, "end": query_date},
                "required_sources": ["official-lhb"],
                "sources": [
                    {"source": "official-lhb", "status": "success", "result_count": 0}
                ],
                "result_count": 0,
            },
            "evidence": [
                {
                    "provider": "manual.evidence",
                    "source_url": "https://example.test/proof",
                    "published_at": f"{query_date}T08:00:00+08:00",
                    "observed_at": f"{query_date}T09:00:00+08:00",
                    "grade": "B",
                    "immutable_proof": True,
                    "is_manual": True,
                }
            ],
            "reason": "截止日前不可变证据人工补录",
            "empty_reason": "权威有界查询成功且返回零条",
        }
        response = self.client.post(
            "/api/v2/finance/market-data/manual-snapshots",
            headers=auth_headers(),
            json=payload,
        )

        self.assertEqual(response.status_code, 201, response.text)
        forwarded = self.center.manual_snapshot_calls[0]["request"]
        self.assertIsInstance(forwarded["field_sources"], dict)
        self.assertIsInstance(forwarded["query_manifest"], dict)

        invalid = self.client.post(
            "/api/v2/finance/market-data/manual-snapshots",
            headers=auth_headers(),
            json={**payload, "query_manifest": []},
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(invalid.json()["error"]["code"], "validation_error")
        self.assertEqual(len(self.center.manual_snapshot_calls), 1)

    def test_global_market_data_governance_requires_admin_role(self) -> None:
        query_date = datetime.now(timezone.utc).date().isoformat()
        user_headers = auth_headers("ordinary-user-token")
        requests = [
            (
                "/api/v2/finance/market-data/manual-snapshots",
                {
                    "symbol": "600519.SH",
                    "dimension_id": "0_basic",
                    "as_of_date": query_date,
                    "cutoff_at": f"{query_date}T23:59:59+08:00",
                    "published_at": f"{query_date}T08:00:00+08:00",
                    "status": "available",
                    "payload": {},
                    "evidence": [{}],
                    "reason": "普通用户不得修改全局事实",
                },
            ),
            (
                "/api/v2/finance/market-data/snapshots/global-snapshot/revoke",
                {"reason": "普通用户不得撤销全局事实"},
            ),
            (
                "/api/v2/finance/market-data/conflicts/global-conflict/resolve",
                {"resolution": "mark_invalid", "reason": "普通用户不得决议全局冲突"},
            ),
            (
                "/api/v2/finance/market-data/bundles/publish",
                {
                    "symbol": "600519.SH",
                    "as_of_date": query_date,
                },
            ),
        ]
        for path, body in requests:
            response = self.client.post(path, headers=user_headers, json=body)
            self.assertEqual(response.status_code, 403, (path, response.text))
            self.assertEqual(response.json()["error"]["code"], "insufficient_role")

        # Ordinary users retain owner-scoped explicit acquisition.
        allowed = self.client.post(
            "/api/v2/finance/market-data/collection-jobs",
            headers=user_headers,
            json={
                "symbols": ["600519.SH"],
                "start_date": query_date,
                "end_date": query_date,
                "dimensions": ["0_basic"],
            },
        )
        self.assertEqual(allowed.status_code, 202, allowed.text)

    def test_conflict_resolution_domain_error_maps_to_http_409(self) -> None:
        response = self.client.post(
            "/api/v2/finance/market-data/conflicts/invalid/resolve",
            headers=auth_headers(),
            json={
                "resolution": "mark_invalid",
                "reason": "畸形候选范围必须返回领域冲突而不是服务器错误",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["error"]["code"], "conflict_resolution_invalid"
        )


if __name__ == "__main__":
    unittest.main()
