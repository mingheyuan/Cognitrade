"""Historical profile repair must execute bounded consumers, never live ones."""
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import (
    UziSourceSnapshotSkill,
)
from domain_packs.finance_ashare.tools import snapshot_collector as collector
from research_system.adapters.vendors.akshare_pit_context_adapter import (
    AkSharePitContextAdapter,
)
from research_system.market_data.contracts import canonical_hash, cutoff_at_for_date
from research_system.market_data.models import (
    MarketDataCollectionTask,
    MarketDataSnapshot,
)
from research_system.market_data.repository import MarketDataRepository
from tests.market_data.test_company_profile_rebuild import (
    collected,
    module,
    run,
)
from tests.market_data.test_company_profile_rebuild import (
    state as state,  # noqa: PLC0414
)
from tests.market_data.test_pit_core import AS_OF, NOW
from tests.test_uzi_fixed_basic_collector_bridge import (
    _fixed,
)
from tests.test_uzi_fixed_basic_collector_bridge import (
    offline as _bridge_fixture,
)

DAY = date(2026, 9, 2)
CUTOFF = cutoff_at_for_date(DAY, "Asia/Shanghai")


def bound(raw, symbol, day=DAY, *, dimension="0_basic"):
    return {**deepcopy(raw), "_pit_context": {
        "version": "fixed-profile-pit-input-v1", "safe": True, "symbol": symbol,
        "dimension": dimension, "requested_as_of_date": day.isoformat(),
        "cutoff_at": cutoff_at_for_date(day, "Asia/Shanghai").isoformat(),
        "snapshot_id": f"fixed-{dimension}", "snapshot_hash": "a" * 64,
        "native_result_hash": canonical_hash(raw),
        "industry_basis": "issuer_disclosure", "classification_system": None,
    }}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("no real source/model calls in historical profile tests")
    monkeypatch.setattr("socket.socket.connect", deny)


@pytest.fixture
def bridge_offline(tmp_path, monkeypatch):
    return _bridge_fixture.__wrapped__(tmp_path, monkeypatch)


def test_bound_historical_basic_reaches_scoped_consumers_without_market(bridge_offline, monkeypatch):
    kwargs, calls = bridge_offline
    observed = []
    original = collector._run_snapshot_skill
    def invoke(*args, **options):
        # Serial and concurrent dispatcher both pass date/cutoff positionally.
        observed.append((args[6:] if len(args) > 6 else (), deepcopy(options)))
        return original(*args, **options)
    monkeypatch.setattr(collector, "_run_snapshot_skill", invoke)
    fixed = {symbol: bound(raw, symbol) for symbol, raw in _fixed().items()}
    before = deepcopy(fixed)
    rows = collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=fixed,
                                       as_of_date=DAY, cutoff_at=CUTOFF)
    assert len(rows) == len(calls) == 2
    assert all(call["snapshot_type"] != "market" for call in calls)
    assert all(call["dependencies"]["0_basic"] == fixed["688041.SH"] for call in calls)
    assert all(dates == (DAY, CUTOFF) for dates, _ in observed)
    assert fixed == before


def test_historical_industry_metrics_consume_bound_identity_without_reclassification(monkeypatch):
    fixed = bound(_fixed()["688041.SH"], "688041.SH")
    seen = []
    def no_reclassification(*args, **kwargs):
        pytest.fail("fixed identity must not be replaced by a separate classification query")
    def metrics(self, *, industry_name, as_of_date, source_id):
        seen.append((industry_name, as_of_date))
        return {"data_date": as_of_date.isoformat(), "pe_weighted": 20.0}, []
    monkeypatch.setattr(AkSharePitContextAdapter, "_classification_history", no_reclassification)
    monkeypatch.setattr(AkSharePitContextAdapter, "_industry_metrics", metrics)
    skill = UziSourceSnapshotSkill()
    skill.snapshot_type = "industry"
    facts = skill._collect(query="历史依赖重建", symbol="688041.SH",
                          dependencies={"0_basic": fixed}, as_of_date=DAY)
    assert facts["industry"] == "集成电路"
    assert seen == [("集成电路", DAY), ("集成电路", DAY - timedelta(days=365))]
    assert facts["classification"]["basis"] == "issuer_disclosure"
    assert not facts["classification"].get("standard")
    assert facts["profile_input"]["snapshot_id"] == "fixed-0_basic"


