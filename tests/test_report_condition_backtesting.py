from __future__ import annotations

import time
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_system.strategies.backtesting.adapters import ExchangeExecutionAdapter
from research_system.strategies.backtesting.instruments import InstrumentResolver
from research_system.strategies.backtesting.market_data_gateway import MarketSeries
from research_system.strategies.backtesting.report_condition_ledger import (
    run_report_conditions,
)
from research_system.strategies.backtesting.result_store import json_content_hash
from research_system.strategies.backtesting.service import (
    StrategyBacktestService,
    _native_plan_readiness,
)
from research_system.strategies.contracts import (
    BacktestMode,
    StrategyDocumentV1,
    StrategyDocumentV2,
    parse_strategy_document,
    report_conditions_template_document,
    strategy_content_hash,
)
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict
from research_system.strategies.models import DailyReportNode, DailyReportSet
from research_system.strategies.report_conditions import report_condition_calculation
from research_system.strategies.service import OwnerScope, StrategyService
from research_system.strategies.signals import SignalEngineService

SYMBOL = "688012.SH"


def _node(verdict="可以蹲（偏弱）", **changes):
    values = {
        "id": "node",
        "subject_code": SYMBOL,
        "status": "completed",
        "confidence": 0.1,
        "disclosed_weight_pct": 10.0,
        "data_gaps_json": [],
        "artifact_id": "artifact",
        "content_hash": "a" * 64,
        "source_json": {
            "report_allowed": True,
            "critical_count": 0,
            "reference_price_evidence": {
                "status": "verified",
                "subject_code": SYMBOL,
                "price": 10.0,
                "trade_date": "2026-08-03",
                "as_of_date": "2026-08-03",
                "provider": "offline_fixture",
                "snapshot_id": "basic-snapshot",
                "snapshot_content_hash": "b" * 64,
                "bundle_id": "stock-bundle",
                "bundle_manifest_hash": "c" * 64,
                "basis": "raw_unadjusted",
                "currency": "CNY",
                "price_unit": "per_share",
                "source_field": "0_basic.data.price",
            },
            "synthesis": {
                "ticker": SYMBOL,
                "verdict_label": verdict,
                "overall_score": 65.0,
                "dashboard": {
                    "battle_plan": {
                        "entry": "¥9.20",
                        "stop": "¥8.50",
                        "target": "¥12.50",
                        "position": "分批建仓 · 勿满仓",
                    }
                },
            },
        },
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _report():
    return SimpleNamespace(
        id="report", revision=1, as_of_date=date(2026, 8, 3), data_gaps_json=[]
    )


def _signal(verdict="可以蹲（偏弱）"):
    calculation = report_condition_calculation(
        _node(verdict), _report(), StrategyDocumentV2()
    )
    return {
        **calculation,
        "id": "signal",
        "kind": "holding_stock",
        "source_report_set_id": "report",
        "effective_from": "2026-08-04",
        "expires_after": "2026-08-06",
        "revision": 1,
    }


def _bars():
    return [
        {
            "date": day,
            "open": opening,
            "close": closing,
            "high": max(opening or closing, closing),
            "low": min(opening or closing, closing),
        }
        for day, opening, closing in [
            ("2026-08-03", 9.0, 9.0),
            ("2026-08-04", 9.0, 9.0),
            ("2026-08-05", 9.1, 9.0),
            ("2026-08-06", 9.1, 9.0),
            ("2026-08-07", 10.0, 12.6),
            ("2026-08-10", 12.7, 12.6),
        ]
    ]


def _run(signals=None, bars=None):
    document = StrategyDocumentV2()
    return run_report_conditions(
        instruments={SYMBOL: InstrumentResolver.resolve_stock("688012")},
        bars_by_symbol={SYMBOL: bars or _bars()},
        stock_signals={SYMBOL: [_signal()] if signals is None else signals},
        adapter=ExchangeExecutionAdapter.stock(document),
        initial_cash=1_000_000,
        start_date=date(2026, 8, 3),
        end_date=date(2026, 8, 10),
    )


def test_v1_canonical_hash_unchanged_and_v2_explicit():
    # Captured from the V1 contract before report-condition support.
    assert (
        strategy_content_hash(StrategyDocumentV1())
        == "56fb84360afbc86c5fc05046f833aa4812a8b00acc2f821bff2d95f51b19e731"
    )
    payload = report_conditions_template_document().model_dump(mode="json")
    assert isinstance(parse_strategy_document(payload), StrategyDocumentV2)
    assert "technical_rule" not in payload["execution"]
    assert "signal_thresholds" not in payload
    assert "fund_composite" not in payload
    with pytest.raises(ValueError):
        StrategyDocumentV1.model_validate(payload)


def test_verdict_and_native_values_frozen_without_rounding_reclassification():
    negative = report_condition_calculation(
        _node("观望偏多"), _report(), StrategyDocumentV2()
    )
    assert not negative["execution_plan"][
        "entry_permitted"
    ]  # displayed score rounds to 65.0
    for verdict in ("值得重仓", "可以蹲一蹲", "可以蹲（偏弱） · 2 派看多 / 3 派看空"):
        node = _node(verdict)
        result = report_condition_calculation(node, _report(), StrategyDocumentV2())
        assert result["execution_plan"][
            "entry_permitted"
        ]  # confidence 0.1 is not another gate
        node.source_json["synthesis"]["dashboard"]["battle_plan"]["stop"] = "¥1"
        assert result["execution_plan"]["native_battle_plan"]["stop"] == "¥8.50"


def test_unknown_range_or_missing_lineage_is_not_an_executable_plan():
    node = _node(content_hash=None)
    node.source_json["synthesis"]["dashboard"]["battle_plan"]["entry"] = (
        "¥9.00-9.20 分批"
    )
    result = report_condition_calculation(node, _report(), StrategyDocumentV2())
    assert not result["execution_plan"]["entry_permitted"]
    assert "battle_plan_prices_not_executable" in result["reasons"]
    assert "report_artifact_lineage_missing" in result["reasons"]


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"status": "unavailable"}, "report_reference_price_unverified"),
        ({"basis": "qfq"}, "report_reference_price_basis_unverified"),
        ({"currency": "HKD"}, "report_reference_price_basis_unverified"),
        ({"subject_code": "600519.SH"}, "report_reference_price_symbol_mismatch"),
        ({"trade_date": "2026-08-04"}, "report_reference_price_date_mismatch"),
        ({"as_of_date": "2026-08-02"}, "report_reference_price_date_mismatch"),
        ({"trade_date": ""}, "report_reference_price_date_unverified"),
        ({"snapshot_content_hash": ""}, "report_reference_price_lineage_missing"),
        ({"price": 11.0}, "battle_plan_reference_price_mismatch"),
    ],
)
def test_price_basis_requires_the_fixed_raw_basic_anchor(changes, expected):
    node = _node()
    node.source_json["reference_price_evidence"].update(changes)
    plan = report_condition_calculation(node, _report(), StrategyDocumentV2())[
        "execution_plan"
    ]
    assert not plan["entry_permitted"]
    assert plan["adjustment"] is None
    assert expected in plan["errors"]
    assert plan["suggested_collection"] == {
        "symbols": [SYMBOL],
        "market": "CN_EQUITY",
        "dimensions": ["0_basic"],
        "start_date": "2026-08-03",
        "end_date": "2026-08-03",
        "mode": "force_all",
    }


