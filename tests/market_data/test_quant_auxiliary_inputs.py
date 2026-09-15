from datetime import date, datetime, timedelta, timezone
from importlib import import_module

import pytest

from research_system.market_data.quant_auxiliary import (
    QuantAuxiliaryInputError,
    collect_quant_auxiliary_inputs,
    find_quant_auxiliary_binding,
    load_quant_auxiliary_recording,
    quant_input_hash,
    raw_quant_inputs,
    replay_quant_auxiliary_inputs,
    replay_quant_auxiliary_recording,
    validate_quant_auxiliary_recording,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.strategies.database import StrategyDatabase

DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 3, tzinfo=timezone.utc)
RAW = {"ticker": "688041.SH", "fund_managers": []}


class Source:
    def __init__(self, *, empty=False, fail=False):
        self.calls = []
        self.empty = empty
        self.fail = fail

    def fetch(self, operation, parameters, **kwargs):
        self.calls.append((operation, parameters))
        if self.fail:
            return {"status": "source_error", "rows": [], "errors": ["ConnectionError"]}
        if self.empty:
            return {"status": "verified_empty", "rows": []}
        return {
            "status": "available",
            "rows": (
                [{"fund_code": f"00000{i}", "fund_name": f"Fund {i}"} for i in range(3)]
                if operation == "all_holding_funds"
                else [{"股票代码": "688041", "占净值比例": 1.5}]
            ),
        }


@pytest.fixture()
def database():
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    yield database
    database.close()


def collect(database, source, raw=RAW, now=NOW, **kwargs):
    return collect_quant_auxiliary_inputs(
        database,
        raw=raw,
        as_of_date=DAY,
        source=source,
        now=now,
        **kwargs,
    )


def test_exact_native_requests_are_frozen_and_replayed_without_a_source(database):
    source = Source()
    binding = collect(database, source)
    assert binding["status"] == "available"
    assert source.calls[0] == (
        "all_holding_funds",
        {"ticker_code": "688041", "max_funds": 80},
    )
    assert source.calls[1][1] == {
        "fund_code": "000000",
        "top_n": 10,
        "holdings_year": "2025",
    }
    with replay_quant_auxiliary_inputs(
        database, binding=binding, raw=RAW
    ) as diagnostics:
        signal = import_module("lib.quant_signal").detect_quant_signal("688041.SH", [])
    assert signal["count"] == 3
    assert signal["is_quant_factor_style"] is True
    assert diagnostics["replayed_requests"] == 4
    assert len(source.calls) == 4


def test_large_manager_input_is_not_capped_and_duplicate_requests_are_reused(database):
    source = Source()
    raw = {
        "ticker": "688041.SH",
        "fund_managers": [{"fund_code": str(i % 21)} for i in range(751)],
    }
    binding = collect(database, source, raw)
    assert binding["status"] == "available"
    assert (
        len(source.calls) == 21
    )  # Native loops 751, source responses are shared by exact request.
    assert all(operation == "top_holdings" for operation, _ in source.calls)
    assert collect(database, source, raw) == binding
    assert len(source.calls) == 21
    with replay_quant_auxiliary_inputs(database, binding=binding, raw=raw):
        signal = import_module("lib.quant_signal").detect_quant_signal(
            "688041.SH", raw["fund_managers"]
        )
    assert signal["active_funds_total"] == signal["count"] == 751


@pytest.mark.parametrize("empty", [True, False])
def test_verified_empty_is_distinct_from_source_failure(database, empty):
    binding = collect(database, Source(empty=empty, fail=not empty))
    if empty:
        with replay_quant_auxiliary_inputs(database, binding=binding, raw=RAW):
            assert (
                import_module("lib.quant_signal").detect_quant_signal("688041.SH", [])[
                    "count"
                ]
                == 0
            )
    else:
        assert binding["status"] == "unassessed"
        with database.session() as session:
            selected = find_quant_auxiliary_binding(
                MarketDataRepository(session),
                raw=RAW,
                as_of_date=DAY,
                cutoff_at=NOW,
            )
        assert selected == binding
        assert selected["reason_codes"] == ["ConnectionError"]
        with replay_quant_auxiliary_inputs(database, binding=binding, raw=RAW) as diagnostics:
            signal = import_module("lib.quant_signal").detect_quant_signal("688041.SH", [])
        assert signal["count"] == 0  # Native fallback, NOT verified empty evidence.
        assert diagnostics["status"] == "degraded"
        assert diagnostics["reason_codes"] == ["ConnectionError"]
        assert diagnostics["replayed_requests"] == 0
        assert diagnostics["report_blocking"] is False


