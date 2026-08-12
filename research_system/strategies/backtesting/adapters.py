from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math

from research_system.strategies.contracts import StrategyDocumentV1


@dataclass(frozen=True)
class FeeBreakdown:
    commission: float = 0.0
    transfer_fee: float = 0.0
    stamp_duty: float = 0.0
    subscription_fee: float = 0.0
    redemption_fee: float = 0.0

    @property
    def total(self) -> float:
        return sum(
            (
                self.commission,
                self.transfer_fee,
                self.stamp_duty,
                self.subscription_fee,
                self.redemption_fee,
            )
        )

    def payload(self) -> dict[str, float]:
        return {
            "commission": round(self.commission, 6),
            "transfer_fee": round(self.transfer_fee, 6),
            "stamp_duty": round(self.stamp_duty, 6),
            "subscription_fee": round(self.subscription_fee, 6),
            "redemption_fee": round(self.redemption_fee, 6),
            "total": round(self.total, 6),
        }


class ExchangeExecutionAdapter:
    def __init__(
        self,
        *,
        board_lot: int,
        commission_rate: float,
        minimum_commission: float,
        transfer_fee_rate: float = 0.0,
        stamp_duty_rate: float = 0.0,
        model_id: str,
    ) -> None:
        self.board_lot = int(board_lot)
        self.commission_rate = float(commission_rate)
        self.minimum_commission = float(minimum_commission)
        self.transfer_fee_rate = float(transfer_fee_rate)
        self.stamp_duty_rate = float(stamp_duty_rate)
        self.model_id = model_id

    @classmethod
    def stock(cls, document: StrategyDocumentV1) -> "ExchangeExecutionAdapter":
        rules = document.asset_execution.stock
        return cls(
            board_lot=rules.board_lot,
            commission_rate=rules.commission_rate,
            minimum_commission=rules.minimum_commission,
            transfer_fee_rate=rules.transfer_fee_rate,
            stamp_duty_rate=rules.stamp_duty_rate,
            model_id="a_stock_v1",
        )

    @classmethod
    def exchange_fund(cls, document: StrategyDocumentV1) -> "ExchangeExecutionAdapter":
        rules = document.asset_execution.exchange_fund
        return cls(
            board_lot=rules.board_lot,
            commission_rate=rules.commission_rate,
            minimum_commission=rules.minimum_commission,
            model_id="exchange_fund_v1",
        )

    def fees(self, side: str, gross: float) -> FeeBreakdown:
        commission = max(self.minimum_commission, gross * self.commission_rate) if gross > 0 else 0.0
        return FeeBreakdown(
            commission=commission,
            transfer_fee=gross * self.transfer_fee_rate,
            stamp_duty=gross * self.stamp_duty_rate if side == "sell" else 0.0,
        )

    def affordable_quantity(self, cash: float, price: float, *, maximum_value: float | None = None) -> int:
        budget = min(float(cash), float(maximum_value)) if maximum_value is not None else float(cash)
        raw = math.floor(budget / price / self.board_lot) * self.board_lot
        quantity = max(0, raw)
        while quantity > 0:
            gross = quantity * price
            if gross + self.fees("buy", gross).total <= cash + 1e-8:
                return quantity
            quantity -= self.board_lot
        return 0

    def target_quantity(self, target_value: float, price: float) -> int:
        return max(0, math.floor(max(0.0, target_value) / price / self.board_lot) * self.board_lot)

    @staticmethod
    def can_sell(*, last_buy_date: date | None, trading_date: date) -> bool:
        return last_buy_date is None or last_buy_date < trading_date


@dataclass(frozen=True)
class OTCExecutionAdapter:
    subscription_fee_rate: float
    redemption_fee_rate: float
    subscription_confirmation_days: int
    redemption_confirmation_days: int
    require_open_status: bool
    model_id: str = "otc_fund_fallback_v1"
    degraded_estimate: bool = True

    @classmethod
    def from_document(cls, document: StrategyDocumentV1) -> "OTCExecutionAdapter":
        rules = document.asset_execution.otc_fund
        return cls(
            subscription_fee_rate=rules.subscription_fee_rate_fallback,
            redemption_fee_rate=rules.redemption_fee_rate_fallback,
            subscription_confirmation_days=rules.subscription_confirmation_days,
            redemption_confirmation_days=rules.redemption_confirmation_days,
            require_open_status=rules.require_open_status,
        )

    def subscription_fee(self, amount: float) -> FeeBreakdown:
        return FeeBreakdown(subscription_fee=max(0.0, amount) * self.subscription_fee_rate)

    def redemption_fee(self, gross: float) -> FeeBreakdown:
        return FeeBreakdown(redemption_fee=max(0.0, gross) * self.redemption_fee_rate)


__all__ = ["ExchangeExecutionAdapter", "FeeBreakdown", "OTCExecutionAdapter"]
