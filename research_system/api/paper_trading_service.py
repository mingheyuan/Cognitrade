from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from research_system.api.finance_market_data_service import FinanceMarketDataService
from research_system.runtime.core.id_utils import generate_ulid


_CENT = Decimal("0.01")
_MICRO = Decimal("0.000001")
_BUY_SLIPPAGE = Decimal("0.0005")
_SELL_SLIPPAGE = Decimal("0.0005")
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

    def __init__(self, *, database_path: Path, market_data: FinanceMarketDataService) -> None:
        self.database_path = Path(database_path)
        self.market_data = market_data
        self._lock = RLock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def get_account(self, *, tenant_id: str, user_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
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
        with self._lock, self._connect() as connection:
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
    ) -> dict[str, Any]:
        normalized_side = str(side or "").strip().lower()
        if normalized_side not in {"buy", "sell"}:
            raise PaperTradingError("invalid_order_side", "交易方向只能是 buy 或 sell")
        if isinstance(quantity, bool) or int(quantity) <= 0:
            raise PaperTradingError("invalid_quantity", "委托数量必须是正整数")
        normalized_quantity = int(quantity)
        normalized_client_order_id = str(client_order_id or "").strip()
        if not normalized_client_order_id or len(normalized_client_order_id) > 128:
            raise PaperTradingError("invalid_client_order_id", "client_order_id 不能为空且不能超过 128 字符")

        # Resolve retries before touching the quote provider. This preserves the
        # original fill even when a duplicate arrives during a provider outage.
        with self._lock, self._connect() as connection:
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            existing = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? AND client_order_id = ?",
                (account["account_id"], normalized_client_order_id),
            ).fetchone()
            if existing is not None:
                payload = self._order_payload(existing)
                payload["idempotent_replay"] = True
                return payload

        # The browser never supplies an executable price. Every fill starts from
        # a fresh server-side quote so a stale/tampered client cannot mint cash.
        quote = self.market_data.get_quote(symbol, refresh=True)
        quote_price = self._decimal(quote["latest_price"])
        fill_price = (
            quote_price * (Decimal("1") + _BUY_SLIPPAGE)
            if normalized_side == "buy"
            else quote_price * (Decimal("1") - _SELL_SLIPPAGE)
        ).quantize(_MICRO, rounding=ROUND_HALF_UP)
        gross = (fill_price * normalized_quantity).quantize(_CENT, rounding=ROUND_HALF_UP)
        commission = max((gross * _COMMISSION_RATE).quantize(_CENT, rounding=ROUND_HALF_UP), _MIN_COMMISSION)
        transfer_fee = (gross * _TRANSFER_FEE_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
        stamp_duty = (
            (gross * _STAMP_DUTY_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
            if normalized_side == "sell"
            else Decimal("0")
        )
        fees = commission + transfer_fee + stamp_duty
        now = self._now()
        trade_date = date.today()

        with self._lock, self._connect() as connection:
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
                cash_cents = int(account["cash_cents"])
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
                next_cash_cents = cash_cents - total_cents
            else:
                lots = connection.execute(
                    """
                    SELECT * FROM paper_position_lots
                    WHERE account_id = ? AND symbol = ? AND remaining_quantity > 0 AND available_date <= ?
                    ORDER BY acquired_at ASC, lot_id ASC
                    """,
                    (account["account_id"], str(quote["symbol"]), trade_date.isoformat()),
                ).fetchall()
                available_quantity = sum(int(lot["remaining_quantity"]) for lot in lots)
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
                    quote_as_of, quote_source, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'filled', ?)
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
                        (trade_date + timedelta(days=1)).isoformat(),
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
        with self._lock, self._connect() as connection:
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            lots = connection.execute(
                """
                SELECT * FROM paper_position_lots
                WHERE account_id = ? AND remaining_quantity > 0
                ORDER BY symbol, acquired_at, lot_id
                """,
                (account["account_id"],),
            ).fetchall()
        positions_by_symbol: dict[str, dict[str, Any]] = {}
        today = date.today().isoformat()
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
        cash_cents = int(account["cash_cents"])
        equity_cents = cash_cents + total_market_value_cents
        initial_cash_cents = int(account["initial_cash_cents"])
        total_pnl_cents = equity_cents - initial_cash_cents
        return {
            "account": self._account_payload(account),
            "summary": {
                "cash": self._from_cents(cash_cents),
                "market_value": self._from_cents(total_market_value_cents),
                "equity": self._from_cents(equity_cents),
                "cost_basis": self._from_cents(total_cost_basis_cents),
                "total_pnl": self._from_cents(total_pnl_cents),
                "total_return_pct": self._percentage(total_pnl_cents, initial_cash_cents),
                "position_count": len(positions),
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
        with self._lock, self._connect() as connection:
            account = self._active_account(connection, tenant_id=tenant_id, user_id=user_id)
            rows = connection.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
                (account["account_id"], normalized_limit),
            ).fetchall()
            return [self._order_payload(row) for row in rows]

    def _initialize_schema(self) -> None:
        with self._lock, self._connect() as connection:
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
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
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
                PRAGMA user_version = 1;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

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

    @staticmethod
    def _account_payload(account: sqlite3.Row) -> dict[str, Any]:
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
            },
        }

    @staticmethod
    def _order_payload(order: sqlite3.Row) -> dict[str, Any]:
        realized = order["realized_pnl_cents"]
        return {
            "order_id": str(order["order_id"]),
            "client_order_id": str(order["client_order_id"]),
            "symbol": str(order["symbol"]),
            "name": str(order["security_name"]),
            "side": str(order["side"]),
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
            "status": str(order["status"]),
            "created_at": str(order["created_at"]),
            "idempotent_replay": False,
        }

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

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()


__all__ = ["PaperTradingError", "PaperTradingService"]
