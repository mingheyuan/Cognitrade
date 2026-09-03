from __future__ import annotations

import time
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from domain_packs.finance_ashare.subagents.runtime import collect_snapshot
from research_system.api.agent_os_run_service import AgentOSRunService
from research_system.api.fund_holding_report_service import (
    FundHoldingReportService,
    FundHoldingReportsUnavailable,
)
from research_system.market_data.bundles import BundlePublisher, bundle_manifest_hash
from research_system.market_data.contracts import LEGACY_UZI_CONTRACT_VERSIONS
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)
from research_system.market_data.fund_holdings_adapter import (
    FundTopHoldingsSnapshotAdapter,
)
from research_system.market_data.fund_holdings_contracts import (
    FUND_TOP_HOLDINGS_REGISTRY,
)
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    ReportDataManifest,
    ReportDataManifestDependency,
    ReportDataManifestItem,
)
from research_system.market_data.report_inputs import (
    DatabaseReportInputGateway,
    ReportInputError,
    ReportInputService,
    slots_for_manifest,
    validate_bundle_only_input,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.runtime.storage.jsonl import read_json
from research_system.schemas.protocol import RequestEnvelope
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.orchestration import ReportOrchestrationService
from tests.market_data.test_fund_holdings_foundation import (
    OBSERVED as FUND_OBSERVED,
)
from tests.market_data.test_fund_holdings_foundation import (
    STOCK_CODES as FUND_STOCK_CODES,
)
from tests.market_data.test_fund_holdings_foundation import (
    direct_skill_output,
)
from tests.market_data.test_pit_core import (
    _input as strict_market_data_input,
)
from tests.market_data.test_pit_core import (
    seed_strict_bundle,
)
from tests.test_fund_holding_reports import (
    FakeAgentOS,
    FakeRunService,
    equity_fund,
    snapshot_artifact,
)


def _manifest(*symbols: str) -> dict:
    items = []
    for symbol in symbols:
        bundle_items = []
        snapshots = []
        for index, (dimension, snapshot_type) in enumerate(
            (("0_basic", "market"), ("1_financials", "fundamental"))
        ):
            content_hash = f"{index + 1:064x}"
            bundle_items.append(
                {
                    "dimension_id": dimension,
                    "ordinal": index,
                    "snapshot_id": f"snapshot-{symbol}-{index}",
                    "snapshot_type": snapshot_type,
                    "content_hash": content_hash,
                    "availability_status": "available",
                    "evidence_grade": "A",
                }
            )
            snapshots.append(
                {
                    "symbol": symbol,
                    "dimension_id": dimension,
                    "snapshot_type": snapshot_type,
                    "snapshot_id": f"snapshot-{symbol}-{index}",
                    "content_hash": content_hash,
                    "availability_status": "available",
                    "evidence_grade": "A",
                    "contract_version": "test-v1",
                    "snapshot_date": "2026-08-20",
                    "facts": {"value": index + 1},
                    "source_refs": [
                        {
                            "type": "market_data_evidence",
                            "id": f"evidence-{symbol}-{index}",
                        }
                    ],
                }
            )
        bundle_id = f"bundle-{symbol}"
        cutoff = datetime(2026, 8, 20, 15, 59, 59, 999999, tzinfo=UTC)
        bundle_hash = bundle_manifest_hash(
            symbol=symbol,
            market="CN_EQUITY",
            as_of_date=date(2026, 8, 20),
            cutoff_at=cutoff,
            contract_version="test-v1",
            items=[
                {
                    "dimension": row["dimension_id"],
                    "ordinal": row["ordinal"],
                    "snapshot_id": row["snapshot_id"],
                    "content_hash": row["content_hash"],
                    "status": row["availability_status"],
                    "evidence_grade": row["evidence_grade"],
                }
                for row in bundle_items
            ],
        )
        items.append(
            {
                "symbol": symbol,
                "bundle_id": bundle_id,
                "bundle": {
                    "bundle_id": bundle_id,
                    "symbol": symbol,
                    "market": "CN_EQUITY",
                    "as_of_date": "2026-08-20",
                    "cutoff_at": cutoff.isoformat(),
                    "status": "published",
                    "report_ready": True,
                    "expected_dimension_count": 2,
                    "manifest_hash": bundle_hash,
                    "contract_version": "test-v1",
                    "items": bundle_items,
                },
                "snapshots": snapshots,
                "source_refs": [{"type": "market_data_bundle", "id": bundle_id}],
            }
        )
    return {
        "manifest_id": "manifest-1",
        "manifest_hash": "a" * 64,
        "contract_version": "test-v1",
        "status": "frozen",
        "as_of_date": "2026-08-20",
        "items": items,
    }


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def freeze_report_manifest(self, **kwargs):
        self.calls.append(dict(kwargs))
        return _manifest(*kwargs["symbols"])


class _CollectContext:
    def __init__(self, slots: dict) -> None:
        self.node = SimpleNamespace(
            node_id="collect",
            inputs={
                "intent": "stock_check",
                "research_profile": "lite",
                "data_source_mode": "bundle_only",
                **slots,
            },
        )
        self._query = SimpleNamespace(
            artifact_id="query-1",
            content={"query": "分析 600519", "symbols": ["600519"]},
            source_refs=[],
        )

    def require_artifact(self, artifact_type: str):
        if artifact_type != "normalized_query":
            raise AssertionError(artifact_type)
        return self._query


class _ArtifactBackedCollectContext(_CollectContext):
    def __init__(self, slots: dict) -> None:
        super().__init__({})
        self.node.inputs = {
            "intent": "stock_check",
            "research_profile": "lite",
            "data_source_mode": "bundle_only",
        }
        self._request = SimpleNamespace(
            artifact_id="request-1",
            artifact_type="user_request",
            content=dict(slots),
            source_refs=[{"type": "request", "id": "request-1"}],
        )

    def artifacts(self, artifact_type: str):
        if artifact_type == "normalized_query":
            return [self._query]
        if artifact_type == "user_request":
            return [self._request]
        return []


class ReportInputServiceTests(unittest.TestCase):
    def test_binding_report_inputs_starts_deferred_fund_batch_recovery(self) -> None:
        calls: list[object] = []
        boundary = SimpleNamespace(freeze_request=lambda request: request)
        holding_reports = SimpleNamespace(
            recover_pending_batches=lambda: calls.append("recover")
        )
        agent_os = SimpleNamespace(
            bind_resolved_request_preflight=lambda callback: calls.append(callback)
        )
        run_service = object.__new__(AgentOSRunService)
        run_service.agent_os = agent_os
        run_service._report_inputs = None
        run_service._fund_holding_reports = holding_reports

        run_service.bind_report_inputs(boundary)

        self.assertIs(run_service.report_input_service, boundary)
        self.assertEqual(calls, [boundary.freeze_request, "recover"])

    def test_canonical_uzi_key_changes_when_bundle_manifest_changes(self) -> None:
        first = ReportOrchestrationService._canonical_uzi_key(
            "600519",
            date(2026, 8, 20),
            "pin-hash",
            "a" * 64,
        )
        second = ReportOrchestrationService._canonical_uzi_key(
            "600519",
            date(2026, 8, 20),
            "pin-hash",
            "b" * 64,
        )
        self.assertNotEqual(first, second)

    def test_formal_request_freezes_and_injects_bundle_only_slots(self) -> None:
        gateway = _Gateway()
        service = ReportInputService(gateway)
        request = RequestEnvelope(
            intent="stock_check",
            user_query="分析 600519",
            slots={"symbols": ["600519"]},
            constraints={
                "research_profile": "lite",
                "analysis_as_of_date": "2026-08-20",
            },
        )

        frozen = service.freeze_request(request)

        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(frozen.constraints["data_source_mode"], "bundle_only")
        self.assertEqual(frozen.constraints["report_manifest_hash"], "a" * 64)
        self.assertEqual(len(frozen.slots["snapshots"]), 2)
        self.assertEqual(
            frozen.slots["market_data_bundle"]["bundle_id"], "bundle-600519"
        )
        # Idempotent retries keep the original frozen input and never resolve a
        # newer Bundle.
        self.assertIs(service.freeze_request(frozen), frozen)
        self.assertEqual(len(gateway.calls), 1)

    def test_direct_uzi_method_envelope_cannot_bypass_bundle_preflight(self) -> None:
        gateway = _Gateway()
        service = ReportInputService(gateway)
        request = RequestEnvelope(
            intent="uzi_dcf",
            user_query="/dcf 600519",
            slots={"symbols": ["600519"]},
            constraints={"analysis_as_of_date": "2026-08-20"},
        )

        frozen = service.freeze_request(request)

        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(frozen.constraints["data_source_mode"], "bundle_only")
        self.assertEqual(frozen.slots["market_data_bundle"]["bundle_id"], "bundle-600519")

    def test_portfolio_projection_gives_each_child_only_its_bundle(self) -> None:
        manifest = _manifest("600519", "000858")
        child = slots_for_manifest(manifest, symbols=["000858"])
        self.assertEqual({row["symbol"] for row in child["snapshots"]}, {"000858"})
        self.assertEqual(child["market_data_bundle"]["bundle_id"], "bundle-000858")
        self.assertEqual(len(child["report_data_manifest"]["items"]), 1)
        self.assertEqual(
            child["report_data_manifest"]["parent_manifest_hash"], "a" * 64
        )
        validate_bundle_only_input(
            snapshots=child["snapshots"],
            manifest=child["report_data_manifest"],
            bundle=child["market_data_bundle"],
            symbols=["000858"],
        )

    def test_hash_mismatch_is_a_hard_failure(self) -> None:
        manifest = _manifest("600519")
        slots = slots_for_manifest(manifest)
        slots["snapshots"][0]["content_hash"] = "f" * 64
        with self.assertRaises(ReportInputError) as captured:
            validate_bundle_only_input(
                snapshots=slots["snapshots"],
                manifest=slots["report_data_manifest"],
                bundle=slots["market_data_bundle"],
                symbols=["600519"],
            )
        self.assertEqual(captured.exception.code, "bundle_snapshot_hash_mismatch")

    def test_collect_node_consumes_bundle_without_provider_fallback(self) -> None:
        manifest = _manifest("600519")
        slots = slots_for_manifest(manifest)
        output = collect_snapshot(_CollectContext(slots))
        bundle = next(
            row
            for row in output.artifacts
            if row.artifact_type == "stock_snapshot_bundle"
        )
        self.assertEqual(
            bundle.content["collection"]["data_source_mode"], "bundle_only"
        )
        self.assertEqual(bundle.content["collection"]["manifest_hash"], "a" * 64)
        self.assertEqual(len(bundle.content["snapshots"]), 2)

    def test_collect_node_loads_large_bundle_inputs_from_user_request_artifact(self) -> None:
        manifest = _manifest("600519")
        slots = slots_for_manifest(manifest)
        context = _ArtifactBackedCollectContext(slots)

        output = collect_snapshot(context)

        bundle = next(
            row
            for row in output.artifacts
            if row.artifact_type == "stock_snapshot_bundle"
        )
        self.assertNotIn("snapshots", context.node.inputs)
        self.assertNotIn("report_data_manifest", context.node.inputs)
        self.assertEqual(bundle.content["collection"]["data_source_mode"], "bundle_only")
        self.assertEqual(bundle.content["collection"]["manifest_hash"], "a" * 64)
        self.assertEqual(len(bundle.content["snapshots"]), 2)


class FundHoldingBundleInputTests(unittest.TestCase):
    def test_parent_freezes_once_and_children_receive_only_their_bundle(self) -> None:
        with TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "runs"
            agent_os = FakeAgentOS(runs_root, delay=0.01)
            run_service = FakeRunService(
                [snapshot_artifact(funds=[equity_fund(count=2)])],
                agent_os,
            )
            gateway = _Gateway()
            run_service.report_input_service = ReportInputService(gateway)
            service = FundHoldingReportService(
                settings=SimpleNamespace(
                    runs_root=runs_root,
                    fund_holding_report_concurrency=2,
                    uzi_llm_max_concurrency=1,
                ),
                agent_os=agent_os,
                run_service=run_service,
            )
            try:
                created = service.create_batch(
                    "source-run",
                    fund_code="110011",
                    profile="lite",
                    as_of_date="2026-08-20",
                )
                deadline = time.monotonic() + 3
                final = created
                while final["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(0.01)
                    final = service.detail(created["batch_id"])
                self.assertEqual(final["status"], "completed")
                self.assertEqual(len(gateway.calls), 1)
                self.assertEqual(final["report_manifest_hash"], "a" * 64)
                first = final["holdings"][0]
                self.assertRegex(
                    final["bundle_manifest_hashes"][first["symbol"]],
                    r"^[0-9a-f]{64}$",
                )
                request = read_json(
                    agent_os.run_store.load_run_paths(first["child_run_id"]).request
                )
                self.assertEqual(
                    request["constraints"]["data_source_mode"], "bundle_only"
                )
                self.assertEqual(
                    request["constraints"]["market_data_bundle_manifest_hash"],
                    final["bundle_manifest_hashes"][first["symbol"]],
                )
                self.assertEqual(
                    {row["symbol"] for row in request["slots"]["snapshots"]},
                    {first["symbol"]},
                )
                self.assertEqual(
                    request["slots"]["market_data_bundle"]["bundle_id"],
                    f"bundle-{first['symbol']}",
                )
                self.assertNotIn("_report_data_manifest", final)
            finally:
                service.close()


class DatabaseReportInputGatewayTests(unittest.TestCase):
    def test_legacy_uzi_bundle_remains_read_only_report_compatible(self) -> None:
        database = StrategyDatabase("sqlite:///:memory:")
        database.create_schema()
        as_of = date(2026, 8, 20)
        legacy_version = next(iter(LEGACY_UZI_CONTRACT_VERSIONS))
        legacy_registry = DimensionRegistry(
            tuple(DEFAULT_DIMENSION_REGISTRY),
            contract_version=legacy_version,
        )
        try:
            with database.session() as session:
                repository = MarketDataRepository(session)
                ingestion = SnapshotIngestionService(
                    repository,
                    registry=legacy_registry,
                )
                for contract in legacy_registry:
                    ingestion.ingest(
                        strict_market_data_input(contract.dimension).model_copy(
                            update={"contract_version": legacy_version}
                        )
                    )
                legacy_bundle = BundlePublisher(
                    repository,
                    registry=legacy_registry,
                ).publish(
                    symbol="600519.SH",
                    market="CN_EQUITY",
                    as_of_date=as_of,
                    published_by="legacy-test",
                    now=datetime(2026, 8, 21, 1, tzinfo=UTC),
                )

            manifest = DatabaseReportInputGateway(
                database,
                clock=lambda: datetime(2026, 8, 21, 1, tzinfo=UTC),
            ).freeze_report_manifest(
                symbols=["600519"],
                as_of_date=as_of,
                tenant_id="tenant-legacy",
                user_id="user-legacy",
                workflow_pins={"research_profile": "lite"},
                context={"report_kind": "single_stock"},
            )

            self.assertEqual(manifest["items"][0]["bundle_id"], legacy_bundle.id)
            self.assertEqual(
                manifest["items"][0]["bundle"]["contract_version"],
                legacy_version,
            )
            self.assertEqual(len(manifest["items"][0]["snapshots"]), 22)
        finally:
            database.close()

    def test_real_fund_batch_fails_closed_when_parent_bundle_is_missing(self) -> None:
        database = StrategyDatabase("sqlite:///:memory:")
        database.create_schema()
        service = None
        try:
            with TemporaryDirectory() as temporary:
                runs_root = Path(temporary) / "runs"
                agent_os = FakeAgentOS(runs_root)
                source = direct_skill_output()
                run_service = FakeRunService(
                    [snapshot_artifact(funds=list(source["funds"]))],
                    agent_os,
                )
                run_service.report_input_service = ReportInputService(
                    DatabaseReportInputGateway(
                        database,
                        clock=lambda: datetime(2026, 8, 20, 4, tzinfo=UTC),
                    )
                )
                service = FundHoldingReportService(
                    settings=SimpleNamespace(
                        runs_root=runs_root,
                        fund_holding_report_concurrency=2,
                        uzi_llm_max_concurrency=1,
                    ),
                    agent_os=agent_os,
                    run_service=run_service,
                )
                with self.assertRaises(ReportInputError) as captured:
                    service.create_batch(
                        "source-run",
                        fund_code="020464",
                        profile="lite",
                        as_of_date="2026-08-20",
                    )
                self.assertEqual(
                    captured.exception.code,
                    "fund_holding_bundle_missing",
                )
                self.assertFalse(runs_root.exists() and any(runs_root.iterdir()))
        finally:
            if service is not None:
                service.close()
            database.close()

    def test_bundle_only_input_must_reference_a_persisted_owned_manifest(self) -> None:
        database = StrategyDatabase("sqlite:///:memory:")
        database.create_schema()
        try:
            manifest = _manifest("600519")
            slots = slots_for_manifest(manifest)
            request = RequestEnvelope(
                intent="stock_check",
                user_query="分析 600519",
                tenant_id="tenant-1",
                user_id="user-1",
                slots={"symbols": ["600519"], **slots},
                constraints={
                    "research_profile": "lite",
                    "data_source_mode": "bundle_only",
                },
            )

            with self.assertRaises(ReportInputError) as captured:
                ReportInputService(DatabaseReportInputGateway(database)).freeze_request(
                    request
                )

            self.assertEqual(captured.exception.code, "report_manifest_not_found")
        finally:
            database.close()

    def test_freezes_persisted_manifest_from_published_bundle_idempotently(
        self,
    ) -> None:
        database = StrategyDatabase("sqlite:///:memory:")
        database.create_schema()
        as_of = date(2026, 8, 20)
        try:
            with database.session() as session:
                repository = MarketDataRepository(session)
                ingestion = SnapshotIngestionService(repository)
                for contract in DEFAULT_DIMENSION_REGISTRY:
                    ingestion.ingest(strict_market_data_input(contract.dimension))
                bundle = BundlePublisher(repository).publish(
                    symbol="600519.SH",
                    market="CN_EQUITY",
                    as_of_date=as_of,
                    published_by="test",
                    now=datetime(2026, 8, 21, 1, tzinfo=UTC),
                )

            service = ReportInputService(
                DatabaseReportInputGateway(
                    database,
                    clock=lambda: datetime(2026, 8, 21, 1, tzinfo=UTC),
                )
            )
            first = service.freeze_symbols(
                ["600519"],
                as_of_date=as_of,
                tenant_id="tenant-1",
                user_id="user-1",
                workflow_pins={"research_profile": "lite", "workflow_commit": "abc"},
                context={"report_kind": "single_stock"},
            )
            second = service.freeze_symbols(
                ["600519"],
                as_of_date=as_of,
                tenant_id="tenant-1",
                user_id="user-1",
                workflow_pins={"research_profile": "lite", "workflow_commit": "abc"},
                context={"report_kind": "single_stock"},
            )
            self.assertEqual(first["manifest_id"], second["manifest_id"])
            self.assertEqual(first["items"][0]["bundle_id"], bundle.id)
            self.assertEqual(len(first["items"][0]["snapshots"]), 22)
            resumed_request = RequestEnvelope(
                intent="stock_check",
                user_query="分析 600519",
                tenant_id="tenant-1",
                user_id="user-1",
                slots={"symbols": ["600519"], **slots_for_manifest(first)},
                constraints={
                    "research_profile": "lite",
                    "data_source_mode": "bundle_only",
                },
            )
            resumed = service.freeze_request(resumed_request)
            self.assertEqual(
                resumed.slots["market_data_bundle"]["bundle_id"],
                bundle.id,
            )
            self.assertEqual(
                resumed.constraints["report_manifest_hash"],
                first["manifest_hash"],
            )
            pinned_request = RequestEnvelope(
                intent="stock_check",
                user_query="使用冻结输入分析 600519",
                tenant_id="tenant-1",
                user_id="user-1",
                slots={"symbols": ["600519"]},
                constraints={
                    "research_profile": "lite",
                    "report_manifest_id": first["manifest_id"],
                    "report_manifest_hash": first["manifest_hash"],
                },
            )
            pinned = service.freeze_request(pinned_request)
            self.assertEqual(pinned.constraints["data_source_mode"], "bundle_only")
            self.assertEqual(
                pinned.slots["market_data_bundle"]["bundle_id"],
                bundle.id,
            )
            with database.session() as session:
                self.assertEqual(session.query(ReportDataManifest).count(), 1)
                self.assertEqual(session.query(ReportDataManifestItem).count(), 1)
        finally:
            database.close()

    def test_fund_manifest_pins_parent_disclosure_and_exact_top_ten(self) -> None:
        database = StrategyDatabase("sqlite:///:memory:")
        database.create_schema()
        as_of = date(2026, 8, 20)
        try:
            stock_only_bundle_ids = []
            for code in FUND_STOCK_CODES:
                seed_strict_bundle(database, code)
            with database.session() as session:
                repository = MarketDataRepository(session)
                for code in FUND_STOCK_CODES:
                    bundle = repository.latest_bundle(
                        symbol=(
                            f"{code}.SH"
                            if code.startswith(("5", "6", "9"))
                            else f"{code}.SZ"
                        ),
                        market="CN_EQUITY",
                        as_of_date=as_of,
                        contract_version=DEFAULT_DIMENSION_REGISTRY.contract_version,
                    )
                    self.assertIsNotNone(bundle)
                    stock_only_bundle_ids.append(bundle.id)
                fund_ingest = FundTopHoldingsSnapshotAdapter().adapt(
                    direct_skill_output(),
                    fund_code="020464",
                    as_of_date=as_of,
                    observed_at=FUND_OBSERVED,
                )
                fund_snapshot = SnapshotIngestionService(
                    repository,
                    registry=FUND_TOP_HOLDINGS_REGISTRY,
                ).ingest(fund_ingest)
                fund_bundle = BundlePublisher(
                    repository,
                    registry=FUND_TOP_HOLDINGS_REGISTRY,
                ).publish(
                    symbol="020464",
                    market="CN_FUND",
                    as_of_date=as_of,
                    cutoff_at=FUND_OBSERVED,
                    exchange_timezone="Asia/Shanghai",
                    published_by="fund-test",
                    now=FUND_OBSERVED,
                )

            gateway = DatabaseReportInputGateway(
                database,
                clock=lambda: datetime(2026, 8, 20, 4, tzinfo=UTC),
            )
            stock_only = gateway.freeze_report_manifest(
                symbols=FUND_STOCK_CODES,
                as_of_date=as_of,
                tenant_id="tenant-1",
                user_id="user-1",
                workflow_pins={"research_profile": "lite"},
                context={"report_kind": "portfolio"},
            )
            pinned = gateway.freeze_report_manifest(
                symbols=FUND_STOCK_CODES,
                as_of_date=as_of,
                tenant_id="tenant-1",
                user_id="user-1",
                workflow_pins={"research_profile": "lite"},
                context={
                    "report_kind": "fund_top_holdings",
                    "bundle_dependencies": [
                        {
                            "dependency_kind": "fund_top_holdings",
                            "bundle_id": fund_bundle.id,
                            "bundle_manifest_hash": fund_bundle.manifest_hash,
                            "snapshot_id": fund_snapshot.id,
                            "snapshot_content_hash": fund_snapshot.content_hash,
                        }
                    ],
                },
            )

            self.assertNotEqual(stock_only["manifest_hash"], pinned["manifest_hash"])
            self.assertEqual(len(pinned["items"]), 10)
            self.assertEqual(len(pinned["dependencies"]), 1)
            self.assertEqual(
                [item["bundle_id"] for item in pinned["items"]],
                stock_only_bundle_ids,
            )
            self.assertEqual(
                [item["weight"] for item in pinned["items"]],
                [float(10 - index) for index in range(10)],
            )
            loaded = gateway.load_frozen_report_manifest(
                manifest_id=pinned["manifest_id"],
                manifest_hash=pinned["manifest_hash"],
                tenant_id="tenant-1",
                user_id="user-1",
            )
            self.assertEqual(loaded["manifest_hash"], pinned["manifest_hash"])
            child_symbol = pinned["items"][0]["symbol"]
            scoped = gateway.load_frozen_report_manifest_items(
                manifest_id=pinned["manifest_id"],
                manifest_hash=pinned["manifest_hash"],
                tenant_id="tenant-1",
                user_id="user-1",
                symbols=[child_symbol],
            )
            self.assertEqual(
                [item["symbol"] for item in scoped["items"]],
                [child_symbol],
            )
            self.assertEqual(len(scoped["items"][0]["snapshots"]), 22)
            self.assertEqual(len(scoped["dependencies"]), 1)
            child_slots = slots_for_manifest(pinned, symbols=[child_symbol])
            child_request = RequestEnvelope(
                intent="stock_check",
                user_query=f"分析A股 {child_symbol}",
                user_id="user-1",
                tenant_id="tenant-1",
                slots={"symbols": [child_symbol], **child_slots},
                constraints={
                    "research_profile": "lite",
                    "data_source_mode": "bundle_only",
                    "fund_holding_report_child": True,
                },
            )
            verified_child = ReportInputService(gateway).freeze_request(child_request)
            self.assertEqual(len(verified_child.slots["snapshots"]), 22)
            self.assertEqual(
                [item["symbol"] for item in verified_child.slots["report_data_manifest"]["items"]],
                [child_symbol],
            )
            self.assertEqual(
                verified_child.constraints["report_manifest_hash"],
                pinned["manifest_hash"],
            )
            self.assertEqual(
                verified_child.constraints["market_data_bundle_manifest_hash"],
                pinned["items"][0]["bundle"]["manifest_hash"],
            )
            portfolio_request = RequestEnvelope(
                intent="uzi_portfolio_batch",
                user_query="使用冻结基金持仓输入生成组合报告",
                user_id="user-1",
                tenant_id="tenant-1",
                slots={
                    "portfolio_holdings": [
                        {"ticker": item["symbol"], "weight": item["weight"]}
                        for item in pinned["items"]
                    ]
                },
                constraints={
                    "research_profile": "deep",
                    "report_manifest_id": pinned["manifest_id"],
                    "report_manifest_hash": pinned["manifest_hash"],
                },
            )
            verified_portfolio = ReportInputService(gateway).freeze_request(
                portfolio_request
            )
            self.assertEqual(
                verified_portfolio.constraints["data_source_mode"],
                "bundle_only",
            )
            self.assertEqual(
                len(verified_portfolio.slots["report_data_manifest"]["items"]),
                10,
            )
            self.assertEqual(len(verified_portfolio.slots["snapshots"]), 220)
            with database.session() as session:
                self.assertEqual(session.query(ReportDataManifestDependency).count(), 1)
                MarketDataRepository(session).invalidate_bundle(
                    fund_bundle.id,
                    reason="parent disclosure revoked in regression",
                )
            with self.assertRaises(ReportInputError) as captured:
                gateway.load_frozen_report_manifest(
                    manifest_id=pinned["manifest_id"],
                    manifest_hash=pinned["manifest_hash"],
                    tenant_id="tenant-1",
                    user_id="user-1",
                )
            self.assertEqual(captured.exception.code, "report_manifest_invalidated")
        finally:
            database.close()

    def test_real_fund_batch_uses_parent_dependency_and_revocation_blocks_retry(self) -> None:
        database = StrategyDatabase("sqlite:///:memory:")
        database.create_schema()
        as_of = date(2026, 8, 20)
        service = None
        try:
            for code in FUND_STOCK_CODES:
                seed_strict_bundle(database, code)
            with database.session() as session:
                repository = MarketDataRepository(session)
                fund_ingest = FundTopHoldingsSnapshotAdapter().adapt(
                    direct_skill_output(),
                    fund_code="020464",
                    as_of_date=as_of,
                    observed_at=FUND_OBSERVED,
                )
                fund_snapshot = SnapshotIngestionService(
                    repository,
                    registry=FUND_TOP_HOLDINGS_REGISTRY,
                ).ingest(fund_ingest)
                fund_bundle = BundlePublisher(
                    repository,
                    registry=FUND_TOP_HOLDINGS_REGISTRY,
                ).publish(
                    symbol="020464",
                    market="CN_FUND",
                    as_of_date=as_of,
                    cutoff_at=FUND_OBSERVED,
                    exchange_timezone="Asia/Shanghai",
                    published_by="fund-batch-test",
                    now=FUND_OBSERVED,
                )

            with TemporaryDirectory() as temporary:
                runs_root = Path(temporary) / "runs"
                agent_os = FakeAgentOS(runs_root, delay=0.005)
                source = direct_skill_output()
                run_service = FakeRunService(
                    [snapshot_artifact(funds=list(source["funds"]))],
                    agent_os,
                )
                report_inputs = ReportInputService(
                    DatabaseReportInputGateway(
                        database,
                        clock=lambda: datetime(2026, 8, 20, 4, tzinfo=UTC),
                    )
                )
                run_service.report_input_service = report_inputs
                service = FundHoldingReportService(
                    settings=SimpleNamespace(
                        runs_root=runs_root,
                        fund_holding_report_concurrency=2,
                        uzi_llm_max_concurrency=1,
                    ),
                    agent_os=agent_os,
                    run_service=run_service,
                )
                created = service.create_batch(
                    "source-run",
                    fund_code="020464",
                    profile="lite",
                    as_of_date=as_of,
                )
                deadline = time.monotonic() + 5
                final = created
                while final["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(0.01)
                    final = service.detail(created["batch_id"])
                self.assertEqual(final["status"], "completed")
                self.assertEqual(final["counts"]["deliverable"], 10)

                frozen_manifest = service.frozen_report_manifest(created["batch_id"])
                self.assertIsNotNone(frozen_manifest)
                self.assertEqual(frozen_manifest["report_kind"], "fund_top_holdings")
                self.assertEqual(len(frozen_manifest["items"]), 10)
                self.assertEqual(len(frozen_manifest["dependencies"]), 1)
                self.assertEqual(
                    frozen_manifest["dependencies"][0]["bundle_id"],
                    fund_bundle.id,
                )
                first = final["holdings"][0]
                child_request_payload = read_json(
                    agent_os.run_store.load_run_paths(first["child_run_id"]).request
                )
                child_manifest = child_request_payload["slots"]["report_data_manifest"]
                self.assertEqual(len(child_manifest["items"]), 1)
                self.assertEqual(len(child_manifest["dependencies"]), 1)
                self.assertEqual(
                    child_manifest["dependencies"][0]["snapshot_id"],
                    fund_snapshot.id,
                )
                verified_child = report_inputs.freeze_request(
                    RequestEnvelope.model_validate(child_request_payload)
                )
                self.assertEqual(
                    verified_child.constraints["report_manifest_hash"],
                    frozen_manifest["manifest_hash"],
                )
                reuse = {
                    str(item["symbol"]): {
                        "run_id": item["child_run_id"],
                        "artifact_id": f"report-{item['symbol']}",
                        "content_hash": f"hash-{item['symbol']}",
                    }
                    for item in final["holdings"]
                }
                fully_reused = service.create_batch(
                    "source-run",
                    fund_code="020464",
                    profile="lite",
                    as_of_date=as_of,
                    canonical_reuse=reuse,
                    force=True,
                )
                self.assertEqual(fully_reused["status"], "completed")
                self.assertEqual(fully_reused["counts"]["deliverable"], 10)
                self.assertTrue(
                    all(item.get("canonical_reused") for item in fully_reused["holdings"])
                )

                with database.session() as session:
                    self.assertEqual(
                        session.query(ReportDataManifestDependency).count(),
                        1,
                    )
                    MarketDataRepository(session).invalidate_bundle(
                        fund_bundle.id,
                        reason="parent disclosure revoked after report batch",
                    )
                invalidated_detail = service.detail(created["batch_id"])
                self.assertEqual(invalidated_detail["status"], "failed")
                self.assertEqual(
                    invalidated_detail["report_manifest_status"],
                    "invalidated",
                )
                self.assertEqual(
                    invalidated_detail["counts"]["deliverable"],
                    0,
                )
                invalidated_artifacts = service.artifacts(created["batch_id"])
                self.assertEqual(invalidated_artifacts["status"], "failed")
                invalidated_history = service.history_row(created["batch_id"])
                self.assertEqual(invalidated_history["status"], "failed")
                self.assertTrue(
                    invalidated_history["display_summary"].startswith("failed:")
                )
                batch_artifact = next(
                    item
                    for item in invalidated_artifacts["artifacts"]
                    if item["artifact_type"] == "fund_holding_report_batch"
                )
                self.assertEqual(batch_artifact["content"]["status"], "failed")
                self.assertIn(
                    "fund_report_parent_dependency_invalidated",
                    batch_artifact["risk_flags"],
                )
                self.assertTrue(
                    all(
                        not item["report_available"]
                        for item in invalidated_detail["holdings"]
                        if item["supported"]
                    )
                )
                with self.assertRaises(FundHoldingReportsUnavailable):
                    service.build_pdf_zip(created["batch_id"])
                with self.assertRaises(FundHoldingReportsUnavailable):
                    service.require_child_report_lineage(first["child_run_id"])
                guarded_runs = object.__new__(AgentOSRunService)
                guarded_runs.agent_os = agent_os
                guarded_runs._fund_holding_reports = service
                blocked_surfaces = {
                    "detail": lambda: guarded_runs.detail(first["child_run_id"]),
                    "artifacts": lambda: guarded_runs.artifacts(first["child_run_id"]),
                    "replay": lambda: guarded_runs.replay(first["child_run_id"]),
                    "trace": lambda: guarded_runs.trace(first["child_run_id"]),
                    "thread": lambda: guarded_runs.thread(
                        first["child_run_id"],
                        "any-thread",
                    ),
                    "thread_replay": lambda: guarded_runs.thread_replay(
                        first["child_run_id"],
                        "any-thread",
                    ),
                    "resume": lambda: guarded_runs.resume(first["child_run_id"]),
                    "rerun_from": lambda: guarded_runs.rerun_from(
                        first["child_run_id"],
                        from_node="collect",
                    ),
                    "response": lambda: guarded_runs._response(
                        SimpleNamespace(user_id=None, tenant_id=None),
                        SimpleNamespace(
                            snapshot=SimpleNamespace(
                                run_id=first["child_run_id"],
                            )
                        ),
                    ),
                }
                for surface, operation in blocked_surfaces.items():
                    with self.subTest(surface=surface), self.assertRaises(
                        FundHoldingReportsUnavailable
                    ):
                        operation()
                reused_invalidated = service.detail(fully_reused["batch_id"])
                self.assertEqual(reused_invalidated["status"], "failed")
                self.assertEqual(reused_invalidated["counts"]["deliverable"], 0)
                with self.assertRaises(ReportInputError) as loaded_error:
                    service.frozen_report_manifest(created["batch_id"])
                self.assertEqual(
                    loaded_error.exception.code,
                    "report_manifest_invalidated",
                )
                with self.assertRaises(ReportInputError) as retry_error:
                    service.retry(
                        created["batch_id"],
                        symbols=[str(first["symbol"])],
                        force=True,
                    )
                self.assertEqual(
                    retry_error.exception.code,
                    "report_manifest_invalidated",
                )
        finally:
            if service is not None:
                service.close()
            database.close()


if __name__ == "__main__":
    unittest.main()