def test_historical_missing_archive_never_calls_today_provider(database):
    source = Source()
    binding = collect(database, source, now=NOW + timedelta(days=1))
    assert binding["status"] == "unassessed"
    assert "historical_auxiliary_unavailable" in binding["reason_codes"]
    assert source.calls == []


def test_deadline_does_not_turn_unattempted_requests_into_empty_results(database):
    source = Source()
    binding = collect(database, source, deadline=0)
    assert binding["status"] == "unassessed"
    assert "deadline_exhausted" in binding["reason_codes"]
    assert source.calls == []


def test_new_evidence_cannot_change_an_existing_binding(database):
    first = collect(database, Source())
    newer = collect(
        database, Source(empty=True), now=NOW + timedelta(minutes=1), force=True
    )
    assert first["content_hash"] != newer["content_hash"]
    with database.session() as session:
        selected = find_quant_auxiliary_binding(
            MarketDataRepository(session),
            raw=RAW,
            as_of_date=DAY,
            cutoff_at=NOW + timedelta(minutes=2),
        )
    assert selected == newer
    with replay_quant_auxiliary_inputs(database, binding=first, raw=RAW):
        assert (
            import_module("lib.quant_signal").detect_quant_signal("688041.SH", [])[
                "count"
            ]
            == 3
        )
    with (
        pytest.raises(QuantAuxiliaryInputError, match="native_quant_input_mismatch"),
        replay_quant_auxiliary_inputs(
            database,
            binding=first,
            raw={**RAW, "fund_managers": [{"fund_code": "new"}]},
        ),
    ):
        pass


def test_scope_preserves_native_optional_style_fallback_and_discloses_failure(database):
    binding = collect(database, Source(fail=True))
    with replay_quant_auxiliary_inputs(database, binding=binding, raw=RAW) as diagnostics:
        style = import_module("lib.stock_style").detect_style({"code": "688041.SH"}, RAW)
    assert style == import_module("lib.stock_style").BALANCED
    assert diagnostics["status"] == "degraded"
    assert diagnostics["missing_request_count"] == 1


def test_cross_midnight_response_keeps_real_observation_and_stops_new_requests(
    database,
):
    current = [datetime(2026, 9, 8, 15, 59, 59, tzinfo=timezone.utc)]

    class LateSource(Source):
        def fetch(self, *args, **kwargs):
            response = super().fetch(*args, **kwargs)
            current[0] = datetime(2026, 9, 8, 16, 0, 1, tzinfo=timezone.utc)
            return response

    source = LateSource()
    binding = collect_quant_auxiliary_inputs(
        database, raw=RAW, as_of_date=DAY, source=source, clock=lambda: current[0]
    )
    assert binding["status"] == "unassessed"
    assert "native_quant_pit_violation" in binding["reason_codes"]
    assert "historical_auxiliary_unavailable" in binding["reason_codes"]
    assert len(source.calls) == 1
    with database.session() as session:
        assert (
            find_quant_auxiliary_binding(
                MarketDataRepository(session),
                raw=RAW,
                as_of_date=DAY,
                cutoff_at=datetime(2026, 9, 8, 15, 59, 59, 999999, tzinfo=timezone.utc),
            )["status"]
            == "unassessed"
        )


def test_pure_transport_rejects_changed_response_and_raw_without_database(database):
    binding = collect(database, Source())
    with database.session() as session:
        recording = load_quant_auxiliary_recording(
            MarketDataRepository(session), binding=binding, raw=RAW, cutoff_at=NOW
        )
    with replay_quant_auxiliary_recording(recording, raw=RAW, binding=binding):
        assert (
            import_module("lib.quant_signal").detect_quant_signal("688041.SH", [])[
                "count"
            ]
            == 3
        )
    first = next(iter(recording["responses"].values()))
    first["rows"] = []
    with pytest.raises(
        QuantAuxiliaryInputError, match="native_quant_recording_mismatch"
    ):
        validate_quant_auxiliary_recording(recording, raw=RAW, binding=binding)


