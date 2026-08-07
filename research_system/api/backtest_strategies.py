from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class BacktestError(ValueError):
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


@dataclass(frozen=True)
class StrategyParameter:
    id: str
    label: str
    default: int
    minimum: int
    maximum: int
    step: int = 1
    help: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "type": "integer",
            "default": self.default,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "help": self.help,
        }


@dataclass(frozen=True)
class BacktestStrategy:
    id: str
    name: str
    description: str
    parameters: tuple[StrategyParameter, ...]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "parameters": [parameter.public() for parameter in self.parameters],
        }


STRATEGIES: dict[str, BacktestStrategy] = {
    "ma_cross": BacktestStrategy(
        id="ma_cross",
        name="双均线交叉",
        description="短周期均线上穿长周期均线买入，反向交叉卖出。",
        parameters=(
            StrategyParameter(
                id="fast_period",
                label="短周期",
                default=5,
                minimum=2,
                maximum=120,
                help="计算快速移动平均线的日 K 数量。",
            ),
            StrategyParameter(
                id="slow_period",
                label="长周期",
                default=20,
                minimum=3,
                maximum=250,
                help="必须大于短周期。",
            ),
        ),
    ),
    "price_ma_cross": BacktestStrategy(
        id="price_ma_cross",
        name="价格均线趋势",
        description="收盘价上穿移动平均线买入，下穿移动平均线卖出。",
        parameters=(
            StrategyParameter(
                id="lookback_period",
                label="均线周期",
                default=20,
                minimum=3,
                maximum=250,
                help="用于判断价格趋势的移动平均日 K 数量。",
            ),
        ),
    ),
}


def strategy_catalog() -> list[dict[str, Any]]:
    return [strategy.public() for strategy in STRATEGIES.values()]


def resolve_strategy(
    strategy_id: str,
    raw_parameters: dict[str, Any] | None,
) -> tuple[BacktestStrategy, dict[str, int]]:
    normalized_id = str(strategy_id or "").strip()
    strategy = STRATEGIES.get(normalized_id)
    if strategy is None:
        raise BacktestError(
            "unsupported_backtest_strategy",
            f"不支持的回测策略：{normalized_id or '空值'}",
            details={"available": list(STRATEGIES)},
        )

    supplied = dict(raw_parameters or {})
    allowed = {parameter.id for parameter in strategy.parameters}
    unknown = sorted(set(supplied) - allowed)
    if unknown:
        raise BacktestError(
            "invalid_strategy_parameters",
            f"策略 {strategy.name} 不接受参数：{'、'.join(unknown)}",
            details={"unknown": unknown, "available": sorted(allowed)},
        )

    resolved: dict[str, int] = {}
    for parameter in strategy.parameters:
        value = supplied.get(parameter.id, parameter.default)
        if isinstance(value, bool):
            raise BacktestError(
                "invalid_strategy_parameters",
                f"{parameter.label}必须是整数",
            )
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise BacktestError(
                "invalid_strategy_parameters",
                f"{parameter.label}必须是整数",
            ) from exc
        if float(value) != number or not parameter.minimum <= number <= parameter.maximum:
            raise BacktestError(
                "invalid_strategy_parameters",
                f"{parameter.label}必须在 {parameter.minimum} 到 {parameter.maximum} 之间",
            )
        resolved[parameter.id] = number

    if normalized_id == "ma_cross" and resolved["slow_period"] <= resolved["fast_period"]:
        raise BacktestError(
            "invalid_strategy_parameters",
            "双均线策略的长周期必须大于短周期",
        )
    return strategy, resolved


def minimum_required_bars(strategy: BacktestStrategy, parameters: dict[str, int]) -> int:
    del strategy
    return max(parameters.values(), default=1) + 5


__all__ = [
    "BacktestError",
    "BacktestStrategy",
    "STRATEGIES",
    "minimum_required_bars",
    "resolve_strategy",
    "strategy_catalog",
]
