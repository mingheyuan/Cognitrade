from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from research_system.api.finance_market_data_service import FinanceMarketDataService
from research_system.api.trading_calendar import AShareTradingCalendar
from research_system.api.veighna_paper_adapter import VeighNaPaperAdapter
from research_system.runtime.core.id_utils import generate_ulid


_CENT = Decimal("0.01")
_MICRO = Decimal("0.000001")
_COMMISSION_RATE = Decimal("0.00025")
_MIN_COMMISSION = Decimal("5")
_TRANSFER_FEE_RATE = Decimal("0.00001")
_STAMP_DUTY_RATE = Decimal("0.0005")
_DEFAULT_INITIAL_CASH = Decimal("1000000")


class PaperTradingError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class PaperTradingService:
    """Persistent server-side A-share paper ledger with FIFO lots and T+1 sells."""

    def __init__(
        self,
        *,
        database_path: Path,
        market_data: FinanceMarketDataService,
        trading_calendar: AShareTradingCalendar | None = None,
        clock: Callable[[], datetime] | None = None,
        enforce_market_hours: bool = True,
        matcher: VeighNaPaperAdapter | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.market_data = market_data
        self.trading_calendar = trading_calendar or AShareTradingCalendar()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.enforce_market_hours = bool(enforce_market_hours)
        self.matcher = matcher or VeighNaPaperAdapter()
        self._lock = RLock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def get_account(self, *, tenant_id: str, user_id: str) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            return self._account_payload(account)

    def reset_account(
        self,
        *,
        tenant_id: str,
        user_id: str,
        initial_cash: float | int | str = _DEFAULT_INITIAL_CASH,
    ) -> dict[str, Any]:
        capital = self._decimal(initial_cash)
        if capital < Decimal("10000") or capital > Decimal("1000000000"):
            raise PaperTradingError(
                "invalid_initial_cash",
                "初始资金必须在 1 万元到 10 亿元之间",
            )
        now = self._now()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._find_active_account(connection, tenant_id=tenant_id, user_id=user_id)
            generation = 1
            if existing is not None:
                generation = int(existing["generation"]) + 1
                connection.execute(
                    "UPDATE paper_accounts SET status = 'archived', updated_at = ? WHERE account_id = ?",
                    (now, existing["account_id"]),
                )
            account = self._insert_account(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                generation=generation,
                initial_cash_cents=self._to_cents(capital),
                now=now,
            )
            connection.commit()
            return self._account_payload(account)

    def place_order(
        self,
        *,
        tenant_id: str,
        user_id: str,
        symbol: str,
        side: str,
        quantity: int,
        client_order_id: str,
        order_type: str = "market",
        limit_price: float | int | str | None = None,
    ) -> dict[str, Any]:
        normalized_side = str(side or "").strip().lower()
        if normalized_side not in {"buy", "sell"}:
            raise PaperTradingError("invalid_order_side", "交易方向只能是 buy 或 sell")
        if isinstance(quantity, bool) or int(quantity) <= 0:
            raise PaperTradingError("invalid_quantity", "委托数量必须是正整数")
        normalized_quantity = int(quantity)
        normalized_order_type = str(order_type or "").strip().lower()
        if normalized_order_type not in {"market", "limit"}:
            raise PaperTradingError("invalid_order_type", "委托类型只能是 market 或 limit")
        normalized_limit_price: Decimal | None = None
        if normalized_order_type == "limit":
            normalized_limit_price = self._decimal(limit_price)
            if normalized_limit_price <= 0:
                raise PaperTradingError("invalid_limit_price", "限价委托价格必须大于 0")
        elif limit_price not in (None, ""):
            raise PaperTradingError("unexpected_limit_price", "市价委托不能指定 limit_price")
        normalized_client_order_id = str(client_order_id or "").strip()
        if not normalized_client_order_id or len(normalized_client_order_id) > 128:
            raise PaperTradingError("invalid_client_order_id", "client_order_id 不能为空且不能超过 128 字符")

        # Resolve retries before touching the quote provider. This preserves the
        # original fill even when a duplicate arrives during a provider outage.
        with self._lock, self._connection() as connection:
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            existing = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? AND client_order_id = ?",
                (account["account_id"], normalized_client_order_id),
            ).fetchone()
            if existing is not None:
                payload = self._order_payload(existing)
                payload["idempotent_replay"] = True
                return payload

        trade_moment = self.clock()
        if self.enforce_market_hours and not self.trading_calendar.is_market_open(trade_moment):
            raise PaperTradingError(
                "market_closed",
                "当前不在 A 股连续竞价交易时段",
                status_code=409,
                details={
                    "timezone": "Asia/Shanghai",
                    "next_session_date": self.trading_calendar.next_session_date(trade_moment).isoformat(),
                    "sessions": ["09:30-11:30", "13:00-15:00"],
                },
            )

        # The browser never supplies an executable price. Every fill starts from
        # a fresh server-side quote so a stale/tampered client cannot mint cash.
        quote = self.market_data.get_quote(symbol, refresh=True)
        self._validate_tradable_quote(quote, side=normalized_side)
        quote_price = self._decimal(quote["latest_price"])
        match = self.matcher.match(
            symbol=str(quote["symbol"]),
            side=normalized_side,
            quantity=normalized_quantity,
            order_type=normalized_order_type,
            quote=quote,
            limit_price=normalized_limit_price,
            moment=trade_moment,
        )
        if match.fill_price is None:
            return self._place_pending_order(
                tenant_id=tenant_id,
                user_id=user_id,
                quote=quote,
                side=normalized_side,
                quantity=normalized_quantity,
                client_order_id=normalized_client_order_id,
                order_type=normalized_order_type,
                limit_price=normalized_limit_price,
                quote_price=quote_price,
                trade_moment=trade_moment,
                engine=match.engine,
            )
        fill_price = match.fill_price
        gross = (fill_price * normalized_quantity).quantize(_CENT, rounding=ROUND_HALF_UP)
        commission, transfer_fee, stamp_duty = self._fees(gross, side=normalized_side)
        fees = commission + transfer_fee + stamp_duty
        now = self._format_moment(trade_moment)
        trade_date = self.trading_calendar.trading_date(trade_moment)
        next_available_date = self.trading_calendar.next_trading_day(trade_date)

        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            existing = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? AND client_order_id = ?",
                (account["account_id"], normalized_client_order_id),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                payload = self._order_payload(existing)
                payload["idempotent_replay"] = True
                return payload

            if normalized_side == "buy" and normalized_quantity % 100 != 0:
                connection.rollback()
                raise PaperTradingError("invalid_board_lot", "买入数量必须是 100 股的整数倍")

            realized_pnl_cents: int | None = None
            total_cents = self._to_cents(gross + fees)
            if normalized_side == "buy":
                cash_cents = self._available_cash_cents(connection, account=account)
                if total_cents > cash_cents:
                    connection.rollback()
                    raise PaperTradingError(
                        "insufficient_cash",
                        "可用资金不足",
                        status_code=409,
                        details={
                            "required": self._from_cents(total_cents),
                            "available": self._from_cents(cash_cents),
                        },
                    )
                next_cash_cents = int(account["cash_cents"]) - total_cents
            else:
                lots = connection.execute(
                    """
                    SELECT * FROM paper_position_lots
                    WHERE account_id = ? AND symbol = ? AND remaining_quantity > 0 AND available_date <= ?
                    ORDER BY acquired_at ASC, lot_id ASC
                    """,
                    (account["account_id"], str(quote["symbol"]), trade_date.isoformat()),
                ).fetchall()
                available_quantity = self._available_sell_quantity(
                    connection,
                    account_id=str(account["account_id"]),
                    symbol=str(quote["symbol"]),
                    trade_date=trade_date.isoformat(),
                )
                if normalized_quantity > available_quantity:
                    connection.rollback()
                    raise PaperTradingError(
                        "insufficient_position",
                        "T+1 可卖持仓不足",
                        status_code=409,
                        details={"requested": normalized_quantity, "available": available_quantity},
                    )
                if normalized_quantity % 100 != 0 and normalized_quantity != available_quantity:
                    connection.rollback()
                    raise PaperTradingError(
                        "invalid_board_lot",
                        "卖出数量应为 100 股整数倍；清仓时允许卖出全部可用零股",
                    )
                cost_basis_micros = self._consume_lots(
                    connection,
                    lots=lots,
                    quantity=normalized_quantity,
                    now=now,
                )
                proceeds_cents = self._to_cents(gross - fees)
                cost_basis_cents = self._to_cents(
                    Decimal(cost_basis_micros) / Decimal("1000000")
                )
                realized_pnl_cents = proceeds_cents - cost_basis_cents
                next_cash_cents = int(account["cash_cents"]) + proceeds_cents

            order_id = generate_ulid()
            connection.execute(
                """
                INSERT INTO paper_orders (
                    order_id, account_id, client_order_id, symbol, security_name, side,
                    quantity, quote_price_micros, fill_price_micros, gross_amount_cents,
                    commission_cents, transfer_fee_cents, stamp_duty_cents,
                    total_fees_cents, cash_effect_cents, realized_pnl_cents,
                    quote_as_of, quote_source, trade_date, status, created_at
                    , order_type, limit_price_micros, matching_engine,
                    reserved_cash_cents, reserved_quantity, updated_at, filled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'filled', ?,
                    ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    order_id,
                    account["account_id"],
                    normalized_client_order_id,
                    str(quote["symbol"]),
                    str(quote["name"]),
                    normalized_side,
                    normalized_quantity,
                    self._to_micros(quote_price),
                    self._to_micros(fill_price),
                    self._to_cents(gross),
                    self._to_cents(commission),
                    self._to_cents(transfer_fee),
                    self._to_cents(stamp_duty),
                    self._to_cents(fees),
                    -total_cents if normalized_side == "buy" else self._to_cents(gross - fees),
                    realized_pnl_cents,
                    str(quote.get("as_of") or ""),
                    self._quote_source(quote),
                    trade_date.isoformat(),
                    now,
                    normalized_order_type,
                    self._to_micros(normalized_limit_price) if normalized_limit_price is not None else None,
                    match.engine,
                    now,
                    now,
                ),
            )
            if normalized_side == "buy":
                unit_cost = ((gross + fees) / normalized_quantity).quantize(_MICRO, rounding=ROUND_HALF_UP)
                connection.execute(
                    """
                    INSERT INTO paper_position_lots (
                        lot_id, account_id, source_order_id, symbol, security_name,
                        original_quantity, remaining_quantity, unit_cost_micros,
                        acquired_at, available_date, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generate_ulid(),
                        account["account_id"],
                        order_id,
                        str(quote["symbol"]),
                        str(quote["name"]),
                        normalized_quantity,
                        normalized_quantity,
                        self._to_micros(unit_cost),
                        now,
                        next_available_date.isoformat(),
                        now,
                    ),
                )
            connection.execute(
                "UPDATE paper_accounts SET cash_cents = ?, updated_at = ? WHERE account_id = ?",
                (next_cash_cents, now, account["account_id"]),
            )
            order = connection.execute(
                "SELECT * FROM paper_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            connection.commit()
            return self._order_payload(order)

    def get_portfolio(self, *, tenant_id: str, user_id: str) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            lots = connection.execute(
                """
                SELECT * FROM paper_position_lots
                WHERE account_id = ? AND remaining_quantity > 0
                ORDER BY symbol, acquired_at, lot_id
                """,
                (account["account_id"],),
            ).fetchall()
            reserved_sell_rows = connection.execute(
                """
                SELECT symbol, COALESCE(SUM(reserved_quantity), 0) AS reserved_quantity
                FROM paper_orders
                WHERE account_id = ? AND status = 'pending' AND side = 'sell'
                GROUP BY symbol
                """,
                (account["account_id"],),
            ).fetchall()
            reserved_sells = {
                str(row["symbol"]): int(row["reserved_quantity"])
                for row in reserved_sell_rows
            }
            frozen_cash_cents = self._frozen_cash_cents(
                connection,
                account_id=str(account["account_id"]),
            )
            open_order_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM paper_orders WHERE account_id = ? AND status = 'pending'",
                    (account["account_id"],),
                ).fetchone()["count"]
            )
        positions_by_symbol: dict[str, dict[str, Any]] = {}
        today = self.trading_calendar.trading_date(self.clock()).isoformat()
        for lot in lots:
            symbol = str(lot["symbol"])
            position = positions_by_symbol.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": str(lot["security_name"]),
                    "quantity": 0,
                    "available_quantity": 0,
                    "cost_basis_micros": 0,
                },
            )
            remaining = int(lot["remaining_quantity"])
            position["quantity"] += remaining
            if str(lot["available_date"]) <= today:
                position["available_quantity"] += remaining
            position["cost_basis_micros"] += int(lot["unit_cost_micros"]) * remaining

        positions: list[dict[str, Any]] = []
        total_market_value_cents = 0
        total_cost_basis_cents = 0
        for position in positions_by_symbol.values():
            quote: dict[str, Any]
            try:
                quote = self.market_data.get_quote(position["symbol"], refresh=False)
                mark_price = self._decimal(quote["latest_price"])
                quote_stale = False
            except Exception:
                mark_price = (
                    Decimal(position["cost_basis_micros"])
                    / Decimal(position["quantity"])
                    / Decimal("1000000")
                )
                quote = {"as_of": None, "source": {}}
                quote_stale = True
            market_value_cents = self._to_cents(mark_price * int(position["quantity"]))
            cost_basis_cents = self._to_cents(
                Decimal(position["cost_basis_micros"]) / Decimal("1000000")
            )
            pnl_cents = market_value_cents - cost_basis_cents
            total_market_value_cents += market_value_cents
            total_cost_basis_cents += cost_basis_cents
            positions.append(
                {
                    "symbol": position["symbol"],
                    "name": position["name"],
                    "quantity": position["quantity"],
                    "available_quantity": position["available_quantity"],
                    "average_cost": self._from_micros(
                        int(position["cost_basis_micros"]) // int(position["quantity"])
                    ),
                    "latest_price": float(mark_price),
                    "market_value": self._from_cents(market_value_cents),
                    "cost_basis": self._from_cents(cost_basis_cents),
                    "unrealized_pnl": self._from_cents(pnl_cents),
                    "unrealized_pnl_pct": self._percentage(pnl_cents, cost_basis_cents),
                    "quote_as_of": quote.get("as_of"),
                    "quote_stale": quote_stale,
                }
            )
        for position in positions:
            position["available_quantity"] = max(
                int(position["available_quantity"]) - reserved_sells.get(str(position["symbol"]), 0),
                0,
            )
        cash_cents = int(account["cash_cents"])
        equity_cents = cash_cents + total_market_value_cents
        initial_cash_cents = int(account["initial_cash_cents"])
        total_pnl_cents = equity_cents - initial_cash_cents
        return {
            "account": self._account_payload(account),
            "summary": {
                "cash": self._from_cents(cash_cents - frozen_cash_cents),
                "cash_balance": self._from_cents(cash_cents),
                "frozen_cash": self._from_cents(frozen_cash_cents),
                "market_value": self._from_cents(total_market_value_cents),
                "equity": self._from_cents(equity_cents),
                "cost_basis": self._from_cents(total_cost_basis_cents),
                "total_pnl": self._from_cents(total_pnl_cents),
                "total_return_pct": self._percentage(total_pnl_cents, initial_cash_cents),
                "position_count": len(positions),
                "open_order_count": open_order_count,
            },
            "positions": positions,
            "as_of": self._now(),
        }

    def list_orders(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        normalized_limit = min(max(int(limit), 1), 200)
        with self._lock, self._connection() as connection:
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            rows = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
                (account["account_id"], normalized_limit),
            ).fetchall()
            return [self._order_payload(row) for row in rows]

    def engine_status(self) -> dict[str, Any]:
        return self.matcher.status()

    def cancel_order(
        self,
        *,
        tenant_id: str,
        user_id: str,
        order_id: str,
    ) -> dict[str, Any]:
        now = self._now()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            order = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? AND order_id = ?",
                (account["account_id"], str(order_id)),
            ).fetchone()
            if order is None:
                raise PaperTradingError("order_not_found", "未找到指定模拟委托", status_code=404)
            if str(order["status"]) != "pending":
                raise PaperTradingError(
                    "order_not_cancellable",
                    "只有待成交委托可以撤销",
                    status_code=409,
                )
            connection.execute(
                """
                UPDATE paper_orders
                SET status = 'cancelled', reserved_cash_cents = 0,
                    reserved_quantity = 0, updated_at = ?
                WHERE order_id = ?
                """,
                (now, order_id),
            )
            updated = connection.execute(
                "SELECT * FROM paper_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            connection.commit()
            return self._order_payload(updated)

    def match_open_orders(
        self,
        *,
        tenant_id: str,
        user_id: str,
        refresh: bool = True,
    ) -> dict[str, Any]:
        trade_moment = self.clock()
        if self.enforce_market_hours and not self.trading_calendar.is_market_open(trade_moment):
            raise PaperTradingError(
                "market_closed",
                "当前不在 A 股连续竞价交易时段，挂单保持待成交",
                status_code=409,
            )
        with self._lock, self._connection() as connection:
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            pending = connection.execute(
                """
                SELECT * FROM paper_orders
                WHERE account_id = ? AND status = 'pending'
                ORDER BY created_at, order_id
                """,
                (account["account_id"],),
            ).fetchall()

        matched: list[dict[str, Any]] = []
        unchanged: list[str] = []
        errors: list[dict[str, str]] = []
        quote_cache: dict[str, dict[str, Any]] = {}
        for order in pending:
            symbol = str(order["symbol"])
            try:
                quote = quote_cache.get(symbol)
                if quote is None:
                    quote = self.market_data.get_quote(symbol, refresh=refresh)
                    quote_cache[symbol] = quote
                self._validate_tradable_quote(quote, side=str(order["side"]))
                limit_micros = order["limit_price_micros"]
                limit_price = (
                    Decimal(int(limit_micros)) / Decimal("1000000")
                    if limit_micros is not None
                    else None
                )
                match = self.matcher.match(
                    symbol=symbol,
                    side=str(order["side"]),
                    quantity=int(order["quantity"]),
                    order_type=str(order["order_type"] or "limit"),
                    quote=quote,
                    limit_price=limit_price,
                    moment=trade_moment,
                )
                if match.fill_price is None:
                    unchanged.append(str(order["order_id"]))
                    continue
                matched.append(
                    self._fill_pending_order(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        order_id=str(order["order_id"]),
                        quote=quote,
                        fill_price=match.fill_price,
                        trade_moment=trade_moment,
                        engine=match.engine,
                    )
                )
            except Exception as exc:
                errors.append(
                    {
                        "order_id": str(order["order_id"]),
                        "error": str(exc),
                    }
                )
        return {
            "matched": matched,
            "unchanged_order_ids": unchanged,
            "errors": errors,
            "matched_count": len(matched),
            "pending_count": len(unchanged) + len(errors),
        }

    def _place_pending_order(
        self,
        *,
        tenant_id: str,
        user_id: str,
        quote: dict[str, Any],
        side: str,
        quantity: int,
        client_order_id: str,
        order_type: str,
        limit_price: Decimal | None,
        quote_price: Decimal,
        trade_moment: datetime,
        engine: str,
    ) -> dict[str, Any]:
        if order_type != "limit" or limit_price is None:
            raise PaperTradingError(
                "market_order_not_filled",
                "市价委托未能由撮合引擎成交",
                status_code=409,
            )
        if side == "buy" and quantity % 100 != 0:
            raise PaperTradingError("invalid_board_lot", "买入数量必须是 100 股的整数倍")

        now = self._format_moment(trade_moment)
        trade_date = self.trading_calendar.trading_date(trade_moment).isoformat()
        reserved_cash_cents = 0
        reserved_quantity = 0
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            existing = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? AND client_order_id = ?",
                (account["account_id"], client_order_id),
            ).fetchone()
            if existing is not None:
                payload = self._order_payload(existing)
                payload["idempotent_replay"] = True
                return payload

            if side == "buy":
                gross = (limit_price * quantity).quantize(_CENT, rounding=ROUND_HALF_UP)
                commission, transfer_fee, stamp_duty = self._fees(gross, side=side)
                reserved_cash_cents = self._to_cents(
                    gross + commission + transfer_fee + stamp_duty
                )
                available_cash_cents = self._available_cash_cents(connection, account=account)
                if reserved_cash_cents > available_cash_cents:
                    raise PaperTradingError(
                        "insufficient_cash",
                        "可用资金不足，无法冻结限价委托资金",
                        status_code=409,
                        details={
                            "required": self._from_cents(reserved_cash_cents),
                            "available": self._from_cents(available_cash_cents),
                        },
                    )
            else:
                available_quantity = self._available_sell_quantity(
                    connection,
                    account_id=str(account["account_id"]),
                    symbol=str(quote["symbol"]),
                    trade_date=trade_date,
                )
                if quantity > available_quantity:
                    raise PaperTradingError(
                        "insufficient_position",
                        "T+1 可卖持仓不足，无法冻结限价卖单",
                        status_code=409,
                        details={"requested": quantity, "available": available_quantity},
                    )
                if quantity % 100 != 0 and quantity != available_quantity:
                    raise PaperTradingError(
                        "invalid_board_lot",
                        "卖出数量应为 100 股整数倍；清仓时允许卖出全部可用零股",
                    )
                reserved_quantity = quantity

            order_id = generate_ulid()
            connection.execute(
                """
                INSERT INTO paper_orders (
                    order_id, account_id, client_order_id, symbol, security_name, side,
                    quantity, quote_price_micros, fill_price_micros, gross_amount_cents,
                    commission_cents, transfer_fee_cents, stamp_duty_cents,
                    total_fees_cents, cash_effect_cents, realized_pnl_cents,
                    quote_as_of, quote_source, trade_date, status, created_at,
                    order_type, limit_price_micros, matching_engine,
                    reserved_cash_cents, reserved_quantity, updated_at, filled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, NULL,
                    ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    order_id,
                    account["account_id"],
                    client_order_id,
                    str(quote["symbol"]),
                    str(quote["name"]),
                    side,
                    quantity,
                    self._to_micros(quote_price),
                    str(quote.get("as_of") or ""),
                    self._quote_source(quote),
                    trade_date,
                    now,
                    order_type,
                    self._to_micros(limit_price),
                    engine,
                    reserved_cash_cents,
                    reserved_quantity,
                    now,
                ),
            )
            order = connection.execute(
                "SELECT * FROM paper_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            connection.commit()
            return self._order_payload(order)

    def _fill_pending_order(
        self,
        *,
        tenant_id: str,
        user_id: str,
        order_id: str,
        quote: dict[str, Any],
        fill_price: Decimal,
        trade_moment: datetime,
        engine: str,
    ) -> dict[str, Any]:
        now = self._format_moment(trade_moment)
        trade_date = self.trading_calendar.trading_date(trade_moment)
        next_available_date = self.trading_calendar.next_trading_day(trade_date)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            order = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? AND order_id = ?",
                (account["account_id"], order_id),
            ).fetchone()
            if order is None:
                raise PaperTradingError("order_not_found", "未找到指定模拟委托", status_code=404)
            if str(order["status"]) != "pending":
                return self._order_payload(order)

            side = str(order["side"])
            quantity = int(order["quantity"])
            gross = (fill_price * quantity).quantize(_CENT, rounding=ROUND_HALF_UP)
            commission, transfer_fee, stamp_duty = self._fees(gross, side=side)
            fees = commission + transfer_fee + stamp_duty
            total_cents = self._to_cents(gross + fees)
            realized_pnl_cents: int | None = None
            if side == "buy":
                available_cash_cents = self._available_cash_cents(
                    connection,
                    account=account,
                    exclude_order_id=order_id,
                )
                if total_cents > available_cash_cents:
                    raise PaperTradingError(
                        "insufficient_cash",
                        "限价单触发时可用资金不足",
                        status_code=409,
                    )
                next_cash_cents = int(account["cash_cents"]) - total_cents
            else:
                lots = connection.execute(
                    """
                    SELECT * FROM paper_position_lots
                    WHERE account_id = ? AND symbol = ? AND remaining_quantity > 0
                      AND available_date <= ?
                    ORDER BY acquired_at ASC, lot_id ASC
                    """,
                    (account["account_id"], order["symbol"], trade_date.isoformat()),
                ).fetchall()
                available_quantity = self._available_sell_quantity(
                    connection,
                    account_id=str(account["account_id"]),
                    symbol=str(order["symbol"]),
                    trade_date=trade_date.isoformat(),
                    exclude_order_id=order_id,
                )
                if quantity > available_quantity:
                    raise PaperTradingError(
                        "insufficient_position",
                        "限价单触发时 T+1 可卖持仓不足",
                        status_code=409,
                    )
                cost_basis_micros = self._consume_lots(
                    connection,
                    lots=lots,
                    quantity=quantity,
                    now=now,
                )
                proceeds_cents = self._to_cents(gross - fees)
                realized_pnl_cents = proceeds_cents - self._to_cents(
                    Decimal(cost_basis_micros) / Decimal("1000000")
                )
                next_cash_cents = int(account["cash_cents"]) + proceeds_cents

            if side == "buy":
                unit_cost = ((gross + fees) / quantity).quantize(_MICRO, rounding=ROUND_HALF_UP)
                connection.execute(
                    """
                    INSERT INTO paper_position_lots (
                        lot_id, account_id, source_order_id, symbol, security_name,
                        original_quantity, remaining_quantity, unit_cost_micros,
                        acquired_at, available_date, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generate_ulid(),
                        account["account_id"],
                        order_id,
                        str(order["symbol"]),
                        str(order["security_name"]),
                        quantity,
                        quantity,
                        self._to_micros(unit_cost),
                        now,
                        next_available_date.isoformat(),
                        now,
                    ),
                )
            cash_effect_cents = (
                -total_cents if side == "buy" else self._to_cents(gross - fees)
            )
            connection.execute(
                """
                UPDATE paper_orders SET
                    quote_price_micros = ?, fill_price_micros = ?,
                    gross_amount_cents = ?, commission_cents = ?,
                    transfer_fee_cents = ?, stamp_duty_cents = ?,
                    total_fees_cents = ?, cash_effect_cents = ?,
                    realized_pnl_cents = ?, quote_as_of = ?, quote_source = ?,
                    trade_date = ?, status = 'filled', matching_engine = ?,
                    reserved_cash_cents = 0, reserved_quantity = 0,
                    updated_at = ?, filled_at = ?
                WHERE order_id = ?
                """,
                (
                    self._to_micros(self._decimal(quote["latest_price"])),
                    self._to_micros(fill_price),
                    self._to_cents(gross),
                    self._to_cents(commission),
                    self._to_cents(transfer_fee),
                    self._to_cents(stamp_duty),
                    self._to_cents(fees),
                    cash_effect_cents,
                    realized_pnl_cents,
                    str(quote.get("as_of") or ""),
                    self._quote_source(quote),
                    trade_date.isoformat(),
                    engine,
                    now,
                    now,
                    order_id,
                ),
            )
            connection.execute(
                "UPDATE paper_accounts SET cash_cents = ?, updated_at = ? WHERE account_id = ?",
                (next_cash_cents, now, account["account_id"]),
            )
            updated = connection.execute(
                "SELECT * FROM paper_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            connection.commit()
            return self._order_payload(updated)

    def _initialize_schema(self) -> None:
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    account_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
                    initial_cash_cents INTEGER NOT NULL,
                    cash_cents INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (tenant_id, user_id, generation)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_paper_account
                    ON paper_accounts (tenant_id, user_id) WHERE status = 'active';
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES paper_accounts(account_id),
                    client_order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    security_name TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                    quantity INTEGER NOT NULL,
                    quote_price_micros INTEGER NOT NULL,
                    fill_price_micros INTEGER NOT NULL,
                    gross_amount_cents INTEGER NOT NULL,
                    commission_cents INTEGER NOT NULL,
                    transfer_fee_cents INTEGER NOT NULL,
                    stamp_duty_cents INTEGER NOT NULL,
                    total_fees_cents INTEGER NOT NULL,
                    cash_effect_cents INTEGER NOT NULL,
                    realized_pnl_cents INTEGER,
                    quote_as_of TEXT,
                    quote_source TEXT,
                    trade_date TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    order_type TEXT NOT NULL DEFAULT 'market',
                    limit_price_micros INTEGER,
                    matching_engine TEXT NOT NULL DEFAULT 'cognitrade-compatible',
                    reserved_cash_cents INTEGER NOT NULL DEFAULT 0,
                    reserved_quantity INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT,
                    filled_at TEXT,
                    UNIQUE (account_id, client_order_id)
                );
                CREATE INDEX IF NOT EXISTS paper_orders_account_created
                    ON paper_orders (account_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS paper_position_lots (
                    lot_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES paper_accounts(account_id),
                    source_order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
                    symbol TEXT NOT NULL,
                    security_name TEXT NOT NULL,
                    original_quantity INTEGER NOT NULL,
                    remaining_quantity INTEGER NOT NULL,
                    unit_cost_micros INTEGER NOT NULL,
                    acquired_at TEXT NOT NULL,
                    available_date TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS paper_lots_account_symbol
                    ON paper_position_lots (account_id, symbol, available_date);
                PRAGMA user_version = 3;
                """
            )
            self._ensure_column(connection, "paper_orders", "trade_date", "TEXT")
            self._ensure_column(connection, "paper_orders", "order_type", "TEXT NOT NULL DEFAULT 'market'")
            self._ensure_column(connection, "paper_orders", "limit_price_micros", "INTEGER")
            self._ensure_column(
                connection,
                "paper_orders",
                "matching_engine",
                "TEXT NOT NULL DEFAULT 'cognitrade-compatible'",
            )
            self._ensure_column(
                connection,
                "paper_orders",
                "reserved_cash_cents",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                connection,
                "paper_orders",
                "reserved_quantity",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(connection, "paper_orders", "updated_at", "TEXT")
            self._ensure_column(connection, "paper_orders", "filled_at", "TEXT")

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _active_account(self, connection: sqlite3.Connection, *, tenant_id: str, user_id: str) -> sqlite3.Row:
        account = self._find_active_account(connection, tenant_id=tenant_id, user_id=user_id)
        if account is not None:
            return account
        generation_row = connection.execute(
            "SELECT COALESCE(MAX(generation), 0) AS generation FROM paper_accounts WHERE tenant_id = ? AND user_id = ?",
            (tenant_id, user_id),
        ).fetchone()
        return self._insert_account(
            connection,
            tenant_id=tenant_id,
            user_id=user_id,
            generation=int(generation_row["generation"]) + 1,
            initial_cash_cents=self._to_cents(_DEFAULT_INITIAL_CASH),
            now=self._now(),
        )

    @staticmethod
    def _find_active_account(
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        user_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM paper_accounts WHERE tenant_id = ? AND user_id = ? AND status = 'active'",
            (tenant_id, user_id),
        ).fetchone()

    @staticmethod
    def _insert_account(
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        user_id: str,
        generation: int,
        initial_cash_cents: int,
        now: str,
    ) -> sqlite3.Row:
        account_id = generate_ulid()
        connection.execute(
            """
            INSERT INTO paper_accounts (
                account_id, tenant_id, user_id, generation, status,
                initial_cash_cents, cash_cents, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (
                account_id,
                tenant_id,
                user_id,
                generation,
                initial_cash_cents,
                initial_cash_cents,
                now,
                now,
            ),
        )
        account = connection.execute(
            "SELECT * FROM paper_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        assert account is not None
        return account

    @staticmethod
    def _consume_lots(
        connection: sqlite3.Connection,
        *,
        lots: list[sqlite3.Row],
        quantity: int,
        now: str,
    ) -> int:
        remaining_to_sell = quantity
        cost_basis_micros = 0
        for lot in lots:
            if remaining_to_sell <= 0:
                break
            consumed = min(int(lot["remaining_quantity"]), remaining_to_sell)
            cost_basis_micros += consumed * int(lot["unit_cost_micros"])
            connection.execute(
                "UPDATE paper_position_lots SET remaining_quantity = ?, updated_at = ? WHERE lot_id = ?",
                (int(lot["remaining_quantity"]) - consumed, now, lot["lot_id"]),
            )
            remaining_to_sell -= consumed
        return cost_basis_micros

    def _account_payload(self, account: sqlite3.Row) -> dict[str, Any]:
        return {
            "account_id": str(account["account_id"]),
            "generation": int(account["generation"]),
            "status": str(account["status"]),
            "currency": "CNY",
            "initial_cash": PaperTradingService._from_cents(int(account["initial_cash_cents"])),
            "cash": PaperTradingService._from_cents(int(account["cash_cents"])),
            "created_at": str(account["created_at"]),
            "updated_at": str(account["updated_at"]),
            "rules": {
                "market": "A-share paper trading",
                "board_lot": 100,
                "settlement": "T+1",
                "short_selling": False,
                "price_source": "server-side Finance Pack quote",
                "matching_engine": self.matcher.status()["engine"],
                "market_hours_enforced": self.enforce_market_hours,
                **self.trading_calendar.rule_summary(),
            },
        }

    @staticmethod
    def _order_payload(order: sqlite3.Row) -> dict[str, Any]:
        realized = order["realized_pnl_cents"]
        keys = set(order.keys())
        limit_price_micros = order["limit_price_micros"] if "limit_price_micros" in keys else None
        return {
            "order_id": str(order["order_id"]),
            "client_order_id": str(order["client_order_id"]),
            "symbol": str(order["symbol"]),
            "name": str(order["security_name"]),
            "side": str(order["side"]),
            "order_type": str(order["order_type"] or "market") if "order_type" in keys else "market",
            "limit_price": (
                PaperTradingService._from_micros(int(limit_price_micros))
                if limit_price_micros is not None
                else None
            ),
            "quantity": int(order["quantity"]),
            "quote_price": PaperTradingService._from_micros(int(order["quote_price_micros"])),
            "fill_price": PaperTradingService._from_micros(int(order["fill_price_micros"])),
            "gross_amount": PaperTradingService._from_cents(int(order["gross_amount_cents"])),
            "fees": {
                "commission": PaperTradingService._from_cents(int(order["commission_cents"])),
                "transfer_fee": PaperTradingService._from_cents(int(order["transfer_fee_cents"])),
                "stamp_duty": PaperTradingService._from_cents(int(order["stamp_duty_cents"])),
                "total": PaperTradingService._from_cents(int(order["total_fees_cents"])),
            },
            "cash_effect": PaperTradingService._from_cents(int(order["cash_effect_cents"])),
            "realized_pnl": PaperTradingService._from_cents(int(realized)) if realized is not None else None,
            "quote_as_of": str(order["quote_as_of"] or ""),
            "quote_source": str(order["quote_source"] or ""),
            "trade_date": str(order["trade_date"] or "") if "trade_date" in order.keys() else "",
            "status": str(order["status"]),
            "matching_engine": (
                str(order["matching_engine"] or "cognitrade-compatible")
                if "matching_engine" in keys
                else "cognitrade-compatible"
            ),
            "reserved_cash": (
                PaperTradingService._from_cents(int(order["reserved_cash_cents"] or 0))
                if "reserved_cash_cents" in keys
                else 0.0
            ),
            "reserved_quantity": (
                int(order["reserved_quantity"] or 0)
                if "reserved_quantity" in keys
                else 0
            ),
            "created_at": str(order["created_at"]),
            "updated_at": (
                str(order["updated_at"] or order["created_at"])
                if "updated_at" in keys
                else str(order["created_at"])
            ),
            "filled_at": (
                str(order["filled_at"] or "")
                if "filled_at" in keys
                else (str(order["created_at"]) if str(order["status"]) == "filled" else "")
            ),
            "idempotent_replay": False,
        }

    @staticmethod
    def _fees(gross: Decimal, *, side: str) -> tuple[Decimal, Decimal, Decimal]:
        commission = max(
            (gross * _COMMISSION_RATE).quantize(_CENT, rounding=ROUND_HALF_UP),
            _MIN_COMMISSION,
        )
        transfer_fee = (gross * _TRANSFER_FEE_RATE).quantize(
            _CENT,
            rounding=ROUND_HALF_UP,
        )
        stamp_duty = (
            (gross * _STAMP_DUTY_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
            if side == "sell"
            else Decimal("0")
        )
        return commission, transfer_fee, stamp_duty

    @staticmethod
    def _frozen_cash_cents(
        connection: sqlite3.Connection,
        *,
        account_id: str,
        exclude_order_id: str | None = None,
    ) -> int:
        if exclude_order_id:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(reserved_cash_cents), 0) AS frozen
                FROM paper_orders
                WHERE account_id = ? AND status = 'pending' AND order_id != ?
                """,
                (account_id, exclude_order_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(reserved_cash_cents), 0) AS frozen
                FROM paper_orders
                WHERE account_id = ? AND status = 'pending'
                """,
                (account_id,),
            ).fetchone()
        return int(row["frozen"])

    def _available_cash_cents(
        self,
        connection: sqlite3.Connection,
        *,
        account: sqlite3.Row,
        exclude_order_id: str | None = None,
    ) -> int:
        return int(account["cash_cents"]) - self._frozen_cash_cents(
            connection,
            account_id=str(account["account_id"]),
            exclude_order_id=exclude_order_id,
        )

    @staticmethod
    def _available_sell_quantity(
        connection: sqlite3.Connection,
        *,
        account_id: str,
        symbol: str,
        trade_date: str,
        exclude_order_id: str | None = None,
    ) -> int:
        lots_row = connection.execute(
            """
            SELECT COALESCE(SUM(remaining_quantity), 0) AS quantity
            FROM paper_position_lots
            WHERE account_id = ? AND symbol = ? AND remaining_quantity > 0
              AND available_date <= ?
            """,
            (account_id, symbol, trade_date),
        ).fetchone()
        if exclude_order_id:
            reserved_row = connection.execute(
                """
                SELECT COALESCE(SUM(reserved_quantity), 0) AS quantity
                FROM paper_orders
                WHERE account_id = ? AND symbol = ? AND side = 'sell'
                  AND status = 'pending' AND order_id != ?
                """,
                (account_id, symbol, exclude_order_id),
            ).fetchone()
        else:
            reserved_row = connection.execute(
                """
                SELECT COALESCE(SUM(reserved_quantity), 0) AS quantity
                FROM paper_orders
                WHERE account_id = ? AND symbol = ? AND side = 'sell'
                  AND status = 'pending'
                """,
                (account_id, symbol),
            ).fetchone()
        return max(int(lots_row["quantity"]) - int(reserved_row["quantity"]), 0)

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise PaperTradingError("invalid_number", "金额或价格格式无效") from exc
        if not result.is_finite():
            raise PaperTradingError("invalid_number", "金额或价格必须是有限数值")
        return result

    @staticmethod
    def _to_cents(value: Decimal) -> int:
        return int((value.quantize(_CENT, rounding=ROUND_HALF_UP) * 100).to_integral_value())

    @staticmethod
    def _from_cents(value: int) -> float:
        return float((Decimal(value) / 100).quantize(_CENT))

    @staticmethod
    def _to_micros(value: Decimal) -> int:
        return int((value.quantize(_MICRO, rounding=ROUND_HALF_UP) * 1_000_000).to_integral_value())

    @staticmethod
    def _from_micros(value: int) -> float:
        return float((Decimal(value) / 1_000_000).quantize(_MICRO))

    @staticmethod
    def _percentage(numerator_cents: int, denominator_cents: int) -> float | None:
        if denominator_cents == 0:
            return None
        return float(
            (Decimal(numerator_cents) / Decimal(denominator_cents) * 100).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )
        )

    @staticmethod
    def _quote_source(quote: dict[str, Any]) -> str:
        source = quote.get("source")
        if isinstance(source, dict):
            return str(source.get("provider_id") or source.get("vendor") or "finance_pack")
        return str(source or "finance_pack")

    def _now(self) -> str:
        return self._format_moment(self.clock())

    @staticmethod
    def _format_moment(moment: datetime) -> str:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()

    def _validate_tradable_quote(self, quote: dict[str, Any], *, side: str) -> None:
        if quote.get("tradable") is False or quote.get("suspended") is True:
            raise PaperTradingError("security_suspended", "该证券当前停牌或不可交易", status_code=409)
        price = self._decimal(quote.get("latest_price"))
        if price <= 0:
            raise PaperTradingError("invalid_quote", "行情源未返回有效成交价格", status_code=409)
        limit_up = quote.get("limit_up")
        limit_down = quote.get("limit_down")
        if side == "buy" and limit_up is not None and price >= self._decimal(limit_up):
            raise PaperTradingError("price_limit_reached", "证券已触及涨停价，模拟买入不成交", status_code=409)
        if side == "sell" and limit_down is not None and price <= self._decimal(limit_down):
            raise PaperTradingError("price_limit_reached", "证券已触及跌停价，模拟卖出不成交", status_code=409)


__all__ = ["PaperTradingError", "PaperTradingService"]