@pytest.mark.parametrize("limited", [False, True])
@pytest.mark.parametrize(
    "snapshot_type,field",
    [
        ("fund_holders", "fund_managers"),
        ("similar_stocks", "similar_stocks"),
    ],
)
def test_unsafe_historical_bonus_cannot_escape_through_legacy_raw_fallback(
    limited,
    snapshot_type,
    field,
):
    from copy import deepcopy

    from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
        build_uzi_raw,
    )

    row = {
        "symbol": "600519.SH",
        "snapshot_type": snapshot_type,
        "facts": {
            "_uzi_raw_result": {
                "data": {field: [{"code": "current-only"}]},
                "source": "recorded",
            },
        },
    }
    if limited:
        row["facts"]["_uzi_transport_limitations"] = {
            "bonus_status": "not_fixed_historical",
            "reason": "no_bound_pre_cutoff_symbol_transport_observation",
        }
    original = deepcopy(row)
    raw = build_uzi_raw([row], "600519.SH")
    assert (field in raw) is not limited
    if limited:
        assert "current-only" not in str(raw)
    assert row == original


@pytest.mark.parametrize("managers", [None, [], [{"fund_code": "000001"}]])
@pytest.mark.parametrize("bound", [True, False])
def test_actual_adapter_bundle_and_quant_projection_use_identical_fixed_input(
    database,
    managers,
    bound,
):
    from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
        build_uzi_raw,
    )
    from research_system.market_data.bundles import BundleMaterializer, BundlePublisher
    from research_system.market_data.contracts import cutoff_at_for_date
    from research_system.market_data.evidence_bindings import freeze_evidence_bindings
    from research_system.market_data.uzi_adapter import UziSnapshotAdapter
    from tests.market_data.test_native_symbol_transport import _source
    from tests.market_data.test_pit_core import AS_OF, _ingest_complete_set
    from tests.market_data.test_pit_core import NOW as PIT_NOW

    _, source = _source({"fund_managers": managers, "similar_stocks": []})
    value = UziSnapshotAdapter().adapt(
        source,
        symbol="600519.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=AS_OF,
    )
    with database.session() as session:
        repository, ingestion = _ingest_complete_set(session)
        snapshot = ingestion.ingest(value)
        before_hash = snapshot.content_hash
        bundle = BundlePublisher(repository).publish(
            symbol="600519.SH",
            market="CN_EQUITY",
            as_of_date=AS_OF,
            now=PIT_NOW,
        )
        ids = [row.snapshot_id for row in repository.list_bundle_items(bundle.id)]
        bindings = freeze_evidence_bindings(repository, ids) if bound else []
        materialized = BundleMaterializer(repository).materialize(
            bundle.id,
            evidence_bindings=bindings,
        )
        raw = build_uzi_raw(materialized.snapshots, "600519.SH")
        quant_raw = raw_quant_inputs(
            repository,
            symbol="600519.SH",
            snapshots=[
                repository.require_snapshot(row["snapshot_id"])
                for row in materialized.snapshots
            ],
            cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai"),
            evidence_bindings=bindings,
        )
        assert quant_input_hash(quant_raw) == quant_input_hash(raw)
        assert raw.get("fund_managers") == (managers if bound else None)
        assert snapshot.content_hash == before_hash


