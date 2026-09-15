from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from statistics import fmean, pstdev
from typing import Any

from research_system.strategies.backtesting.adapters import (
    ExchangeExecutionAdapter,
    OTCExecutionAdapter,
)
from research_system.strategies.backtesting.instruments import TradableInstrument
from research_system.strategies.errors import StrategyDomainError


def run_exchange_single_asset(
    *,
    instrument: TradableInstrument,
    bars: list[dict[str, Any]],
    events: list[dict[str, Any]],
    adapter: ExchangeExecutionAdapter,
    initial_cash: float,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_event[str(event["date"])].append(dict(event))
    cash = float(initial_cash)
    quantity = 0
    last_buy_date: date | None = None
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    total_fees = 0.0
    turnover = 0.0
    for bar in bars:
        day = date.fromisoformat(str(bar["date"]))
        if day < start_date or day > end_date:
            continue
        price = float(bar["open"])
        for event in pending:
            side = str(event["side"])
            if side == "buy" and quantity == 0:
                bought = adapter.affordable_quantity(cash, price)
                if bought <= 0:
                    continue
                gross = bought * price
                fees = adapter.fees("buy", gross)
                cash -= gross + fees.total
                quantity += bought
                last_buy_date = day
            elif side == "sell" and quantity > 0 and adapter.can_sell(
                last_buy_date=last_buy_date,
                trading_date=day,
            ):
                bought = quantity
                gross = bought * price
                fees = adapter.fees("sell", gross)
                cash += gross - fees.total
                quantity = 0
            else:
                continue
            total_fees += fees.total
            turnover += gross
            trades.append(
                _trade_payload(
                    instrument=instrument,
                    day=day,
                    side=side,
                    price=price,
                    quantity=float(bought),
                    gross=gross,
                    fees=fees.payload(),
                    cash_after=cash,
                    event=event,
                )
            )
        pending = [dict(event) for event in by_event.get(day.isoformat(), [])]
        equity = cash + quantity * float(bar["close"])
        curve.append(
            {
                "date": day.isoformat(),
                "equity": round(equity, 6),
                "cash": round(cash, 6),
                "market_value": round(quantity * float(bar["close"]), 6),
            }
        )
    return _result(
        initial_cash=initial_cash,
        curve=curve,
        trades=trades,
        total_fees=total_fees,
        turnover=turnover,
        positions={instrument.code: quantity},
        adapter={"model_id": adapter.model_id, "board_lot": adapter.board_lot},
    )


def run_otc_single_asset(
    *,
    instrument: TradableInstrument,
    bars: list[dict[str, Any]],
    events: list[dict[str, Any]],
    fund_signals: list[dict[str, Any]] | None = None,
    adapter: OTCExecutionAdapter,
    initial_cash: float,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_event[str(event["date"])].append(dict(event))
    cash = float(initial_cash)
    shares = 0.0
    pending: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []
    entry_plan_event: dict[str, Any] | None = None
    exit_plan_event: dict[str, Any] | None = None
    entry_orders_submitted = 0
    total_entry_orders_submitted = 0
    last_entry_order_index: int | None = None
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    total_fees = 0.0
    turnover = 0.0
    open_status_assumptions: list[dict[str, str]] = []
    staged_rules_enabled = (
        adapter.max_position_pct != 100.0
        or adapter.entry_tranche_pct != 100.0
        or adapter.max_entry_tranches != 1
        or adapter.minimum_entry_interval_days != 0
    )
    period_bars = [
        row for row in bars if start_date <= date.fromisoformat(str(row["date"])) <= end_date
    ]
    for index, bar in enumerate(period_bars):
        day = date.fromisoformat(str(bar["date"]))
        nav = float(bar["close"])
        due = [row for row in pending if int(row["confirm_index"]) <= index]
        pending = [row for row in pending if int(row["confirm_index"]) > index]
        for order in due:
            if order["side"] == "buy":
                amount = float(order["amount"])
                fees = adapter.subscription_fee(amount)
                net = max(0.0, amount - fees.total)
                acquired = round(net / nav, 4)
                shares += acquired
                gross = amount
                quantity = acquired
            else:
                quantity = float(order["quantity"])
                gross = quantity * nav
                fees = adapter.redemption_fee(gross)
                cash += gross - fees.total
                shares = max(0.0, shares - quantity)
                if shares <= 1e-12:
                    entry_orders_submitted = 0
                    last_entry_order_index = None
            total_fees += fees.total
            turnover += gross
            trades.append(
                _trade_payload(
                    instrument=instrument,
                    day=day,
                    side=str(order["side"]),
                    price=nav,
                    quantity=quantity,
                    gross=gross,
                    fees=fees.payload(),
                    cash_after=cash,
                    event=order["event"],
                    order_date=str(order["order_date"]),
                )
            )
        for event in next_actions:
            side = str(event["side"])
            if side == "buy" and not any(row["side"] == "sell" for row in pending):
                entry_plan_event = dict(event)
                exit_plan_event = None
            elif side == "sell":
                entry_plan_event = None
                exit_plan_event = dict(event)

        if (
            exit_plan_event is not None
            and shares > 1e-12
            and not any(row["side"] in {"buy", "sell"} for row in pending)
        ):
            event = exit_plan_event
            if adapter.require_open_status and bar.get("redemption_open") is None:
                if adapter.missing_open_status_policy == "block":
                    raise StrategyDomainError(
                        "otc_redemption_status_unavailable",
                        f"场外基金 {day.isoformat()} 缺少历史赎回开放状态，不能可靠执行赎回信号",
                        status_code=409,
                        details={"date": day.isoformat(), "side": "sell"},
                    )
                open_status_assumptions.append(
                    {
                        "date": day.isoformat(),
                        "side": "sell",
                        "assumption": "assumed_open",
                        "reason": "historical_redemption_status_unavailable",
                    }
                )
            if bar.get("redemption_open") is not False:
                pending.append(
                    {
                        "side": "sell",
                        "quantity": shares,
                        "confirm_index": index + adapter.redemption_confirmation_days,
                        "order_date": day.isoformat(),
                        "event": event,
                    }
                )
                exit_plan_event = None

        interval_ready = (
            last_entry_order_index is None
            or index - last_entry_order_index >= adapter.minimum_entry_interval_days
        )
        current_fund_gate = (
            _latest_signal(fund_signals, day.isoformat())
            if fund_signals is not None
            else None
        )
        fund_gate_allows_entry = (
            fund_signals is None
            or (
                current_fund_gate is not None
                and str(current_fund_gate.get("state") or "") == "ALLOW_BUY"
            )
        )
        can_stage_entry = (
            entry_plan_event is not None
            and exit_plan_event is None
            and not any(row["side"] in {"buy", "sell"} for row in pending)
            and entry_orders_submitted < adapter.max_entry_tranches
            and interval_ready
            and fund_gate_allows_entry
        )
        if can_stage_entry:
            equity_before_order = cash + shares * nav
            maximum_position_value = equity_before_order * adapter.max_position_pct / 100.0
            current_position_value = shares * nav
            remaining_capacity = max(0.0, maximum_position_value - current_position_value)
            tranche_budget = equity_before_order * adapter.entry_tranche_pct / 100.0
            amount = min(cash, remaining_capacity, tranche_budget)
            if amount > 1e-8:
                if adapter.require_open_status and bar.get("purchase_open") is None:
                    if adapter.missing_open_status_policy == "block":
                        raise StrategyDomainError(
                            "otc_purchase_status_unavailable",
                            f"场外基金 {day.isoformat()} 缺少历史申购开放状态，不能可靠执行申购信号",
                            status_code=409,
                            details={"date": day.isoformat(), "side": "buy"},
                        )
                    open_status_assumptions.append(
                        {
                            "date": day.isoformat(),
                            "side": "buy",
                            "assumption": "assumed_open",
                            "reason": "historical_purchase_status_unavailable",
                        }
                    )
                if bar.get("purchase_open") is not False:
                    entry_orders_submitted += 1
                    total_entry_orders_submitted += 1
                    last_entry_order_index = index
                    staged_event = dict(entry_plan_event)
                    if current_fund_gate is not None:
                        signal_ids = list(
                            dict.fromkeys(
                                str(value)
                                for value in [
                                    current_fund_gate.get("id"),
                                    staged_event.get("signal_id"),
                                    *(staged_event.get("signal_ids") or []),
                                ]
                                if value
                            )
                        )
                        staged_event.update(
                            {
                                "signal_id": signal_ids[0] if signal_ids else None,
                                "signal_ids": signal_ids,
                                "source_report_set_id": current_fund_gate.get(
                                    "source_report_set_id"
                                ),
                            }
                        )
                    if staged_rules_enabled:
                        staged_event.update(
                            {
                                "entry_tranche_index": entry_orders_submitted,
                                "entry_tranche_limit": adapter.max_entry_tranches,
                            }
                        )
                    cash -= amount
                    pending.append(
                        {
                            "side": "buy",
                            "amount": amount,
                            "confirm_index": index + adapter.subscription_confirmation_days,
                            "order_date": day.isoformat(),
                            "event": staged_event,
                        }
                    )
                    if entry_orders_submitted >= adapter.max_entry_tranches:
                        entry_plan_event = None
        next_actions = [dict(event) for event in by_event.get(day.isoformat(), [])]
        pending_cash = sum(float(row.get("amount") or 0.0) for row in pending if row["side"] == "buy")
        equity = cash + pending_cash + shares * nav
        committed_position_value = pending_cash + shares * nav
        curve_row = {
            "date": day.isoformat(),
            "equity": round(equity, 6),
            "cash": round(cash, 6),
            "market_value": round(shares * nav, 6),
            "pending_confirmation_value": round(pending_cash, 6),
        }
        if staged_rules_enabled:
            curve_row.update(
                {
                    "committed_position_value": round(committed_position_value, 6),
                    "position_weight_pct": round(
                        committed_position_value / equity * 100.0 if equity > 0 else 0.0,
                        6,
                    ),
                }
            )
        curve.append(curve_row)
    return _result(
        initial_cash=initial_cash,
        curve=curve,
        trades=trades,
        total_fees=total_fees,
        turnover=turnover,
        positions={instrument.code: round(shares, 4)},
        adapter={
            "model_id": adapter.model_id,
            "degraded_estimate": adapter.degraded_estimate,
            "require_open_status": adapter.require_open_status,
            "missing_open_status_policy": adapter.missing_open_status_policy,
            "open_status_validation": "order_date_and_side",
            "open_status_assumptions": open_status_assumptions,
            "subscription_confirmation_days": adapter.subscription_confirmation_days,
            "redemption_confirmation_days": adapter.redemption_confirmation_days,
            **(
                {
                    "max_position_pct": adapter.max_position_pct,
                    "entry_tranche_pct": adapter.entry_tranche_pct,
                    "max_entry_tranches": adapter.max_entry_tranches,
                    "minimum_entry_interval_days": adapter.minimum_entry_interval_days,
                    "entry_orders_submitted": total_entry_orders_submitted,
                }
                if staged_rules_enabled
                else {}
            ),
        },
    )


def run_holdings_portfolio(
    *,
    instruments: dict[str, TradableInstrument],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    events_by_symbol: dict[str, list[dict[str, Any]]],
    stock_signals: dict[str, list[dict[str, Any]]],
    fund_signals: list[dict[str, Any]],
    report_schedule: list[dict[str, Any]],
    adapter: ExchangeExecutionAdapter,
    initial_cash: float,
    start_date: date,
    end_date: date,
    max_single_stock_pct: float,
    drift_pct_points: float,
    fallback_trading_days: Iterable[date] = (),
) -> dict[str, Any]:
    bars_index = {
        symbol: {str(row["date"]): row for row in rows}
        for symbol, rows in bars_by_symbol.items()
    }
    trading_days = sorted(
        {
            day
            for rows in bars_index.values()
            for day in rows
            if start_date.isoformat() <= day <= end_date.isoformat()
        }
    )
    if not trading_days and not instruments:
        trading_days = sorted(
            day.isoformat()
            for day in fallback_trading_days
            if start_date <= day <= end_date
        )
    if not trading_days:
        raise StrategyDomainError("market_history_unavailable", "持仓组合在请求区间没有可交易行情")
    event_index = {
        symbol: {str(row["date"]): dict(row) for row in events}
        for symbol, events in events_by_symbol.items()
    }
    cash = float(initial_cash)
    positions = {symbol: 0 for symbol in instruments}
    last_buy: dict[str, date | None] = {symbol: None for symbol in instruments}
    technical_active = {symbol: False for symbol in instruments}
    pending_events: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    total_fees = 0.0
    turnover = 0.0
    previous_report_id: str | None = None
    previous_fund_state = "BLOCKED"
    for day_text in trading_days:
        day = date.fromisoformat(day_text)
        available_symbols = [symbol for symbol in instruments if day_text in bars_index[symbol]]
        if instruments and not available_symbols:
            continue
        current_report = _latest_effective(report_schedule, day_text)
        targets = dict((current_report or {}).get("target_weights") or {})
        report_id = str((current_report or {}).get("id") or "") or None
        fund_signal = _latest_signal(fund_signals, day_text)
        fund_state = str((fund_signal or {}).get("state") or "BLOCKED")
        report_changed = report_id != previous_report_id
        fund_changed = fund_state != previous_fund_state
        forced_sell: set[str] = set()
        trigger_events: dict[str, dict[str, Any]] = {}
        rebalance_event = report_changed or fund_changed

        for symbol, event in list(pending_events.items()):
            trigger_events[symbol] = event
            if event["side"] == "buy":
                current_stock = _latest_signal(stock_signals.get(symbol, []), day_text)
                if fund_state == "ALLOW_BUY" and str((current_stock or {}).get("state")) == "ALLOW_BUY":
                    technical_active[symbol] = True
                    rebalance_event = True
            else:
                technical_active[symbol] = False
                forced_sell.add(symbol)
                rebalance_event = True
        pending_events = {}

        current_stock_signals = {
            symbol: _latest_signal(stock_signals.get(symbol, []), day_text)
            for symbol in instruments
        }
        if fund_state == "AVOID":
            forced_sell.update(symbol for symbol, quantity in positions.items() if quantity > 0)
        for symbol, signal in current_stock_signals.items():
            if str((signal or {}).get("state") or "BLOCKED") == "AVOID":
                forced_sell.add(symbol)

        open_prices = {
            symbol: float(bars_index[symbol][day_text]["open"])
            for symbol in available_symbols
        }
        equity_open = cash + sum(
            positions[symbol] * open_prices.get(symbol, 0.0) for symbol in positions
        )
        desired_quantities: dict[str, int] = {}
        for symbol, instrument in instruments.items():
            if symbol not in open_prices:
                continue
            current_quantity = positions[symbol]
            stock_state = str((current_stock_signals[symbol] or {}).get("state") or "BLOCKED")
            if symbol in forced_sell:
                desired = 0
            elif fund_state == "ALLOW_BUY" and stock_state == "ALLOW_BUY" and technical_active[symbol]:
                target_weight = min(
                    max(0.0, float(targets.get(symbol) or 0.0)),
                    max_single_stock_pct / 100.0,
                )
                desired = adapter.target_quantity(equity_open * target_weight, open_prices[symbol])
            else:
                # WATCH/BLOCKED remove permission to add, but do not manufacture
                # a data-failure sale. Existing risk/technical exits still apply.
                desired = current_quantity
            desired_quantities[symbol] = desired
            if equity_open > 0 and current_quantity != desired:
                current_weight = current_quantity * open_prices[symbol] / equity_open
                desired_weight = desired * open_prices[symbol] / equity_open
                if abs(current_weight - desired_weight) * 100 >= drift_pct_points:
                    rebalance_event = True

        if rebalance_event or forced_sell:
            # Sell before buying so target weights never borrow against unsettled cash.
            for symbol in sorted(desired_quantities):
                current = positions[symbol]
                desired = desired_quantities[symbol]
                if desired >= current or not adapter.can_sell(last_buy_date=last_buy[symbol], trading_date=day):
                    continue
                quantity = current - desired
                gross = quantity * open_prices[symbol]
                fees = adapter.fees("sell", gross)
                positions[symbol] = desired
                cash += gross - fees.total
                total_fees += fees.total
                turnover += gross
                event = _portfolio_event(
                    symbol=symbol,
                    technical_event=trigger_events.get(symbol),
                    stock_signal=current_stock_signals[symbol],
                    fund_signal=fund_signal,
                    report_id=report_id,
                    reason="forced_or_rebalance_exit" if symbol in forced_sell else "target_weight_rebalance",
                )
                trades.append(
                    _trade_payload(
                        instrument=instruments[symbol],
                        day=day,
                        side="sell",
                        price=open_prices[symbol],
                        quantity=float(quantity),
                        gross=gross,
                        fees=fees.payload(),
                        cash_after=cash,
                        event=event,
                    )
                )
            for symbol in sorted(desired_quantities):
                current = positions[symbol]
                desired = desired_quantities[symbol]
                if desired <= current:
                    continue
                maximum = (desired - current) * open_prices[symbol]
                quantity = adapter.affordable_quantity(cash, open_prices[symbol], maximum_value=maximum)
                if quantity <= 0:
                    continue
                gross = quantity * open_prices[symbol]
                fees = adapter.fees("buy", gross)
                positions[symbol] += quantity
                last_buy[symbol] = day
                cash -= gross + fees.total
                total_fees += fees.total
                turnover += gross
                event = _portfolio_event(
                    symbol=symbol,
                    technical_event=trigger_events.get(symbol),
                    stock_signal=current_stock_signals[symbol],
                    fund_signal=fund_signal,
                    report_id=report_id,
                    reason="technical_entry_or_rebalance",
                )
                trades.append(
                    _trade_payload(
                        instrument=instruments[symbol],
                        day=day,
                        side="buy",
                        price=open_prices[symbol],
                        quantity=float(quantity),
                        gross=gross,
                        fees=fees.payload(),
                        cash_after=cash,
                        event=event,
                    )
                )

        close_value = cash + sum(
            positions[symbol] * float(bars_index[symbol][day_text]["close"])
            for symbol in available_symbols
        )
        curve.append(
            {
                "date": day_text,
                "equity": round(close_value, 6),
                "cash": round(cash, 6),
                "market_value": round(close_value - cash, 6),
            }
        )
        for symbol in instruments:
            event = event_index.get(symbol, {}).get(day_text)
            if event is not None:
                pending_events[symbol] = event
        previous_report_id = report_id
        previous_fund_state = fund_state
    return _result(
        initial_cash=initial_cash,
        curve=curve,
        trades=trades,
        total_fees=total_fees,
        turnover=turnover,
        positions=positions,
        adapter={"model_id": adapter.model_id, "board_lot": adapter.board_lot},
    )


def _portfolio_event(
    *,
    symbol: str,
    technical_event: dict[str, Any] | None,
    stock_signal: dict[str, Any] | None,
    fund_signal: dict[str, Any] | None,
    report_id: str | None,
    reason: str,
) -> dict[str, Any]:
    signal_ids = list(
        dict.fromkeys(
            str(value)
            for value in [
                (technical_event or {}).get("signal_id"),
                *((technical_event or {}).get("signal_ids") or []),
                *((row or {}).get("id") for row in (stock_signal, fund_signal)),
            ]
            if value
        )
    )
    return {
        "technical_reason": (technical_event or {}).get("technical_reason") or reason,
        "signal_id": signal_ids[0] if signal_ids else None,
        "signal_ids": signal_ids,
        "source_report_set_id": report_id or (stock_signal or {}).get("source_report_set_id"),
        "subject_code": symbol,
    }


def _trade_payload(
    *,
    instrument: TradableInstrument,
    day: date,
    side: str,
    price: float,
    quantity: float,
    gross: float,
    fees: dict[str, Any],
    cash_after: float,
    event: dict[str, Any],
    order_date: str | None = None,
) -> dict[str, Any]:
    signal_ids = list(dict.fromkeys(
        str(value)
        for value in [event.get("signal_id"), *(event.get("signal_ids") or [])]
        if value
    ))
    payload = {
        "date": day.isoformat(),
        "order_date": order_date,
        "instrument_id": instrument.instrument_id,
        "symbol": instrument.code,
        "side": side,
        "price": round(price, instrument.price_precision),
        "quantity": round(quantity, instrument.quantity_precision),
        "gross_value": round(gross, 6),
        "fees": fees,
        "cash_after": round(cash_after, 6),
        "signal_id": signal_ids[0] if signal_ids else None,
        "signal_ids": signal_ids,
        "source_report_set_id": event.get("source_report_set_id"),
        "technical_reason": event.get("technical_reason"),
    }
    if event.get("entry_tranche_index") is not None:
        payload["entry_tranche_index"] = int(event["entry_tranche_index"])
        payload["entry_tranche_limit"] = int(event.get("entry_tranche_limit") or 0)
    return payload


def _result(
    *,
    initial_cash: float,
    curve: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    total_fees: float,
    turnover: float,
    positions: dict[str, Any],
    adapter: dict[str, Any],
) -> dict[str, Any]:
    if not curve:
        raise StrategyDomainError("backtest_empty_curve", "回测没有形成有效资产曲线")
    final_equity = float(curve[-1]["equity"])
    values = [float(row["equity"]) for row in curve]
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0]
    annualized = (final_equity / initial_cash) ** (252 / max(1, len(values))) - 1 if initial_cash > 0 else 0.0
    volatility = pstdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0
    sharpe = (fmean(returns) * 252 / volatility) if volatility > 0 else 0.0
    peak = values[0]
    maximum_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            maximum_drawdown = min(maximum_drawdown, value / peak - 1)
    return {
        "metrics": {
            "initial_cash": round(initial_cash, 6),
            "final_equity": round(final_equity, 6),
            "total_return_pct": round((final_equity / initial_cash - 1) * 100, 6),
            "annualized_return_pct": round(annualized * 100, 6),
            "max_drawdown_pct": round(maximum_drawdown * 100, 6),
            "sharpe": round(sharpe, 6),
            "turnover_pct": round(turnover / initial_cash * 100, 6),
            "total_fees": round(total_fees, 6),
            "trade_count": len(trades),
        },
        "equity_curve": curve,
        "trades": trades,
        "ending_positions": positions,
        "execution_adapter": adapter,
    }


def _latest_signal(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    latest = None
    for row in sorted(rows, key=lambda value: (str(value.get("effective_from") or ""), int(value.get("revision") or 0))):
        if str(row.get("effective_from") or "9999-12-31") <= day:
            latest = row
        else:
            break
    if latest is None:
        return None
    if latest.get("expires_after") and day > str(latest["expires_after"]):
        return {**latest, "state": "BLOCKED", "expired": True}
    return latest


def _latest_effective(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    latest = None
    for row in sorted(rows, key=lambda value: (str(value.get("effective_date") or ""), int(value.get("revision") or 0))):
        if str(row.get("effective_date") or "9999-12-31") <= day:
            latest = row
        else:
            break
    return latest


__all__ = [
    "run_exchange_single_asset",
    "run_holdings_portfolio",
    "run_otc_single_asset",
]