def test_materials_to_futures_retains_actual_history_proof(bridge_offline, monkeypatch):
    kwargs, calls = bridge_offline
    base = collector._run_snapshot_skill
    def source(*args, **kw):
        row = base(*args, **kw)
        row["historical_reconstruction"] = {"safe": True, "requested_as_of_date": DAY.isoformat(),
            "cutoff_at": CUTOFF.isoformat(), "window_end": DAY.isoformat(),
            "proof_basis": "date_filtered_provider_series"}
        return row
    monkeypatch.setattr(collector, "_run_snapshot_skill", source)
    fixed = {symbol: bound(raw, symbol) for symbol, raw in _fixed().items()}
    rows = collector.collect_snapshots(**{**kwargs, "snapshot_types": ["materials", "futures"]},
        fixed_basic_by_symbol=fixed, as_of_date=DAY, cutoff_at=CUTOFF)
    material = next(row for row in rows if row["snapshot_type"] == "materials")
    future = next(row for row in calls if row["snapshot_type"] == "futures")
    delivered = future["dependencies"]["8_materials"]
    assert delivered["_pit_context"] == material["historical_reconstruction"]
    assert {key: value for key, value in delivered.items() if key != "_pit_context"} == material["facts"]["_uzi_raw_result"]
    assert "_pit_context" not in material["facts"]["_uzi_raw_result"]


def test_fixed_materials_wrong_day_rejected_before_source(bridge_offline):
    kwargs, calls = bridge_offline
    fixed = {symbol: bound(raw, symbol) for symbol, raw in _fixed().items()}
    materials = {"688041.SH": bound({"data": {"industry": "集成电路"}}, "688041.SH",
        day=DAY + timedelta(days=1), dimension="8_materials")}
    with pytest.raises(ValueError, match="fixed_pit_binding_invalid"):
        collector.collect_snapshots(**{**kwargs, "snapshot_types": ["futures"]}, fixed_basic_by_symbol=fixed,
            fixed_materials_by_symbol=materials, as_of_date=DAY, cutoff_at=CUTOFF)
    assert not calls


def test_fixed_policy_query_uses_bound_historical_identity_and_end_date(monkeypatch):
    from domain_packs.finance_ashare.skills.policy_snapshot.skill import (
        PolicySnapshotSkill,
    )
    fixed = bound(_fixed()["688041.SH"], "688041.SH")
    skill = PolicySnapshotSkill()
    calls = []
    monkeypatch.setattr(skill.adapter, "get_policy_snapshot", lambda **kw: calls.append(kw) or {
        "policies": [], "policy_query_status": "empty"})
    skill.run(query="当前错误行业", symbol="688041.SH", as_of_date=DAY,
              cutoff_at=CUTOFF, dependencies={"0_basic": fixed})
    assert calls == [{"theme": fixed["data"]["industry"], "constituents": [
        {"code": "688041", "name": fixed["data"]["name"]}], "as_of_date": DAY}]


def test_rebuild_dispatches_only_authorized_historical_sources_and_persists_bound_results(state, monkeypatch):
    monkeypatch.setattr(module(), "_today", lambda zone: AS_OF + timedelta(days=1))
    seen = []
    def history(options):
        seen.append(options)
        assert options["as_of_date"] == AS_OF
        assert options["cutoff_at"] == cutoff_at_for_date(AS_OF, "Asia/Shanghai")
        assert "market" not in options["snapshot_types"]
        rows = collected(options)
        for row in rows:
            row["retrieved_at"] = (NOW + timedelta(days=2)).isoformat()
            row["historical_reconstruction"] = {
                "safe": True, "requested_as_of_date": AS_OF.isoformat(),
                "cutoff_at": options["cutoff_at"].isoformat(),
                "window_start": AS_OF.isoformat(), "window_end": AS_OF.isoformat(),
                "proof_basis": "source_bounded_point_in_time_query", "strategy": "derived_from_pit",
            }
        return rows
    result = run(state, history)
    assert len(seen) == 1, result
    assert set(seen[0]["snapshot_types"]) == {"industry", "peers"}
    assert len(result["revisions"]) == 2, [(row.get("reason_code"), row.get("error_type")) for row in result["scopes"]]
    assert {row["dimension"] for row in result["revisions"]} == {"7_industry", "4_peers"}


@pytest.mark.parametrize("mutation", ["missing", "date", "symbol", "dimension", "hash", "naive", "cutoff_day"])
def test_bad_fixed_history_binding_stops_before_any_source(bridge_offline, mutation):
    kwargs, calls = bridge_offline
    fixed = {symbol: bound(raw, symbol) for symbol, raw in _fixed().items()}
    raw = fixed["688041.SH"]
    if mutation == "missing":
        raw.pop("_pit_context")
    elif mutation == "hash":
        raw["data"]["industry"] = "篡改后的行业"
    else:
        field, value = {
            "date": ("requested_as_of_date", "2026-09-03"),
            "symbol": ("symbol", "600519.SH"),
            "dimension": ("dimension", "8_materials"),
            "naive": ("cutoff_at", CUTOFF.replace(tzinfo=None).isoformat()),
            "cutoff_day": ("cutoff_at", (CUTOFF + timedelta(days=1)).isoformat()),
        }[mutation]
        raw["_pit_context"][field] = value
    cutoff = CUTOFF + timedelta(days=1) if mutation == "cutoff_day" else CUTOFF
    with pytest.raises(ValueError, match="fixed_pit_binding_invalid"):
        collector.collect_snapshots(**kwargs, fixed_basic_by_symbol=fixed,
                                    as_of_date=DAY, cutoff_at=cutoff)
    assert not calls