def test_quant_preserves_legacy_bonus_before_later_explicit_root_spill():
    from types import SimpleNamespace

    from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
        build_uzi_raw,
    )
    from research_system.market_data.bundles import native_transport_facts
    from research_system.market_data.contracts import cutoff_at_for_date
    from research_system.market_data.native_projection import project_native_result
    from research_system.market_data.uzi_adapter import UziSnapshotAdapter
    from tests.market_data.test_native_symbol_transport import _source
    from tests.market_data.test_pit_core import AS_OF

    _, source = _source({"fund_managers": [{"fund_code": "later"}]})
    value = UziSnapshotAdapter().adapt(
        source,
        symbol="600519.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=AS_OF,
    )
    common = {
        "as_of_date": AS_OF,
        "exchange_timezone": "Asia/Shanghai",
        "status": "available",
    }
    first = SimpleNamespace(
        **common,
        id="first",
        dimension="6_fund_holders",
        payload_json={"raw": {"fund_managers": [{"fund_code": "legacy-first"}]}},
        query_manifest_json={},
    )
    second = SimpleNamespace(
        **common,
        id="second",
        dimension="similar_stocks",
        payload_json=value.payload,
        query_manifest_json=value.query_manifest,
    )
    repository = SimpleNamespace(
        list_snapshot_evidence=lambda _id: value.evidence if _id == "second" else []
    )
    cutoff = cutoff_at_for_date(AS_OF, "Asia/Shanghai")
    rows = [
        {
            "symbol": "600519.SH",
            "snapshot_type": snapshot_type,
            "facts": {
                "_uzi_raw_result": project_native_result(snapshot),
                **native_transport_facts(
                    snapshot,
                    evidence=repository.list_snapshot_evidence(snapshot.id),
                    cutoff_at=cutoff,
                ),
            },
        }
        for snapshot, snapshot_type in [
            (first, "fund_holders"),
            (second, "similar_stocks"),
        ]
    ]
    report = build_uzi_raw(rows, "600519.SH")
    auxiliary = raw_quant_inputs(
        repository, symbol="600519.SH", snapshots=[first, second]
    )
    assert report["fund_managers"] == [{"fund_code": "legacy-first"}]
    assert quant_input_hash(report) == quant_input_hash(auxiliary)


@pytest.mark.parametrize(
    "explicit,recovery", [(True, False), (False, True), (False, False)]
)
def test_research_only_job_does_not_opt_in_to_unrelated_quant_acquisition(
    database,
    monkeypatch,
    explicit,
    recovery,
):
    from threading import Event

    from research_system.market_data import quant_auxiliary
    from research_system.market_data.collection import MarketDataCollectionCoordinator
    from research_system.market_data.dimension_contracts import (
        DEFAULT_DIMENSION_REGISTRY,
    )

    coordinator = MarketDataCollectionCoordinator(
        database,
        collector=lambda **kw: [],
        archive_collector=object(),
    )
    try:
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="user",
            symbols=["688041.SH"],
            start_date=DAY,
            end_date=DAY,
            dimensions=["3_macro", "13_policy"],
            include_research_archive=explicit,
            submit=False,
        )
        calls = []
        monkeypatch.setattr(
            quant_auxiliary,
            "collect_quant_auxiliary_inputs",
            lambda *args, **kw: calls.append(kw) or {"status": "available"},
        )
        result = quant_auxiliary.collect_job_quant_auxiliary_inputs(
            database,
            job_id=job["id"],
            registry=DEFAULT_DIMENSION_REGISTRY,
            deadline=100,
            cancellation=Event(),
            source=Source(),
            recovery=recovery,
        )
        if not (explicit or recovery):
            assert result["status"] == "not_requested"
            assert result["total"] == len(calls) == 0
            return
        assert result["status"] == "not_requested"
        assert result["reason_code"] == "quant_dependency_not_requested"
        assert result["total"] == result["completed"] == len(calls) == 0
    finally:
        coordinator.close()


def test_empty_auxiliary_job_scope_is_not_success(database):
    from threading import Event

    from research_system.market_data.dimension_contracts import (
        DEFAULT_DIMENSION_REGISTRY,
    )
    from research_system.market_data.models import MarketDataCollectionJob
    from research_system.market_data.quant_auxiliary import (
        collect_job_quant_auxiliary_inputs,
    )

    with database.session() as session:
        job = MarketDataCollectionJob(
            tenant_id="tenant",
            user_id="user",
            idempotency_key="no-tasks",
            start_date=DAY,
            end_date=DAY,
            requested_by="user",
            request_json={"include_research_archive": True},
        )
        session.add(job)
        session.flush()
        job_id = job.id
    source = Source()
    result = collect_job_quant_auxiliary_inputs(
        database,
        job_id=job_id,
        registry=DEFAULT_DIMENSION_REGISTRY,
        deadline=100,
        cancellation=Event(),
        source=source,
    )
    assert result["status"] == "not_applicable"
    assert result["reason_code"] == "no_requested_equity_scope"
    assert source.calls == []