def test_missing_price_proof_is_not_defaulted_raw_and_qfq_technical_data_is_not_the_anchor():
    node = _node()
    node.source_json.pop("reference_price_evidence")
    plan = report_condition_calculation(node, _report(), StrategyDocumentV2())[
        "execution_plan"
    ]
    assert not plan["executable"]
    assert not plan["price_basis_verified"]
    node = _node()
    node.source_json["technical"] = {"adjustment": "qfq", "close": 7.0}
    plan = report_condition_calculation(node, _report(), StrategyDocumentV2())[
        "execution_plan"
    ]
    assert plan["entry_permitted"]
    assert plan["prices"] == {"entry": 9.2, "stop": 8.5, "target": 12.5}


def test_stored_pre_proof_v2_plan_cannot_trade_by_claiming_raw():
    signal = _signal()
    signal["execution_plan"].pop("price_basis_verified")
    result = _run([signal])
    assert not result["trades"]
    assert any(
        row["reason"] == "report_plan_not_executable"
        and row["errors"] == ["report_reference_price_unverified"]
        for row in result["execution_decisions"]
    )


def test_missing_native_report_stays_blocked_without_inventing_price_collection():
    node = _node(status="failed", artifact_id=None, content_hash=None)
    node.source_json.pop("synthesis")
    node.source_json.update(
        report_allowed=False,
        reference_price_evidence={
            "status": "not_evaluated",
            "reason": "missing_native_report",
        },
    )
    plan = report_condition_calculation(node, _report(), StrategyDocumentV2())[
        "execution_plan"
    ]
    assert not plan["executable"] and not plan["entry_permitted"]
    assert "holding_report_unavailable" in plan["errors"]
    assert plan["suggested_collection"] is None


