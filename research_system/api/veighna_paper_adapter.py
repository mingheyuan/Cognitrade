from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from importlib import metadata
from importlib.util import find_spec
from typing import Any


_MICRO = Decimal("0.000001")
_DEFAULT_HALF_SPREAD = Decimal("0.0005")


@dataclass(frozen=True)
class PaperMatch:
    status: str
    fill_price: Decimal | None
    bid_price: Decimal
    ask_price: Decimal
    engine: str


class VeighNaPaperAdapter:
    """Headless adapter around VeighNa PaperAccount's official crossing logic.

    PaperAccount intentionally does not calculate cash. Cognitrade therefore
    delegates order/tick modelling and price crossing to VeighNa while keeping
    cash, fees, A-share lot rules and T+1 settlement in its audited SQLite
    ledger.
    """

    engine_name = "veighna-paperaccount"

    def __init__(self, *, half_spread: Decimal = _DEFAULT_HALF_SPREAD) -> None:
        self.half_spread = Decimal(half_spread)
        self._available = bool(find_spec("vnpy") and find_spec("vnpy_paperaccount"))
        self._load_error: str | None = None

    def status(self) -> dict[str, Any]:
        versions: dict[str, str | None] = {}
        for package in ("vnpy", "vnpy_paperaccount"):
            try:
                versions[package] = metadata.version(package)
            except metadata.PackageNotFoundError:
                versions[package] = None
        return {
            "engine": self.engine_name if self._available else "cognitrade-compatible",
            "available": self._available,
            "versions": versions,
            "mode": "headless_tick_crossing",
            "cash_ledger": "cognitrade-sqlite",
            "load_error": self._load_error,
            "capabilities": {
                "market_orders": True,
                "limit_orders": True,
                "stop_orders": False,
                "tick_crossing": self._available,
                "fund_accounting": False,
            },
        }

    def match(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        quote: dict[str, Any],
        limit_price: Decimal | None = None,
        moment: datetime | None = None,
    ) -> PaperMatch:
        latest = self._decimal(quote.get("latest_price"))
        bid = self._positive_decimal(quote.get("bid_price_1"))
        ask = self._positive_decimal(quote.get("ask_price_1"))
        bid = bid or (latest * (Decimal("1") - self.half_spread))
        ask = ask or (latest * (Decimal("1") + self.half_spread))
        bid = bid.quantize(_MICRO, rounding=ROUND_HALF_UP)
        ask = ask.quantize(_MICRO, rounding=ROUND_HALF_UP)

        if self._available:
            try:
                return self._match_with_veighna(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    order_type=order_type,
                    limit_price=limit_price,
                    bid=bid,
                    ask=ask,
                    latest=latest,
                    moment=moment or datetime.now(UTC),
                )
            except Exception as exc:
                # A framework import/runtime issue must not make the paper
                # account unusable. The status endpoint exposes the fallback.
                self._available = False
                self._load_error = f"{type(exc).__name__}: {exc}"

        return self._compatible_match(
            side=side,
            order_type=order_type,
            limit_price=limit_price,
            bid=bid,
            ask=ask,
        )

    def _match_with_veighna(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        limit_price: Decimal | None,
        bid: Decimal,
        ask: Decimal,
        latest: Decimal,
        moment: datetime,
    ) -> PaperMatch:
        from vnpy.trader.constant import Direction, Exchange, Offset, OrderType, Product
        from vnpy.trader.object import ContractData, OrderRequest, TickData
        from vnpy_paperaccount.engine import EVENT_TRADE, PaperEngine

        exchange = self._exchange(symbol, Exchange)
        direction = Direction.LONG if side == "buy" else Direction.SHORT
        offset = Offset.OPEN if side == "buy" else Offset.CLOSE
        vn_order_type = OrderType.MARKET if order_type == "market" else OrderType.LIMIT
        request = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            type=vn_order_type,
            volume=quantity,
            price=float(limit_price or latest),
            offset=offset,
        )
        order = request.create_order_data("COGNITRADE", "PAPER")
        contract = ContractData(
            gateway_name="COGNITRADE",
            symbol=symbol,
            exchange=exchange,
            name=symbol,
            product=Product.EQUITY,
            size=1,
            pricetick=0.01,
            min_volume=100,
            net_position=True,
        )
        tick = TickData(
            gateway_name="COGNITRADE",
            symbol=symbol,
            exchange=exchange,
            datetime=moment,
            last_price=float(latest),
            bid_price_1=float(bid),
            ask_price_1=float(ask),
        )

        class _ContractLookup:
            @staticmethod
            def get_contract(_vt_symbol: str):
                return contract

        # Use real bound methods on a minimal PaperEngine subclass. Assigning
        # lambdas to an object created via object.__new__ worked for the
        # no-fill branch, but VeighNa's filled branch calls both methods as
        # instance methods and some releases reject the unbound shims.
        class _HeadlessPaperEngine(PaperEngine):
            def __init__(self) -> None:
                self.main_engine = _ContractLookup()
                self.trade_slippage = 0
                self.events: list[tuple[str, Any]] = []

            def put_event(self, event_type: str, data: object) -> None:
                self.events.append((event_type, data))

            def update_position(self, trade: object, current_contract: object) -> None:
                del trade, current_contract

        # Position and fund state remain in Cognitrade's audited SQLite
        # transaction; only VeighNa's official crossing function runs here.
        engine = _HeadlessPaperEngine()
        PaperEngine.cross_order(engine, order, tick)
        trade = next(
            (data for event_type, data in engine.events if event_type == EVENT_TRADE),
            None,
        )
        fill = (
            Decimal(str(trade.price)).quantize(_MICRO, rounding=ROUND_HALF_UP)
            if trade is not None
            else None
        )
        return PaperMatch(
            status="filled" if fill is not None else "pending",
            fill_price=fill,
            bid_price=bid,
            ask_price=ask,
            engine=self.engine_name,
        )

    def _compatible_match(
        self,
        *,
        side: str,
        order_type: str,
        limit_price: Decimal | None,
        bid: Decimal,
        ask: Decimal,
    ) -> PaperMatch:
        fill: Decimal | None = None
        if order_type == "market":
            fill = ask if side == "buy" else bid
        elif limit_price is not None:
            if side == "buy" and limit_price >= ask:
                fill = ask
            elif side == "sell" and limit_price <= bid:
                fill = bid
        return PaperMatch(
            status="filled" if fill is not None else "pending",
            fill_price=fill,
            bid_price=bid,
            ask_price=ask,
            engine="cognitrade-compatible",
        )

    @staticmethod
    def _exchange(symbol: str, exchange_type):
        if symbol.startswith(("4", "8", "92")):
            return exchange_type.BSE
        if symbol.startswith(("5", "6", "9")):
            return exchange_type.SSE
        return exchange_type.SZSE

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError("latest quote price must be positive")
        return result

    @staticmethod
    def _positive_decimal(value: Any) -> Decimal | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            result = Decimal(str(value))
        except Exception:
            return None
        return result if result.is_finite() and result > 0 else None


__all__ = ["PaperMatch", "VeighNaPaperAdapter"]