@pytest.mark.parametrize(
    "name",
    [
        "quant_auxiliary.py",
        "bundles.py",
        "native_projection.py",
        "evidence_bindings.py",
    ],
)
def test_auxiliary_and_projection_sources_change_execution_identity(name, monkeypatch):
    from pathlib import Path

    from research_system.strategies.runtime_pins import deployed_prompt_hash

    reader = Path.read_bytes
    target = (
        Path(__file__).resolve().parents[2] / "research_system" / "market_data" / name
    )
    deployed_prompt_hash.cache_clear()
    baseline = deployed_prompt_hash()
    try:
        monkeypatch.setattr(
            Path,
            "read_bytes",
            lambda path: reader(path) + (b"\n# fixture" if path == target else b""),
        )
        deployed_prompt_hash.cache_clear()
        assert deployed_prompt_hash() != baseline
    finally:
        deployed_prompt_hash.cache_clear()


def test_manifest_binds_auxiliary_responses_and_never_absorbs_newer_ones(database):
    from types import SimpleNamespace

    from research_system.market_data.bundles import BundlePublisher
    from research_system.market_data.dimension_contracts import (
        DEFAULT_DIMENSION_REGISTRY,
    )
    from research_system.market_data.ingestion import SnapshotIngestionService
    from research_system.market_data.report_inputs import DatabaseReportInputGateway
    from tests.market_data.test_pit_core import AS_OF, _input
    from tests.market_data.test_pit_core import NOW as PIT_NOW

    raw = {"ticker": "600519.SH", "fund_managers": []}
    with database.session() as session:
        repository = MarketDataRepository(session)
        ingestion = SnapshotIngestionService(repository)
        for dimension in DEFAULT_DIMENSION_REGISTRY.dimension_ids:
            ingestion.ingest(_input(dimension))
        BundlePublisher(repository).publish(
            symbol="600519.SH",
            market="CN_EQUITY",
            as_of_date=AS_OF,
            now=PIT_NOW + timedelta(days=1),
        )
    first = collect_quant_auxiliary_inputs(
        database, raw=raw, as_of_date=AS_OF, source=Source(), now=PIT_NOW
    )
    gateway = DatabaseReportInputGateway(
        database,
        clock=lambda: PIT_NOW + timedelta(days=1),
        runtime_settings=SimpleNamespace(
            default_model="fixture", provider_name="fixture"
        ),
    )
    frozen = gateway.freeze_report_manifest(
        symbols=["600519.SH"],
        as_of_date=AS_OF,
        tenant_id="tenant",
        user_id="user",
        workflow_pins={"research_profile": "medium"},
        context={},
    )
    assert (
        frozen["workflow_pins"]["native_quant_auxiliary_version"]
        == "native-quant-aux-v1"
    )
    assert frozen["items"][0]["context"]["native_quant_auxiliary"] == first
    assert frozen["items"][0]["native_quant_auxiliary_recording"]["binding"] == first
    collect_quant_auxiliary_inputs(
        database,
        raw=raw,
        as_of_date=AS_OF,
        source=Source(empty=True),
        now=PIT_NOW + timedelta(minutes=1),
        force=True,
    )
    loaded = gateway.load_frozen_report_manifest(
        manifest_id=frozen["manifest_id"],
        manifest_hash=frozen["manifest_hash"],
        tenant_id="tenant",
        user_id="user",
    )
    assert (
        loaded["items"][0]["native_quant_auxiliary_recording"]
        == frozen["items"][0]["native_quant_auxiliary_recording"]
    )
    assert loaded["manifest_hash"] == frozen["manifest_hash"]

    # Build a separate legacy row using the original hash payload. Do not
    # update any frozen row in order to simulate an old database.
    from research_system.market_data.contracts import canonical_hash
    from research_system.market_data.models import (
        ReportDataManifest,
        ReportDataManifestItem,
    )

    legacy_pins = {
        key: value
        for key, value in frozen["workflow_pins"].items()
        if key != "native_quant_auxiliary_version"
    }
    legacy_item = {
        key: frozen["items"][0][key]
        for key in (
            "ordinal",
            "symbol",
            "market",
            "bundle_id",
            "bundle_manifest_hash",
            "weight",
        )
    }
    legacy_context = {
        key: value
        for key, value in frozen["items"][0]["context"].items()
        if key != "native_quant_auxiliary"
    }
    legacy_hash = canonical_hash(
        {
            "contract_version": frozen["contract_version"],
            "report_kind": frozen["report_kind"],
            "as_of_date": frozen["as_of_date"],
            "evaluation_at": frozen["evaluation_at"],
            "workflow_pins": legacy_pins,
            "items": [{**legacy_item, "context": legacy_context}],
        }
    )
    with database.session() as session:
        legacy = ReportDataManifest(
            tenant_id="tenant",
            user_id="user",
            report_kind=frozen["report_kind"],
            contract_version=frozen["contract_version"],
            as_of_date=AS_OF,
            evaluation_at=datetime.fromisoformat(frozen["evaluation_at"]),
            workflow_pins_json=legacy_pins,
            manifest_hash=legacy_hash,
            frozen_by="fixture",
            frozen_at=PIT_NOW + timedelta(days=1),
        )
        session.add(legacy)
        session.flush()
        legacy_id = legacy.id
        session.add(
            ReportDataManifestItem(
                manifest_id=legacy_id, **legacy_item, context_json=legacy_context
            )
        )
    old = gateway.load_frozen_report_manifest(
        manifest_id=legacy_id,
        manifest_hash=legacy_hash,
        tenant_id="tenant",
        user_id="user",
    )
    assert old["manifest_hash"] == legacy_hash
    assert "native_quant_auxiliary_version" not in old["workflow_pins"]
    assert "native_quant_auxiliary_recording" not in old["items"][0]
    assert "native_quant_auxiliary" not in old["items"][0]["context"]