def test_real_native_results_and_context_link_use_canonical_artifact_lineage():
    node = _node(artifact_id="holding-context-link")
    synthesis = node.source_json.pop("synthesis")
    node.source_json.update(
        results=[{"symbol": SYMBOL, "synthesis": synthesis, "report_allowed": True}],
        canonical_artifact_id="native-analysis-artifact",
        canonical_content_hash=node.content_hash,
        context_link_artifact_id=node.artifact_id,
    )
    plan = report_condition_calculation(node, _report(), StrategyDocumentV2())[
        "execution_plan"
    ]
    assert plan["entry_permitted"]
    assert plan["source_field"] == "results[0].synthesis.dashboard.battle_plan"
    assert plan["source_artifact_id"] == "native-analysis-artifact"
    assert plan["source_content_hash"] == node.content_hash
    assert plan["context_link_artifact_id"] == "holding-context-link"
    node.source_json["canonical_content_hash"] = "b" * 64
    assert (
        "report_artifact_lineage_mismatch"
        in report_condition_calculation(node, _report(), StrategyDocumentV2())[
            "reasons"
        ]
    )
    node.source_json.pop("canonical_artifact_id")
    assert (
        "report_artifact_lineage_missing"
        in report_condition_calculation(node, _report(), StrategyDocumentV2())[
            "reasons"
        ]
    )


def test_first_half_budget_once_and_locked_exit_after_expiration_and_new_negative_report():
    replacement = deepcopy(_signal("回避"))
    replacement.update(
        id="new-negative", effective_from="2026-08-06", expires_after="2026-08-10"
    )
    replacement["execution_plan"]["prices"] = {
        "entry": 14.0,
        "stop": 12.0,
        "target": 20.0,
    }
    result = _run([_signal(), replacement])
    assert [(row["date"], row["side"]) for row in result["trades"]] == [
        ("2026-08-05", "buy"),
        ("2026-08-10", "sell"),
    ]
    buy, sell = result["trades"]
    assert (
        buy["gross_value"] < 50_000
    )  # 10% original fund weight × 50%; no renormalization
    assert buy["quantity"] == 5400
    assert buy["price"] == 9.1  # next real opening, not entry threshold 9.2
    assert sell["locked_plan_signal_id"] == "signal"
    assert sell["condition"] == "target"
    assert not result["ending_positions"]


def test_opening_gap_cancels_entry_and_missing_open_does_not_fill_at_close():
    bars = _bars()
    bars[2]["open"] = 8.4
    result = _run(bars=bars)
    # A cancelled instruction is not a consumed first tranche: the same
    # still-valid plan can trigger at the next close and fill the next open.
    assert [(row["date"], row["side"]) for row in result["trades"]] == [
        ("2026-08-06", "buy"),
        ("2026-08-10", "sell"),
    ]
    assert result["trades"][0]["order_date"] == "2026-08-05"
    assert result["consumed_plan_ids"] == ["signal"]
    assert any(
        row["reason"] == "opening_gap_outside_stop_target"
        for row in result["execution_decisions"]
    )
    bars = _bars()
    bars[2]["open"] = None
    result = _run(bars=bars)
    assert result["trades"][0]["date"] == "2026-08-06"
    assert any(
        row["reason"] == "real_open_unavailable"
        for row in result["execution_decisions"]
    )


def test_nonpositive_new_report_cancels_unfilled_entry_and_never_uses_ma():
    replacement = deepcopy(_signal("谨慎"))
    replacement.update(
        id="new", effective_from="2026-08-05", expires_after="2026-08-07"
    )
    result = _run([_signal(), replacement])
    assert not result["trades"]
    assert any(
        row["reason"] == "entry_plan_expired_or_replaced"
        for row in result["execution_decisions"]
    )


