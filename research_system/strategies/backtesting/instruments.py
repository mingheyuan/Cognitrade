from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from research_system.strategies.errors import StrategyDomainError


class AssetType(StrEnum):
    STOCK = "stock"
    EXCHANGE_FUND = "exchange_fund"
    OTC_FUND = "otc_fund"
    INDEX = "index"


@dataclass(frozen=True)
class TradableInstrument:
    instrument_id: str
    code: str
    name: str
    asset_type: AssetType
    exchange: str
    currency: str = "CNY"
    price_precision: int = 4
    quantity_precision: int = 0
    board_lot: int = 100
    settlement_rule: str = "T+1"
    fee_model_id: str = ""
    calendar_id: str = "XSHG"

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value["asset_type"] = self.asset_type.value
        return value


class InstrumentResolver:
    _EXCLUDED_MARKERS = ("债", "货币", "商品", "黄金", "QDII", "海外")

    @classmethod
    def resolve_fund(cls, payload: dict[str, Any], *, code: str, name: str | None = None) -> TradableInstrument:
        fund_type = str(payload.get("fund_type") or payload.get("category") or "").strip()
        if any(marker in fund_type.upper() for marker in cls._EXCLUDED_MARKERS):
            raise StrategyDomainError(
                "unsupported_fund_type",
                f"首版不支持该基金类型：{fund_type or '未知'}",
                status_code=409,
            )
        normalized_code = str(code or "").strip()
        normalized_name = str(name or payload.get("fund_name") or normalized_code).strip()
        exchange_traded = cls._is_exchange_fund(normalized_code, fund_type, payload)
        if exchange_traded:
            exchange = "SH" if normalized_code.startswith(("5", "6", "9")) else "SZ"
            return TradableInstrument(
                instrument_id=f"{exchange}:{normalized_code}",
                code=normalized_code,
                name=normalized_name,
                asset_type=AssetType.EXCHANGE_FUND,
                exchange=exchange,
                board_lot=100,
                settlement_rule="T+1",
                fee_model_id="exchange_fund_v1",
            )
        return TradableInstrument(
            instrument_id=f"OTC:{normalized_code}",
            code=normalized_code,
            name=normalized_name,
            asset_type=AssetType.OTC_FUND,
            exchange="OTC",
            quantity_precision=4,
            board_lot=1,
            settlement_rule="subscription/redemption confirmation",
            fee_model_id="otc_fund_fallback_v1",
        )

    @staticmethod
    def resolve_stock(code: str, name: str | None = None) -> TradableInstrument:
        normalized = str(code or "").strip()
        if len(normalized) != 6 or not normalized.isdigit():
            raise StrategyDomainError("unsupported_holding_instrument", f"不支持的持仓证券代码：{code}")
        exchange = "SH" if normalized.startswith(("5", "6", "9")) else (
            "BJ" if normalized.startswith(("4", "8", "92")) else "SZ"
        )
        return TradableInstrument(
            instrument_id=f"{exchange}:{normalized}",
            code=normalized,
            name=str(name or normalized),
            asset_type=AssetType.STOCK,
            exchange=exchange,
            board_lot=100,
            settlement_rule="T+1",
            fee_model_id="a_stock_v1",
        )

    @staticmethod
    def resolve_index(code: str, name: str | None = None) -> TradableInstrument:
        normalized = str(code or "").strip().lower().removeprefix("sh").removeprefix("sz")
        if len(normalized) != 6 or not normalized.isdigit():
            raise StrategyDomainError("unsupported_benchmark", f"不支持的基准指数代码：{code}")
        exchange = "SH" if normalized.startswith(("0", "9")) else "SZ"
        return TradableInstrument(
            instrument_id=f"INDEX:{exchange}:{normalized}",
            code=normalized,
            name=str(name or normalized),
            asset_type=AssetType.INDEX,
            exchange=exchange,
            board_lot=1,
            settlement_rule="not_tradable",
            fee_model_id="benchmark_only",
        )

    @staticmethod
    def _is_exchange_fund(code: str, fund_type: str, payload: dict[str, Any]) -> bool:
        declared = str(payload.get("trading_venue") or payload.get("market") or "").upper()
        if declared in {"SH", "SZ", "SSE", "SZSE", "EXCHANGE"}:
            return True
        upper_type = fund_type.upper()
        if "ETF联接" in fund_type or "ETF FEEDER" in upper_type:
            return False
        if "ETF" in upper_type or "LOF" in upper_type:
            return code.startswith(("1", "5"))
        return False


__all__ = ["AssetType", "InstrumentResolver", "TradableInstrument"]