def test_historical_receipt_can_reference_previously_observed_exact_responses(database):
    source = Source()
    raw = {
        "ticker": "688041.SH",
        "fund_managers": [{"fund_code": str(index)} for index in range(20)],
    }
    collect(database, source, raw)
    historical_raw = {
        **raw,
        "fund_managers": [
            {**row, "fund_name": "Recorded name"} for row in raw["fund_managers"]
        ],
    }
    binding = collect(
        database, source, historical_raw, now=NOW + timedelta(days=1), force=True
    )
    assert binding["status"] == "available"
    assert len(source.calls) == 20
    with database.session() as session:
        recording = load_quant_auxiliary_recording(
            MarketDataRepository(session),
            binding=binding,
            raw=historical_raw,
            cutoff_at=NOW,
        )
    validate_quant_auxiliary_recording(recording, raw=historical_raw)


def test_native_source_captures_actual_parameters_and_swallowed_failures(monkeypatch):
    from time import monotonic

    import pandas as pd

    from research_system.market_data.quant_auxiliary import (
        NativeQuantSource,
        _native_module,
    )

    module = _native_module()
    holders = import_module("fetch_fund_holders")

    class Akshare:
        def stock_fund_stock_holder(self, *, symbol):
            assert symbol == "688041"
            return pd.DataFrame([{"基金代码": "000001", "基金名称": "Recorded"}])

        def fund_portfolio_hold_em(self, *, symbol, date):
            assert symbol == "000001" and date == "2025"
            raise ConnectionError("offline fixture")

    provider = Akshare()
    monkeypatch.setattr(module, "ak", provider)
    monkeypatch.setattr(holders, "ak", provider)
    source = NativeQuantSource()
    lookup = source.fetch(
        "all_holding_funds",
        {"ticker_code": "688041", "max_funds": 80},
        deadline=monotonic() + 2,
    )
    assert lookup["rows"] == [{"fund_code": "000001", "fund_name": "Recorded"}]
    assert lookup["status"] == "available"
    failed = source.fetch(
        "top_holdings",
        {"fund_code": "000001", "top_n": 10, "holdings_year": "2025"},
        deadline=monotonic() + 2,
    )
    assert failed["status"] == "source_error"
    assert failed["errors"] == ["ConnectionError"]
    assert module.ak is provider and holders.ak is provider