def test_expiry_includes_three_sessions_and_prevents_fourth_session_fill():
    bars = _bars()
    bars[1]["close"] = 10.0  # no entry on first valid day
    bars[2]["close"] = 10.0
    result = _run(bars=bars)
    assert not result[
        "trades"
    ]  # day-three close queues, day-four opening has no permission
    cancellations = [
        row
        for row in result["execution_decisions"]
        if row["reason"] == "entry_plan_expired_or_replaced"
    ]
    assert cancellations[0]["date"] == "2026-08-07"
    assert not result["consumed_plan_ids"]


def test_insufficient_budget_does_not_consume_plan_before_later_affordable_open():
    signal = _signal()
    # Raw weight × 50% leaves 1,000 CNY; 10.1 cannot buy a board lot plus fees.
    signal["execution_plan"]["original_disclosed_weight_pct"] = 0.2
    bars = _bars()
    bars[2]["open"] = 10.1
    result = _run([signal], bars)
    buy = result["trades"][0]
    assert (buy["date"], buy["side"], buy["quantity"]) == ("2026-08-06", "buy", 100)
    assert result["consumed_plan_ids"] == ["signal"]
    assert any(
        row["reason"] == "insufficient_first_tranche_budget"
        for row in result["execution_decisions"]
    )


def test_consumed_plan_cannot_reenter_even_if_its_permission_has_not_expired():
    signal = _signal()
    # Longer validity isolates the consumption guard from the independent
    # three-session contract, covered by the expiry test above.
    signal["expires_after"] = "2026-08-10"
    bars = _bars()
    bars[2]["close"] = 8.0
    bars[3].update(open=8.1, close=9.0)
    bars[4].update(open=9.0, close=9.0)
    bars[5].update(open=9.0, close=9.0)
    result = _run([signal], bars)
    assert [row["side"] for row in result["trades"]] == ["buy", "sell"]
    assert any(
        row["reason"] == "plan_first_tranche_consumed"
        for row in result["execution_decisions"]
    )


@pytest.mark.parametrize(
    "change, reason",
    [
        ("no_report", "no_effective_report_plan"),
        ("negative", "native_verdict_no_new_entry"),
        ("invalid", "report_plan_not_executable"),
        ("missing_close", "close_unavailable_condition_not_evaluated"),
        ("above_entry", "entry_price_not_reached"),
        ("below_stop", "close_at_or_below_stop"),
    ],
)
def test_every_unfilled_close_has_a_condition_reason(change, reason):
    bars = _bars()
    signal = _signal("谨慎" if change == "negative" else "可以蹲（偏弱）")
    if change == "invalid":
        signal["execution_plan"].update(
            executable=False,
            entry_permitted=False,
            errors=["battle_plan_prices_not_executable"],
        )
    if change in {"missing_close", "above_entry", "below_stop"}:
        bars[1]["close"] = {
            "missing_close": None,
            "above_entry": 10.0,
            "below_stop": 8.5,
        }[change]
    result = _run([] if change == "no_report" else [signal], bars)
    rows = [
        row
        for row in result["execution_decisions"]
        if row.get("phase") == "close" and row["date"] == "2026-08-04"
    ]
    assert len(rows) == 1
    assert rows[0]["reason"] == reason
    if change != "no_report":
        assert (
            rows[0]["source_content_hash"]
            == signal["execution_plan"]["source_content_hash"]
        )
    if change == "invalid":
        assert rows[0]["errors"] == ["battle_plan_prices_not_executable"]


def test_condition_audit_includes_queued_filled_waiting_and_held_nontrigger():
    bars = _bars()
    bars[2]["open"] = None
    result = _run(bars=bars)
    decisions = result["execution_decisions"]
    assert {row["reason"] for row in decisions} >= {
        "entry_condition_met",
        "real_open_unavailable",
        "order_already_pending",
        "next_real_open_fill",
        "exit_conditions_not_met",
        "target_condition_met",
    }
    assert len([row for row in decisions if row.get("phase") == "close"]) == 6
    assert (
        sum(
            row["status"] == "queued" and row["condition"] == "entry"
            for row in decisions
        )
        == 1
    )


def test_stop_stays_active_after_expiry_and_does_not_reenter_same_consumed_plan():
    bars = _bars()
    bars[3]["close"] = 8.0
    bars[4].update(open=8.1, close=9.0)
    result = _run(bars=bars)
    assert [
        (row["date"], row["side"], row["condition"]) for row in result["trades"]
    ] == [
        ("2026-08-05", "buy", "entry"),
        ("2026-08-07", "sell", "stop"),
    ]