def test_historical_peers_self_identity_does_not_revert_to_provider_name(monkeypatch):
    from domain_packs.finance_ashare.skills.fundamental_snapshot.skill import (
        FundamentalSnapshotSkill,
    )
    fixed = bound(_fixed()["688041.SH"], "688041.SH")
    monkeypatch.setattr(FundamentalSnapshotSkill, "run", lambda *a, **kw: {
        "facts": {"info": {"股票简称": "旧公司名", "industry": "旧行业"}}})
    monkeypatch.setattr(AkSharePitContextAdapter, "_industry_metrics", lambda *a, **kw: ({}, []))
    skill = UziSourceSnapshotSkill()
    skill.snapshot_type = "peers"
    result = skill._collect(query="历史", symbol="688041.SH", as_of_date=DAY,
                            dependencies={"0_basic": fixed})
    assert result["comparison"]["industry"] == fixed["data"]["industry"]
    assert result["comparison"]["self_only"][0]["name"] == fixed["data"]["name"]
    assert result["comparison"]["peers"] == []  # No invented historical constituent set.


@pytest.mark.parametrize("industry", ["集成电路", ""])
def test_historical_lhb_consumes_native_industry_presence_but_never_live_statistics(monkeypatch, industry):
    from domain_packs.finance_ashare.skills.lhb_snapshot.skill import LHBSnapshotSkill
    raw = deepcopy(_fixed()["688041.SH"])
    raw["data"]["industry"] = industry
    fixed = bound(raw, "688041.SH")
    skill = LHBSnapshotSkill()
    calls = []
    monkeypatch.setattr(skill.adapter, "get_lhb_snapshot", lambda **kw: calls.append(kw) or {
        "records": [], "availability": "available", "query_completed": True})
    result = skill.run(query="历史", symbol="688041.SH", as_of_date=DAY,
                      cutoff_at=CUTOFF, dependencies={"0_basic": fixed})
    assert calls == [{"symbol": "688041.SH", "as_of_date": DAY}]
    context = result["facts"]["historical_sector_context"]
    assert context["industry_present"] is bool(industry)
    assert context["status"] == ("historical_unavailable" if industry else "not_applicable")
    assert "sector_lhb_top50" not in result["facts"]
    assert result["facts"]["profile_input"] == fixed["_pit_context"]
    assert any(row.get("request_executed") is False for row in result["source_trace"])


def test_historical_macro_rebuild_executes_native_archive_consumer_and_binds_actual_basic(state, tmp_path, monkeypatch):
    from tests.market_data.test_macro_derivation import GENERIC_TEXT, INDUSTRY, seed
    seed(SimpleNamespace(database=state["database"], root=tmp_path), basic=False,
         generic_text=GENERIC_TEXT.replace(INDUSTRY, "集成电路设计"))
    with state["database"].session() as session:
        task = MarketDataCollectionTask(job_id=state["job_id"], symbol="600519.SH", market="CN_EQUITY",
            exchange_timezone="Asia/Shanghai", dimension="3_macro", start_date=AS_OF, end_date=AS_OF,
            status="succeeded", attempt_count=1)
        session.add(task)
        session.flush()
        task_id = task.id
        originals = {row.id: row.content_hash for row in session.scalars(select(MarketDataSnapshot))}
    monkeypatch.setattr(module(), "_today", lambda zone: AS_OF + timedelta(days=1))
    result = run(state, lambda options: pytest.fail("macro must use native archives, not benchmark fetch"), task_ids=[task_id])
    assert len(result["revisions"]) == 1, result
    with state["database"].session() as session:
        repo = MarketDataRepository(session)
        row = repo.require_snapshot(result["revisions"][0]["snapshot_id"])
        derived = row.query_manifest_json["macro_derivation"]
        assert derived["status"] == "completed" and len(derived["calls"]) == 6
        assert derived["isolation_events"] == []
        assert derived["recording"]["industry"] == "集成电路设计"
        assert row.query_manifest_json["company_profile_dependency_receipt"]["input_basic_snapshot_id"] == state["basic_id"]
        assert {key: repo.require_snapshot(key).content_hash for key in originals} == originals
