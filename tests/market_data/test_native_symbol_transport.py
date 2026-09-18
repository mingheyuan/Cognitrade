"""Collector → immutable database → Bundle → native root parity, offline."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
from domain_packs.finance_ashare.tools.snapshot_collector import (
    _project_uzi_symbol_snapshot,
)
from research_system.market_data.bundles import (
    BundleIntegrityError,
    BundleMaterializer,
    BundlePublisher,
    native_transport_facts,
)
from research_system.market_data.contracts import canonical_hash, cutoff_at_for_date
from research_system.market_data.historical import reconstruct_historical_snapshot
from research_system.market_data.native_projection import V2_PROJECTION_VERSION
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import AS_OF, NOW, _ingest_complete_set


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *args, **kwargs: pytest.fail("transport parity must be offline"))
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: pytest.fail("transport parity must be offline"))


@pytest.fixture
def database(tmp_path):
    db = StrategyDatabase(f"sqlite:///{tmp_path / 'transport.sqlite'}")
    db.create_schema()
    yield db
    db.close()


def _source(extra):
    original = {"ticker": "600519", "market": "CN", "code": "600519", **extra,
                "dimensions": {"0_basic": {"source": "offline", "data": {
                    "code": "600519", "name": "贵州茅台", "industry": "白酒", "price": 100,
                    "market_cap_raw": 1_000_000, "pe_ttm": 20, "pb": 3,
                }}}}
    projected = _project_uzi_symbol_snapshot(
        query="600519", requested_symbol="600519.SH", snapshot_type="market", symbol_result=deepcopy(original),
    )
    projected.update(retrieved_at="2026-08-20T02:30:00+00:00", snapshot_date="2026-08-20")
    return original, projected


@pytest.mark.parametrize("extra", [
    {},
    {"full": None, "fund_managers": None, "similar_stocks": []},
    {"full": "600519.SH", "fund_managers": [], "similar_stocks": None},
    {"fetched_at": "2026-08-20T02:29:00+00:00", "fund_managers": [{"fund_code": "000001"}], "similar_stocks": []},
])
@pytest.mark.parametrize("projection_version", ["uzi-dim-result-v3", "uzi-dim-result-v4", "uzi-dim-result-v5", "uzi-dim-result-v6", "uzi-dim-result-v7", "uzi-dim-result-v8", "uzi-dim-result-v9", "uzi-dim-result-v10"])
def test_native_root_presence_survives_ingestion_and_bundle(database, extra, projection_version):
    original, source = _source(extra)
    value = UziSnapshotAdapter().adapt(source, symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=AS_OF)
    assert value.payload["_uzi_root_fields"] == source["facts"]["_uzi_root_fields"]
    assert value.payload["_uzi_top_level_fields"] == source["facts"]["_uzi_top_level_fields"]
    with database.session() as session:
        repo, ingestion = _ingest_complete_set(session)
        snapshot = ingestion.ingest(value)
        before_hash = snapshot.content_hash
        bundle = BundlePublisher(repo).publish(symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF, now=NOW)
        rows = BundleMaterializer(repo).materialize(bundle.id, projection_version=projection_version).snapshots
        assert rows[0]["snapshot_id"] == snapshot.id
        raw = build_uzi_raw(rows, "600519.SH")
        native_data = raw["dimensions"]["0_basic"]["data"]
        if projection_version in {"uzi-dim-result-v4", "uzi-dim-result-v5", "uzi-dim-result-v6", "uzi-dim-result-v7", "uzi-dim-result-v8", "uzi-dim-result-v9", "uzi-dim-result-v10"}:
            assert native_data == original["dimensions"]["0_basic"]["data"]
        else:
            assert "market_overview" in native_data
        for key in ("ticker", "market", "code", "full", "fetched_at", "fund_managers", "similar_stocks"):
            if projection_version in {"uzi-dim-result-v6", "uzi-dim-result-v7", "uzi-dim-result-v8", "uzi-dim-result-v9", "uzi-dim-result-v10"} and key in {"code", "full"}:
                assert key not in raw
                continue
            assert (key in raw) == (key in original), key
            if key in original:
                assert raw[key] == original[key], key
        assert snapshot.content_hash == before_hash
        old_rows = BundleMaterializer(repo).materialize(bundle.id, projection_version=V2_PROJECTION_VERSION).snapshots
        assert "_uzi_root_fields" not in old_rows[0]["facts"]
        assert "_uzi_top_level_fields" not in old_rows[0]["facts"]


@pytest.mark.parametrize("kind,field", [("fund_holders", "fund_managers"), ("similar_stocks", "similar_stocks")])
def test_skill_bonus_error_survives_database_without_becoming_verified_empty(database, kind, field):
    from research_system.market_data.native_projection import project_native_result

    failure = {"data": {}, "source": "recorded-provider", "error": "recorded source unavailable", "_timeout": True, "fallback": True}
    native = {"ticker": "600519.SH", "market": "A", "fetched_at": "2026-08-20T10:30:00", "dimensions": {},
              field: [], "_uzi_bonus_results": {field: failure},
              "_uzi_execution": {"lane": "skill_main_process", "input_contract": "native-skill-main-envelope-v2"}}
    source = _project_uzi_symbol_snapshot(query="test", requested_symbol="600519.SH", snapshot_type=kind, symbol_result=native)
    source.update(retrieved_at="2026-08-20T02:30:00+00:00", snapshot_date="2026-08-20")
    value = UziSnapshotAdapter().adapt(source, symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=AS_OF)
    assert value.payload["_uzi_raw_result"] == failure
    assert value.status.value == "data_gap"
    with database.session() as session:
        repo, ingestion = _ingest_complete_set(session)
        snapshot = ingestion.ingest(value)
        before_hash = snapshot.content_hash
        evidence = repo.list_snapshot_evidence(snapshot.id)
        cutoff = cutoff_at_for_date(AS_OF, "Asia/Shanghai")
        projected = project_native_result(snapshot, version="uzi-dim-result-v6")
        transport = native_transport_facts(snapshot, evidence=evidence, cutoff_at=cutoff, projection_version="uzi-dim-result-v6")
        raw = build_uzi_raw([{"symbol": "600519.SH", "snapshot_type": kind,
                              "facts": {**transport, "_uzi_raw_result": projected}}], "600519.SH")
        assert raw[field] == []
        assert snapshot.status == "data_gap"
        assert projected["error"] == failure["error"] and projected["_timeout"] is True
        assert snapshot.content_hash == before_hash
        assert "6_fund_holders" not in raw["dimensions"]


def test_historical_reconstruction_does_not_make_current_bonus_point_in_time_safe(database):
    _, source = _source({"full": "600519.SH", "fund_managers": [{"fund_code": "current-only"}], "similar_stocks": []})
    source["snapshot_type"] = "policy"
    source["retrieved_at"] = "2026-09-08T02:30:00+00:00"
    source["facts"]["_uzi_raw_result"] = {"source": "offline", "data": {
        "policies": [{"title": "白酒监管政策", "date": "2026-08-19", "url": "https://www.gov.cn/test.htm"}],
        "query_window": {"start": "2026-02-21", "end": "2026-08-20"},
    }}
    reconstructed = reconstruct_historical_snapshot(source, dimension="13_policy", as_of_date=AS_OF)
    assert reconstructed is not None
    assert reconstructed["facts"]["_uzi_top_level_fields"]["fund_managers"]["value"][0]["fund_code"] == "current-only"
    value = UziSnapshotAdapter().adapt(reconstructed, symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=AS_OF)
    assert value.payload["_uzi_top_level_fields"] == source["facts"]["_uzi_top_level_fields"]
    assert value.query_manifest["native_symbol_transport"]["bonus_status"] == "not_fixed_historical"
    with database.session() as session:
        repo, ingestion = _ingest_complete_set(session)
        snapshot = ingestion.ingest(value)
        bundle = BundlePublisher(repo).publish(symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF, now=NOW)
        rows = BundleMaterializer(repo).materialize(bundle.id).snapshots
        selected = next(row for row in rows if row["dimension"] == "13_policy")
        assert selected["snapshot_id"] == snapshot.id
        assert "_uzi_top_level_fields" not in selected["facts"]
        raw = build_uzi_raw(rows, "600519.SH")
        assert "fund_managers" not in raw
        assert "current-only" not in str(raw)


def test_old_snapshot_without_transport_keeps_legacy_materialization(database):
    with database.session() as session:
        repo, _ = _ingest_complete_set(session)
        bundle = BundlePublisher(repo).publish(symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF, now=NOW)
        before = bundle.manifest_hash
        first = BundleMaterializer(repo).materialize(bundle.id).snapshots
        second = BundleMaterializer(repo).materialize(bundle.id).snapshots
        assert canonical_hash(first) == canonical_hash(second)
        assert all("_uzi_top_level_fields" not in row["facts"] for row in first)
        assert bundle.manifest_hash == before


def test_transport_requires_bound_content_proof_not_a_boolean_or_date_marker():
    _, source = _source({"fund_managers": []})
    value = UziSnapshotAdapter().adapt(source, symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=AS_OF)
    snapshot = SimpleNamespace(payload_json=value.payload, query_manifest_json=value.query_manifest)
    cutoff = cutoff_at_for_date(AS_OF, "Asia/Shanghai")
    assert "_uzi_top_level_fields" not in native_transport_facts(snapshot, evidence=[], cutoff_at=cutoff)
    facts = native_transport_facts(snapshot, evidence=value.evidence, cutoff_at=cutoff)
    assert facts["_uzi_top_level_fields"]["fund_managers"] == {"present": True, "value": []}
    from research_system.market_data.contracts import canonical_json
    transport = {key: value.payload[key] for key in ("_uzi_root_fields", "_uzi_top_level_fields")}
    old_inline_proof = [row.model_copy(update={"inline_content": canonical_json(transport), "metadata": {"transport_version": "native-symbol-transport-v1"}})
                        if row.source_kind == "native_symbol_transport" else row for row in value.evidence]
    assert native_transport_facts(snapshot, evidence=old_inline_proof, cutoff_at=cutoff) == facts
    # The independent actual observation proof remains valid on a later
    # immutable research revision; no fake historical ingested_at is needed.
    snapshot.observed_at = datetime(2026, 9, 8, tzinfo=timezone.utc)
    assert native_transport_facts(snapshot, evidence=value.evidence, cutoff_at=cutoff) == facts
    future_proofs = [row.model_copy(update={"observed_at": datetime(2026, 9, 8, tzinfo=timezone.utc)}) for row in value.evidence]
    assert "_uzi_top_level_fields" not in native_transport_facts(snapshot, evidence=future_proofs, cutoff_at=cutoff)
    snapshot.payload_json = deepcopy(value.payload)
    snapshot.payload_json["_uzi_top_level_fields"]["fund_managers"]["value"] = [{"invented": True}]
    with pytest.raises(BundleIntegrityError, match="transport hash mismatch"):
        native_transport_facts(snapshot, evidence=value.evidence, cutoff_at=cutoff)


@pytest.mark.parametrize("change", [
    {"is_manual": True}, {"immutable_proof": False}, {"grade": "C"},
    {"content_hash": "f" * 64}, {"metadata": {"transport_version": "native-symbol-transport-v1"}},
])
def test_hash_only_transport_still_requires_qualified_bound_proof(change):
    _, source = _source({"fund_managers": []})
    value = UziSnapshotAdapter().adapt(source, symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=AS_OF)
    snapshot = SimpleNamespace(payload_json=value.payload, query_manifest_json=value.query_manifest)
    evidence = [row.model_copy(update=change) if row.source_kind == "native_symbol_transport" else row for row in value.evidence]
    facts = native_transport_facts(snapshot, evidence=evidence, cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai"))
    assert "_uzi_top_level_fields" not in facts


def test_full_751_fund_spill_does_not_overflow_inline_evidence_limit(database):
    # Native 9/7 actually returned 751 records with these fields, including
    # NAV arrays. Synthetic fixture values retain that shape and full count.
    managers = [{"name": "测试基金经理", "fund_code": f"{index:06d}",
                 "fund_name": "测试数字经济混合A", "avatar": "", "position_pct": 0.09,
                 "rank_in_fund": 0, "holding_quarters": 1, "position_trend": "持有",
                 "return_5y": 268.3, "annualized_5y": 48.9, "max_drawdown": -41.4,
                 "sharpe": 1.06, "peer_rank_pct": 50,
                 "nav_history": [1.0, 1.006, 0.844, 0.833, 0.986, 0.978, 1.019, 1.267,
                                 1.423, 1.299, 1.589, 2.167, 2.557, 2.713, 5.174, 3.683],
                 "fund_url": f"https://fund.eastmoney.com/{index:06d}.html", "_row_type": "full"}
                for index in range(751)]
    from research_system.market_data.contracts import canonical_json
    assert len(canonical_json(managers).encode()) > 262_144
    original, source = _source({"fund_managers": managers, "similar_stocks": []})
    value = UziSnapshotAdapter().adapt(source, symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=AS_OF)
    proof = next(row for row in value.evidence if row.source_kind == "native_symbol_transport")
    assert proof.inline_content is None
    with database.session() as session:
        repo, ingestion = _ingest_complete_set(session)
        snapshot = ingestion.ingest(value)
        bundle = BundlePublisher(repo).publish(symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF, now=NOW)
        materialized = BundleMaterializer(repo).materialize(bundle.id)
        assert materialized.snapshots[0]["snapshot_id"] == snapshot.id
        raw = build_uzi_raw(materialized.snapshots, "600519.SH")
        assert len(raw["fund_managers"]) == 751
        assert raw["fund_managers"] == original["fund_managers"]