def test_missing_daily_close_carries_valuation_and_never_synthesizes_exit():
    bars = _bars()
    bars = [row for row in bars if row["date"] != "2026-08-06"]
    result = _run(bars=bars)
    by_day = {row["date"]: row for row in result["equity_curve"]}
    assert by_day["2026-08-06"]["equity"] == by_day["2026-08-05"]["equity"]
    assert any(
        row["status"] == "valuation_carried" for row in result["execution_decisions"]
    )


class FrozenGateway:
    def __init__(self):
        self.symbols = []

    def get_series(self, *args, **kwargs):
        raise AssertionError("V2 cannot use the live/network-enabled gateway")

    def get_frozen_series(self, instrument, **kwargs):
        self.symbols.append(instrument.code)
        bars = _bars()
        return MarketSeries(
            instrument,
            tuple(bars),
            json_content_hash(bars),
            {
                "provider": "offline_test_snapshot",
                "evidence_mode": "fixture",
                "adjustment": "raw",
                "currency": "CNY",
                "price_unit": "per_share",
            },
        )


class NoTechnicalSignals:
    engine_version = "must_not_execute"

    def events(self, **kwargs):
        raise AssertionError("V2 cannot run MA or any technical signal core")


@pytest.mark.parametrize("scenario", ["blocked", "unverified_price", "watch"])
def test_preflight_distinguishes_ten_blocked_plans_from_legal_watch_cash_replay(
    tmp_path, scenario
):
    database = StrategyDatabase("sqlite:///:memory:")
    strategies = StrategyService(database, workspace_root=tmp_path)
    owner = OwnerScope("tenant", "user", "actor")
    strategy = strategies.create_strategy(owner, name="input readiness")
    version = strategies.publish(owner, strategy["id"])
    assignment = strategies.create_assignment(
        owner,
        strategy_id=strategy["id"],
        fund_code="020464",
        fund_name="fixture",
        mode=BacktestMode.HOLDINGS_PORTFOLIO,
    )
    symbols = [f"6000{index:02}.SH" for index in range(10)]
    with database.session() as session:
        report = DailyReportSet(
            tenant_id=owner.tenant_id,
            user_id=owner.user_id,
            assignment_id=assignment["id"],
            fund_code="020464",
            as_of_date=date(2026, 8, 3),
            effective_date=date(2026, 8, 4),
            pin_set_hash="b" * 64,
            revision=1,
            status="final_partial",
            max_top_n=10,
            count_coverage=1.0,
            coverage=1.0,
            source_hash="c" * 64,
            finalized_at=datetime.now(UTC),
        )
        session.add(report)
        session.flush()
        report_id = report.id
        for rank, symbol in enumerate(symbols, 1):
            source = _node(
                "观望偏多" if scenario == "watch" else "可以蹲一蹲"
            ).source_json
            source["synthesis"]["ticker"] = symbol
            source["reference_price_evidence"]["subject_code"] = symbol
            if scenario == "blocked":
                source.update(report_allowed=False, critical_count=1)
            elif scenario == "unverified_price":
                source.pop("reference_price_evidence")
            session.add(
                DailyReportNode(
                    tenant_id=owner.tenant_id,
                    user_id=owner.user_id,
                    report_set_id=report.id,
                    node_type="holding_uzi",
                    subject_code=symbol,
                    status="completed",
                    rank=rank,
                    confidence=0.8,
                    disclosed_weight_pct=5.0,
                    effective_weight_pct=5.0,
                    canonical_key=f"readiness:{symbol}",
                    artifact_id=f"artifact:{symbol}",
                    content_hash="a" * 64,
                    source_json=source,
                    data_gaps_json=[],
                )
            )
    result = SignalEngineService(database).recompute(
        owner,
        assignment_id=assignment["id"],
        report_set_id=report_id,
    )
    assert len(result["holding_signals"]) == 10
    assert all(row["expires_after"] is not None for row in result["holding_signals"])
    gateway = FrozenGateway()
    backtests = StrategyBacktestService(
        database,
        market_data_gateway=gateway,
        object_store_root=tmp_path / "objects",
        signal_core=NoTechnicalSignals(),
        recover=False,
    )
    request = {
        "strategy_version_id": version["id"],
        "fund_code": "020464",
        "mode": BacktestMode.HOLDINGS_PORTFOLIO,
        "start_date": date(2026, 8, 3),
        "end_date": date(2026, 8, 6),
    }
    try:
        preflight = backtests.preflight(owner, **request)
        assert preflight["signal_count"] == 10
        assert preflight["signal_coverage"] == 0.75
        assert preflight["market_data_symbol_count"] == 10
        assert not preflight["market_data_missing"]
        assert set(gateway.symbols) == {symbol.split(".")[0] for symbol in symbols}
        run_request = {
            **request,
            "mode": request["mode"].value,
            "start_date": "2026-08-03",
            "end_date": "2026-08-06",
            "initial_cash": 1_000_000,
            "benchmark": {"hs300": False},
        }
        if scenario != "watch":
            assert not preflight["can_run"] and not preflight["ready"]
            assert preflight["executable_plan_count"] == 0
            assert preflight["executable_plan_coverage"] == 0
            assert preflight["blocked_plan_count"] == 10
            assert len(preflight["report_plan_blockers"]) == 10
            assert all(row["reasons"] for row in preflight["report_plan_blockers"])
            if scenario == "unverified_price":
                assert all(
                    row["suggested_collection"]["dimensions"] == ["0_basic"]
                    for row in preflight["report_plan_blockers"]
                )
            with pytest.raises(StrategyConflict) as blocked:
                backtests.create(owner, **request, initial_cash=1_000_000)
            assert blocked.value.code == "backtest_report_plans_blocked"
            assert len(blocked.value.details["report_plan_blockers"]) == 10
            # A job queued by an older build must not bypass the same guard.
            with pytest.raises(StrategyConflict) as queued:
                backtests._run(owner, run_request)
            assert queued.value.code == "backtest_report_plans_blocked"
        else:
            assert preflight["can_run"]
            assert preflight["executable_plan_count"] == 10
            assert preflight["executable_plan_coverage"] == 0.75
            assert not preflight["report_plan_blockers"]
            outcome, _, _ = backtests._run(owner, run_request)
            assert not outcome["trades"]
            assert outcome["report_plan_readiness"]["executable_plan_count"] == 10
            assert any(
                row["reason"] == "native_verdict_no_new_entry"
                for row in outcome["execution_decisions"]
            )
    finally:
        backtests.close()


