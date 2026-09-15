"""Daily-close UZI conditions and next-open fills with a locked entry plan."""

from __future__ import annotations

import math
from collections.abc import Callable
from copy import deepcopy
from datetime import date
from typing import Any

from research_system.strategies.backtesting.adapters import ExchangeExecutionAdapter
from research_system.strategies.backtesting.instruments import TradableInstrument
from research_system.strategies.backtesting.portfolio_ledger import (
    _result,
    _trade_payload,
)
from research_system.strategies.errors import StrategyDomainError
from research_system.strategies.trading_calendar import trading_dates

ENGINE_VERSION = "report-conditions-daily-close-next-open-v1"


def _price(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _latest(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    available = [row for row in rows if str(row["effective_from"]) <= day]
    return max(
        available,
        key=lambda row: (str(row["effective_from"]), int(row.get("revision") or 0)),
        default=None,
    )


def _entry_valid(signal: dict[str, Any] | None, day: str) -> bool:
    return bool(
        signal
        and signal.get("execution_plan", {}).get("entry_permitted")
        and signal.get("execution_plan", {}).get("price_basis_verified") is True
        and str(signal["effective_from"])
        <= day
        <= str(signal.get("expires_after") or "")
    )


def run_report_conditions(
    *,
    instruments: dict[str, TradableInstrument],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    stock_signals: dict[str, list[dict[str, Any]]],
    adapter: ExchangeExecutionAdapter,
    initial_cash: float,
    start_date: date,
    end_date: date,
    control: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    bars = {
        symbol: {str(row["date"]): row for row in rows}
        for symbol, rows in bars_by_symbol.items()
    }
    cash = float(initial_cash)
    holdings: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    consumed: set[str] = set()
    last_close: dict[str, float] = {}
    trades: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    fees_total = turnover = 0.0

    def record_decision(
        *,
        symbol: str,
        day: str,
        signal: dict[str, Any] | None,
        status: str,
        reason: str,
        phase: str = "close",
        condition: str = "entry",
        observed_price: float | None = None,
        **details: Any,
    ) -> None:
        """Keep one compact condition audit, never another copy of raw report data."""
        plan = (signal or {}).get("execution_plan") or {}
        decisions.append(
            {
                "symbol": symbol,
                "date": day,
                "phase": phase,
                "signal_id": (signal or {}).get("id"),
                "effective_from": (signal or {}).get("effective_from"),
                "expires_after": (signal or {}).get("expires_after"),
                "condition": condition,
                "status": status,
                "reason": reason,
                "observed_price": observed_price,
                "prices": dict(plan.get("prices") or {}),
                "source_artifact_id": plan.get("source_artifact_id"),
                "source_content_hash": plan.get("source_content_hash"),
                "source_field": plan.get("source_field"),
                **details,
            }
        )

    for day in trading_dates(start_date, end_date):
        control()
        day_text = day.isoformat()
        current_signals = {
            symbol: _latest(stock_signals.get(symbol, []), day_text)
            for symbol in instruments
        }
        closed_today: set[str] = set()
        # Stable ordering and sells first make finite-cash allocation reproducible.
        for symbol in sorted(
            pending, key=lambda key: (pending[key]["side"] != "sell", key)
        ):
            order = pending[symbol]
            signal = order["signal"]
            plan = signal["execution_plan"]
            side = order["side"]
            if side == "buy" and (
                not _entry_valid(signal, day_text)
                or (current_signals[symbol] or {}).get("id") != signal["id"]
            ):
                record_decision(
                    symbol=symbol,
                    day=day_text,
                    signal=signal,
                    phase="open",
                    status="cancelled",
                    reason="entry_plan_expired_or_replaced",
                    permission_valid=_entry_valid(signal, day_text),
                    active_signal_id=(current_signals[symbol] or {}).get("id"),
                )
                del pending[symbol]
                continue
            opening = _price(bars.get(symbol, {}).get(day_text, {}).get("open"))
            if opening is None:
                record_decision(
                    symbol=symbol,
                    day=day_text,
                    signal=signal,
                    phase="open",
                    condition=order["condition"],
                    status="waiting",
                    reason="real_open_unavailable",
                )
                continue
            if side == "buy":
                if not plan["prices"]["stop"] < opening < plan["prices"]["target"]:
                    record_decision(
                        symbol=symbol,
                        day=day_text,
                        signal=signal,
                        phase="open",
                        status="cancelled",
                        reason="opening_gap_outside_stop_target",
                        observed_price=opening,
                    )
                    del pending[symbol]
                    continue
                quantity = adapter.affordable_quantity(
                    min(cash, order["budget"]), opening
                )
                if quantity <= 0:
                    record_decision(
                        symbol=symbol,
                        day=day_text,
                        signal=signal,
                        phase="open",
                        status="cancelled",
                        reason="insufficient_first_tranche_budget",
                        observed_price=opening,
                        budget=order["budget"],
                        cash=cash,
                    )
                    del pending[symbol]
                    continue
                gross = quantity * opening
                fees = adapter.fees(side, gross)
                cash -= gross + fees.total
                holdings[symbol] = {
                    "quantity": quantity,
                    "last_buy_date": day,
                    "signal": deepcopy(signal),
                    "entry_budget": order["budget"],
                }
                last_close[symbol] = opening
                # Only a completed first tranche consumes the plan. A cancelled
                # order may trigger again at a later valid close, never at this open.
                consumed.add(signal["id"])
            else:
                holding = holdings.get(symbol)
                if not holding or not adapter.can_sell(
                    last_buy_date=holding["last_buy_date"], trading_date=day
                ):
                    record_decision(
                        symbol=symbol,
                        day=day_text,
                        signal=signal,
                        phase="open",
                        condition=order["condition"],
                        status="waiting",
                        reason="position_unavailable"
                        if not holding
                        else "settlement_restriction",
                        observed_price=opening,
                    )
                    continue
                quantity = holding["quantity"]
                gross = quantity * opening
                fees = adapter.fees(side, gross)
                cash += gross - fees.total
                del holdings[symbol]
                closed_today.add(symbol)
            fees_total += fees.total
            turnover += gross
            trade = _trade_payload(
                instrument=instruments[symbol],
                day=day,
                side=side,
                price=opening,
                quantity=quantity,
                gross=gross,
                fees=fees.payload(),
                cash_after=cash,
                order_date=order["triggered_at"],
                event={
                    "signal_id": signal["id"],
                    "source_report_set_id": signal["source_report_set_id"],
                    "technical_reason": order["condition"],
                },
            )
            trade.update(
                {
                    "condition": order["condition"],
                    "condition_observed_price": order["observed_price"],
                    "source_field": plan["source_field"],
                    "source_artifact_id": plan["source_artifact_id"],
                    "source_content_hash": plan["source_content_hash"],
                    "locked_plan_signal_id": signal["id"],
                    "entry_tranche_index": 1,
                    "entry_tranche_limit": 1,
                }
            )
            trades.append(trade)
            record_decision(
                symbol=symbol,
                day=day_text,
                signal=signal,
                phase="open",
                condition=order["condition"],
                status="filled",
                reason="next_real_open_fill",
                observed_price=opening,
                quantity=quantity,
                triggered_at=order["triggered_at"],
            )
            del pending[symbol]

        for symbol in instruments:
            closing = _price(bars.get(symbol, {}).get(day_text, {}).get("close"))
            if closing is not None:
                last_close[symbol] = closing
            elif symbol in holdings:
                decisions.append(
                    {
                        "symbol": symbol,
                        "date": day_text,
                        "status": "valuation_carried",
                        "reason": "close_unavailable_condition_not_evaluated",
                    }
                )
        if any(symbol not in last_close for symbol in holdings):
            raise StrategyDomainError(
                "holding_valuation_unavailable", "已有仓位缺少可审计价格，不能按零估值"
            )
        equity = cash + sum(
            row["quantity"] * last_close[symbol] for symbol, row in holdings.items()
        )
        curve.append(
            {
                "date": day_text,
                "equity": round(equity, 6),
                "cash": round(cash, 6),
                "market_value": round(equity - cash, 6),
            }
        )

        for symbol in sorted(instruments):
            closing = _price(bars.get(symbol, {}).get(day_text, {}).get("close"))
            holding = holdings.get(symbol)
            signal = holding["signal"] if holding else current_signals[symbol]
            audit = {
                "symbol": symbol,
                "day": day_text,
                "signal": signal,
                "observed_price": closing,
                "condition": "exit" if holding else "entry",
            }
            if symbol in pending:
                record_decision(
                    **{
                        **audit,
                        "signal": pending[symbol]["signal"],
                        "condition": pending[symbol]["condition"],
                    },
                    status="not_evaluated",
                    reason="order_already_pending",
                )
                continue
            if symbol in closed_today:
                record_decision(
                    **audit, status="not_evaluated", reason="same_day_reentry_disabled"
                )
                continue
            if closing is None:
                record_decision(
                    **audit,
                    status="not_evaluated",
                    reason="close_unavailable_condition_not_evaluated",
                )
                continue
            if not signal:
                record_decision(
                    **audit, status="not_evaluated", reason="no_effective_report_plan"
                )
                continue
            plan = signal.get("execution_plan") or {}
            prices = plan.get("prices") or {}
            if holding:
                condition = (
                    "stop"
                    if closing <= prices["stop"]
                    else "target"
                    if closing >= prices["target"]
                    else None
                )
                if condition:
                    pending[symbol] = {
                        "side": "sell",
                        "signal": deepcopy(signal),
                        "condition": condition,
                        "triggered_at": day_text,
                        "observed_price": closing,
                    }
                    record_decision(
                        **{**audit, "condition": condition},
                        status="queued",
                        reason=f"{condition}_condition_met",
                    )
                else:
                    record_decision(
                        **audit,
                        status="not_triggered",
                        reason="exit_conditions_not_met",
                    )
            elif (
                not plan.get("executable")
                or plan.get("price_basis_verified") is not True
            ):
                record_decision(
                    **audit,
                    status="blocked",
                    reason="report_plan_not_executable",
                    errors=list(plan.get("errors") or [])
                    or ["report_reference_price_unverified"],
                    suggested_collection=plan.get("suggested_collection"),
                )
            elif not plan.get("entry_permitted"):
                record_decision(
                    **audit,
                    status="not_permitted",
                    reason="native_verdict_no_new_entry",
                    native_verdict=plan.get("native_verdict"),
                )
            elif signal["id"] in consumed:
                record_decision(
                    **audit,
                    status="not_permitted",
                    reason="plan_first_tranche_consumed",
                )
            elif not _entry_valid(signal, day_text):
                record_decision(
                    **audit, status="not_permitted", reason="entry_permission_expired"
                )
            else:
                if closing <= prices["stop"]:
                    record_decision(
                        **audit, status="not_triggered", reason="close_at_or_below_stop"
                    )
                elif closing > prices["entry"]:
                    record_decision(
                        **audit,
                        status="not_triggered",
                        reason="entry_price_not_reached",
                    )
                else:
                    weight = (
                        min(
                            plan["original_disclosed_weight_pct"],
                            plan["max_single_stock_pct"],
                        )
                        / 100.0
                    )
                    pending[symbol] = {
                        "side": "buy",
                        "signal": deepcopy(signal),
                        "condition": "entry",
                        "triggered_at": day_text,
                        "observed_price": closing,
                        "budget": equity * weight * plan["entry_fraction"],
                    }
                    record_decision(
                        **audit,
                        status="queued",
                        reason="entry_condition_met",
                        budget=pending[symbol]["budget"],
                    )
    result = _result(
        initial_cash=initial_cash,
        curve=curve,
        trades=trades,
        total_fees=fees_total,
        turnover=turnover,
        positions={symbol: row["quantity"] for symbol, row in holdings.items()},
        adapter={"model_id": ENGINE_VERSION, "board_lot": adapter.board_lot},
    )
    result.update(
        {
            "execution_decisions": decisions,
            "consumed_plan_ids": sorted(consumed),
            "pending_orders": [
                {
                    "symbol": symbol,
                    "side": row["side"],
                    "condition": row["condition"],
                    "signal_id": row["signal"]["id"],
                    "triggered_at": row["triggered_at"],
                }
                for symbol, row in sorted(pending.items())
            ],
            "locked_position_plans": {
                symbol: row["signal"]["id"] for symbol, row in holdings.items()
            },
            "execution_limitations": [
                "daily_close_conditions_next_open_market_fill",
                "no_intraday_path_simulation",
            ],
        }
    )
    return result
