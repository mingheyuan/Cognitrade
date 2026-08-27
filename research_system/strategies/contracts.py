from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from research_system.strategies.trading_calendar import next_trading_day


class StrategyLifecycle(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class GovernanceState(StrEnum):
    PUBLISHED = "published"
    VERIFIED = "verified"
    PROMOTED = "promoted"


class SignalState(StrEnum):
    ALLOW_BUY = "ALLOW_BUY"
    WATCH = "WATCH"
    AVOID = "AVOID"
    BLOCKED = "BLOCKED"


class BacktestMode(StrEnum):
    FUND_ONLY = "fund_only"
    HOLDINGS_PORTFOLIO = "holdings_portfolio"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    COMPLETED_PARTIAL = "completed_partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReportSetStatus(StrEnum):
    BUILDING = "building"
    FINAL = "final"
    FINAL_PARTIAL = "final_partial"
    BLOCKED = "blocked"


Weight = Annotated[float, Field(ge=0.0, le=1.0)]
Percent = Annotated[float, Field(ge=0.0, le=100.0)]


REGISTERED_FUND_FACTORS = frozenset(
    {
        "year_1_return",
        "since_inception_annualized",
        "benchmark_excess_year_1",
        "max_drawdown",
        "volatility",
        "fee",
        "return_consistency",
    }
)
REGISTERED_HOLDING_FACTORS = frozenset({"uzi_overall_score"})
REGISTERED_STRUCTURE_FACTORS = frozenset(
    {
        "count_coverage",
        "weight_coverage",
        "disclosure_freshness",
        "concentration_risk",
        "lookthrough_completeness",
        "unsupported_weight",
    }
)


class StrategyMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    description: str = Field(default="", max_length=2000)


class StrategyPins(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uzi_methodology_version: str = "3.9.2"
    uzi_source_commit: str = "repository-current"
    model_id: str = "workspace-default"
    model_parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    prompt_hash: str = "domain-pack-managed"
    output_schema_versions: dict[str, str] = Field(
        default_factory=lambda: {
            "fund_snapshot": "1",
            "fund_analysis": "1",
            "uzi_native_analysis": "1",
            "research_report": "1",
        }
    )
    snapshot_schema_version: str = "1"
    factor_extractor_version: str = "fund-composite-v1"

    @field_validator(
        "uzi_methodology_version",
        "uzi_source_commit",
        "model_id",
        "prompt_hash",
        "snapshot_schema_version",
        "factor_extractor_version",
    )
    @classmethod
    def non_empty_pin(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("策略发布固定项不能为空")
        return normalized


class ReportInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holding_uzi_required: Literal[True] = True
    research_profile: Literal["lite", "medium", "deep"] = "deep"
    top_n: int = Field(default=10, ge=5, le=20)
    independent_holding_signals_enabled: bool = True
    enabled_backtest_modes: list[BacktestMode] = Field(
        default_factory=lambda: [BacktestMode.FUND_ONLY, BacktestMode.HOLDINGS_PORTFOLIO]
    )
    supported_fund_types: list[str] = Field(
        default_factory=lambda: [
            "股票型",
            "偏股混合型",
            "A股指数型",
            "ETF",
            "ETF联接",
            "LOF",
            "可穿透FOF",
        ]
    )

    @field_validator("enabled_backtest_modes")
    @classmethod
    def unique_modes(cls, value: list[BacktestMode]) -> list[BacktestMode]:
        return list(dict.fromkeys(value))

    @field_validator("supported_fund_types")
    @classmethod
    def normalize_fund_types(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        if not normalized:
            raise ValueError("至少需要一种受支持基金类型")
        return normalized


class FundCompositeWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_performance_risk: Weight = 0.40
    holding_stock_quality: Weight = 0.40
    holding_structure_quality: Weight = 0.20

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "FundCompositeWeights":
        total = self.fund_performance_risk + self.holding_stock_quality + self.holding_structure_quality
        if abs(total - 1.0) > 1e-8:
            raise ValueError("基金复合模型三个模块权重之和必须等于 1")
        return self


class FactorWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_performance_risk: dict[str, Weight] = Field(
        default_factory=lambda: {
            "year_1_return": 0.20,
            "since_inception_annualized": 0.15,
            "benchmark_excess_year_1": 0.15,
            "max_drawdown": 0.20,
            "volatility": 0.15,
            "fee": 0.05,
            "return_consistency": 0.10,
        }
    )
    holding_stock_quality: dict[str, Weight] = Field(
        default_factory=lambda: {"uzi_overall_score": 1.0}
    )
    holding_structure_quality: dict[str, Weight] = Field(
        default_factory=lambda: {
            "count_coverage": 0.25,
            "weight_coverage": 0.25,
            "disclosure_freshness": 0.20,
            "concentration_risk": 0.10,
            "lookthrough_completeness": 0.10,
            "unsupported_weight": 0.10,
        }
    )

    @model_validator(mode="after")
    def registered_and_normalized(self) -> "FactorWeights":
        groups = (
            ("fund_performance_risk", self.fund_performance_risk, REGISTERED_FUND_FACTORS),
            ("holding_stock_quality", self.holding_stock_quality, REGISTERED_HOLDING_FACTORS),
            ("holding_structure_quality", self.holding_structure_quality, REGISTERED_STRUCTURE_FACTORS),
        )
        for label, weights, registered in groups:
            unknown = set(weights) - registered
            if unknown:
                raise ValueError(f"{label} 包含未登记因子: {', '.join(sorted(unknown))}")
            if not weights or abs(sum(weights.values()) - 1.0) > 1e-8:
                raise ValueError(f"{label} 因子权重之和必须等于 1")
        return self


class SignalThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buy: Percent = 70.0
    watch: Percent = 55.0
    minimum_confidence: Weight = 0.65
    missing_report_carry_days: int = Field(default=3, ge=0, le=20)

    @model_validator(mode="after")
    def ordered_thresholds(self) -> "SignalThresholds":
        if self.watch >= self.buy:
            raise ValueError("观察阈值必须低于买入阈值")
        return self


class QualityGates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normal_coverage: Weight = 0.80
    minimum_coverage: Weight = 0.60
    max_holding_staleness_days: int = Field(default=120, ge=1, le=730)
    require_report_allowed: bool = True
    require_no_critical: bool = True
    require_symbol_match: bool = True
    point_in_time_required: Literal[True] = True

    @model_validator(mode="after")
    def ordered_coverage(self) -> "QualityGates":
        if self.minimum_coverage >= self.normal_coverage:
            raise ValueError("最低 coverage 必须低于正常 coverage")
        return self


class ExecutionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    technical_rule: Literal["ma_cross"] = "ma_cross"
    fast_period: int = Field(default=5, ge=2, le=120)
    slow_period: int = Field(default=20, ge=3, le=250)
    max_single_stock_pct: Percent = 20.0
    rebalance_drift_pct_points: Percent = 2.0

    @model_validator(mode="after")
    def valid_ma_periods(self) -> "ExecutionRules":
        if self.fast_period >= self.slow_period:
            raise ValueError("快均线周期必须小于慢均线周期")
        if self.max_single_stock_pct <= 0:
            raise ValueError("单只股票仓位上限必须大于 0")
        return self


class StockExecutionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board_lot: int = Field(default=100, ge=1)
    commission_rate: Weight = 0.00025
    minimum_commission: float = Field(default=5.0, ge=0.0)
    stamp_duty_rate: Weight = 0.0005
    transfer_fee_rate: Weight = 0.00001
    settlement: Literal["T+1"] = "T+1"


class ExchangeFundExecutionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board_lot: int = Field(default=100, ge=1)
    commission_rate: Weight = 0.00025
    minimum_commission: float = Field(default=5.0, ge=0.0)
    settlement: Literal["T+1"] = "T+1"


class OtcFundExecutionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscription_fee_rate_fallback: Weight = 0.0015
    redemption_fee_rate_fallback: Weight = 0.005
    subscription_confirmation_days: int = Field(default=1, ge=1, le=10)
    redemption_confirmation_days: int = Field(default=2, ge=1, le=20)
    require_open_status: bool = True
    missing_open_status_policy: Literal["assume_open_degraded", "block"] = "assume_open_degraded"
    # Percentages are expressed as human-facing 0..100 values, matching
    # ExecutionRules.max_single_stock_pct.  The defaults deliberately preserve
    # every already-published strategy version's historical all-in behaviour.
    max_position_pct: float = Field(default=100.0, gt=0.0, le=100.0)
    entry_tranche_pct: float = Field(default=100.0, gt=0.0, le=100.0)
    max_entry_tranches: int = Field(default=1, ge=1, le=100)
    minimum_entry_interval_days: int = Field(default=0, ge=0, le=250)


class AssetExecutionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock: StockExecutionRules = Field(default_factory=StockExecutionRules)
    exchange_fund: ExchangeFundExecutionRules = Field(default_factory=ExchangeFundExecutionRules)
    otc_fund: OtcFundExecutionRules = Field(default_factory=OtcFundExecutionRules)


class ScheduleDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    local_time: str = Field(default="16:30", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    settlement_cutoff: str = Field(default="08:30", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    skip_non_trading_days: Literal[True] = True


class StrategyDocumentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: StrategyMetadata = Field(default_factory=StrategyMetadata)
    pins: StrategyPins = Field(default_factory=StrategyPins)
    report_inputs: ReportInputs = Field(default_factory=ReportInputs)
    fund_composite: FundCompositeWeights = Field(default_factory=FundCompositeWeights)
    factor_weights: FactorWeights = Field(default_factory=FactorWeights)
    signal_thresholds: SignalThresholds = Field(default_factory=SignalThresholds)
    quality_gates: QualityGates = Field(default_factory=QualityGates)
    execution: ExecutionRules = Field(default_factory=ExecutionRules)
    asset_execution: AssetExecutionRules = Field(default_factory=AssetExecutionRules)
    schedule_defaults: ScheduleDefaults = Field(default_factory=ScheduleDefaults)


SYSTEM_TEMPLATE_KEY = "equity_fund_report_v1"
SYSTEM_TEMPLATE_NAME = "权益基金日报复合策略 V1"


def blank_strategy_document(*, description: str = "") -> StrategyDocumentV1:
    return StrategyDocumentV1(metadata=StrategyMetadata(description=description))


def system_template_document() -> StrategyDocumentV1:
    return blank_strategy_document(
        description="基金表现/风险、持仓 UZI 质量与持仓结构质量的 40/40/20 复合策略。"
    )


def canonical_strategy_json(document: StrategyDocumentV1 | dict) -> str:
    validated = (
        document
        if isinstance(document, StrategyDocumentV1)
        else StrategyDocumentV1.model_validate(document)
    )
    return json.dumps(
        validated.model_dump(mode="json", exclude_none=False),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def strategy_content_hash(document: StrategyDocumentV1 | dict) -> str:
    return hashlib.sha256(canonical_strategy_json(document).encode("utf-8")).hexdigest()


def next_weekday(value: date) -> date:
    """Backward-compatible name for the next XSHG trading session."""

    return next_trading_day(value)


__all__ = [
    "BacktestMode",
    "GovernanceState",
    "JobStatus",
    "REGISTERED_FUND_FACTORS",
    "REGISTERED_HOLDING_FACTORS",
    "REGISTERED_STRUCTURE_FACTORS",
    "ReportSetStatus",
    "SYSTEM_TEMPLATE_KEY",
    "SYSTEM_TEMPLATE_NAME",
    "SignalState",
    "StrategyDocumentV1",
    "StrategyLifecycle",
    "blank_strategy_document",
    "canonical_strategy_json",
    "next_weekday",
    "strategy_content_hash",
    "system_template_document",
]