def test_executable_coverage_does_not_count_a_blocked_plan_or_superseded_permission():
    old = _signal()
    blocked = deepcopy(old)
    blocked.update(id="blocked", state="BLOCKED", revision=2)
    # Even contradictory stored executable=True flags cannot override BLOCKED.
    readiness = _native_plan_readiness([old, blocked], [date(2026, 8, 4)])
    assert readiness["executable_plan_count"] == 1
    assert readiness["executable_plan_coverage"] == 0
    assert readiness["report_plan_blockers"][0]["reasons"] == ["report_signal_blocked"]


def test_service_freezes_market_manifest_and_runs_without_fund_gate_or_failed_stock_removal(
    tmp_path: Path,
):
    database = StrategyDatabase("sqlite:///:memory:")
    service = StrategyService(database, workspace_root=tmp_path)
    owner = OwnerScope("tenant", "user", "actor")
    strategy = service.create_strategy(owner, name="native replay")
    assert strategy["draft"]["document"]["metadata"]["schema_version"] == "2"
    version = service.publish(owner, strategy["id"])
    assert version["schema_version"] == "2"
    assert version["signal_model"] == "report_conditions_v1"
    assignment = service.create_assignment(
        owner,
        strategy_id=strategy["id"],
        fund_code="020464",
        fund_name="测试基金",
        mode=BacktestMode.HOLDINGS_PORTFOLIO,
    )
    with database.session() as session:
        report = DailyReportSet(
            tenant_id=owner.tenant_id,
            user_id=owner.user_id,
            assignment_id=assignment["id"],
            fund_code="020464",
            as_of_date=date(2026, 8, 3),
            effective_date=date(2026, 8, 4),
            pin_set_hash="b" * 64,
            revision=1,
            status="final_partial",
            max_top_n=10,
            count_coverage=0.1,
            coverage=0.1,
            source_hash="c" * 64,
            finalized_at=datetime.now(UTC),
        )
        session.add(report)
        session.flush()
        report_id = report.id
        for symbol, state in ((SYMBOL, "completed"), ("688200.SH", "failed")):
            source = _node().source_json
            source["synthesis"]["ticker"] = symbol
            session.add(
                DailyReportNode(
                    tenant_id=owner.tenant_id,
                    user_id=owner.user_id,
                    report_set_id=report_id,
                    node_type="holding_uzi",
                    subject_code=symbol,
                    status=state,
                    rank=1 if state == "completed" else 2,
                    confidence=0.1,
                    disclosed_weight_pct=10.0,
                    effective_weight_pct=99.0,
                    canonical_key=f"report:{symbol}",
                    artifact_id=f"artifact:{symbol}",
                    content_hash="a" * 64,
                    source_json=source,
                    data_gaps_json=[],
                )
            )
    signals = SignalEngineService(database).recompute(
        owner, assignment_id=assignment["id"], report_set_id=report_id
    )
    assert signals["fund_signal"] is None
    assert len(signals["holding_signals"]) == 2
    assert {row["expires_after"] for row in signals["holding_signals"]} == {
        date(2026, 8, 6)
    }
    gateway = FrozenGateway()
    backtests = StrategyBacktestService(
        database,
        market_data_gateway=gateway,
        object_store_root=tmp_path / "objects",
        signal_core=NoTechnicalSignals(),
        recover=False,
    )
    try:
        preflight = backtests.preflight(
            owner,
            strategy_version_id=version["id"],
            fund_code="020464",
            mode=BacktestMode.HOLDINGS_PORTFOLIO,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 10),
        )
        assert preflight["can_run"] and preflight["ready"]
        assert preflight["missing_report_dates"]
        with pytest.raises(StrategyConflict, match="日报不完整"):
            backtests.create(
                owner, strategy_version_id=version["id"], fund_code="020464",
                mode=BacktestMode.HOLDINGS_PORTFOLIO, start_date=date(2026, 8, 3),
                end_date=date(2026, 8, 10), initial_cash=1_000_000,
                report_policy="require_complete",
            )
        row = backtests.create(
            owner,
            strategy_version_id=version["id"],
            fund_code="020464",
            mode=BacktestMode.HOLDINGS_PORTFOLIO,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 10),
            initial_cash=1_000_000,
            benchmark={"hs300": False},
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = backtests.get(owner, row["id"])
            if result["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert result["status"] == "completed", result
        outcome = result["result"]
        assert sorted(set(gateway.symbols)) == ["688012", "688200"]
        assert outcome["metrics"]["trade_count"] == 2
        assert Path(outcome["market_data_manifest"]["uri"]).is_file()
        assert outcome["trades"][0]["source_artifact_id"] == f"artifact:{SYMBOL}"
        assert all(row["signal_lineage"] for row in outcome["trades"])
        manifest_hash = outcome["market_data_manifest"]["content_hash"]

        def missing_market(*args, **kwargs):
            raise StrategyConflict(
                "market_history_missing",
                "数据中心缺少行情，请显式补采",
                details={
                    "suggested_collection": {"symbol": SYMBOL, "dimension": "2_kline"}
                },
            )

        gateway.get_frozen_series = missing_market
        replay = backtests.create(
            owner,
            strategy_version_id=version["id"],
            fund_code="020464",
            mode=BacktestMode.HOLDINGS_PORTFOLIO,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 10),
            initial_cash=1_000_000,
            benchmark={"hs300": False},
            market_data_manifest_hash=manifest_hash,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            replay = backtests.get(owner, replay["id"])
            if replay["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert replay["status"] == "completed", replay
        assert replay["result_content_hash"] == result["result_content_hash"]
        assert replay["input_snapshot_hash"] == result["input_snapshot_hash"]
        with pytest.raises(StrategyConflict) as wrong_owner:
            backtests._read_market_manifest(
                manifest_hash,
                tenant_id="other",
                user_id=owner.user_id,
                start_date=date(2026, 8, 3),
                end_date=date(2026, 8, 10),
            )
        assert wrong_owner.value.code == "market_manifest_not_found"
        with pytest.raises(StrategyConflict) as missing:
            backtests.create(
                owner,
                strategy_version_id=version["id"],
                fund_code="020464",
                mode=BacktestMode.HOLDINGS_PORTFOLIO,
                start_date=date(2026, 8, 3),
                end_date=date(2026, 8, 10),
                initial_cash=999_999,
                benchmark={"hs300": False},
            )
        assert missing.value.code == "backtest_market_data_incomplete"
        assert (
            missing.value.details["market_data_missing"][0]["suggested_collection"][
                "dimension"
            ]
            == "2_kline"
        )
    finally:
        backtests.close()
        service.close()
