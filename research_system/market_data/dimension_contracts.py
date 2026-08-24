from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research_system.market_data.contracts import CONTRACT_VERSION

VerifiedEmptyPolicy = Literal[
    "never",
    "bounded_zero",
    "market_not_applicable",
    "bounded_zero_or_market_not_applicable",
]
FieldValueType = Literal[
    "number",
    "string",
    "object",
    "collection",
    "date",
    "datetime",
]
NumberConstraint = Literal["any", "positive", "nonnegative"]


class AlternativeFieldRequirement(BaseModel):
    """One of the named row fields must exist and match the declared value rule."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    keys: tuple[str, ...]
    allowed_types: tuple[FieldValueType, ...] = ()
    number_constraint: NumberConstraint = "any"


class FieldValueRequirement(BaseModel):
    """Serializable top-level and collection-row shape/value contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    allowed_types: tuple[FieldValueType, ...]
    number_constraint: NumberConstraint = "any"
    item_allowed_types: tuple[FieldValueType, ...] = ()
    item_required_keys: tuple[str, ...] = ()
    item_field_types: dict[str, tuple[FieldValueType, ...]] = Field(
        default_factory=dict
    )
    item_number_constraints: dict[str, NumberConstraint] = Field(default_factory=dict)
    item_required_any: tuple[AlternativeFieldRequirement, ...] = ()


class ItemCountRequirement(BaseModel):
    """A mechanically checkable cardinality rule for one payload collection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    minimum: int | None = Field(default=None, ge=0)
    exact: int | None = Field(default=None, ge=0)
    zero_requires_verified_empty: bool = False
    alternative_coverage_claim: str | None = None
    empty_query_alternative: bool = False

    @model_validator(mode="after")
    def _one_cardinality(self) -> ItemCountRequirement:
        if (self.minimum is None) == (self.exact is None):
            raise ValueError(
                "item count requirement needs exactly one of minimum or exact"
            )
        return self


class ItemStatusRequirement(BaseModel):
    """Allowed states for every row of a payload collection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    status_field: str = "status"
    allowed_values: tuple[str, ...]
    id_field: str | None = None
    require_unique_ids: bool = False


class FieldQueryRequirement(BaseModel):
    """Proof that a field was queried, including legitimate empty subfields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    mode: Literal["always", "when_empty"] = "when_empty"
    allow_empty: bool = True
    allow_not_applicable: bool = False
    minimum_window_days: int = Field(default=0, ge=0)
    result_count_matches_payload: bool = True


class HistorySpanRequirement(BaseModel):
    """Minimum calendar coverage for a dated history collection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    minimum_days: int = Field(gt=0)
    minimum_points: int = Field(default=2, ge=2)
    date_fields: tuple[str, ...] = (
        "date",
        "point_at",
        "trade_date",
        "nav_date",
        "period",
        "reporting_period",
    )
    allow_query_window_alternative: bool = False


class CoverageValueRequirement(BaseModel):
    """An explicit, versioned acquisition claim stored in query_manifest.coverage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    allowed_values: tuple[str, ...]


class DimensionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: str
    ordinal: int = Field(ge=0)
    snapshot_type: str
    label: str
    description: str
    required_fields: tuple[str, ...]
    freshness_seconds: int = Field(gt=0)
    history_requirement: str
    min_evidence_count: int = Field(default=1, ge=1)
    verified_empty_policy: VerifiedEmptyPolicy = "never"
    verified_empty_requirement: str | None = None
    verified_empty_minimum_window_days: int = Field(default=0, ge=0)
    item_count_requirements: tuple[ItemCountRequirement, ...] = ()
    item_status_requirements: tuple[ItemStatusRequirement, ...] = ()
    field_query_requirements: tuple[FieldQueryRequirement, ...] = ()
    history_span_requirements: tuple[HistorySpanRequirement, ...] = ()
    coverage_value_requirements: tuple[CoverageValueRequirement, ...] = ()
    field_value_requirements: tuple[FieldValueRequirement, ...] = ()

    @property
    def permits_verified_empty(self) -> bool:
        return self.verified_empty_policy != "never"


class DimensionRegistry(Sequence[DimensionContract]):
    def __init__(
        self,
        contracts: Sequence[DimensionContract],
        *,
        contract_version: str = CONTRACT_VERSION,
    ) -> None:
        ordered = tuple(sorted(contracts, key=lambda item: item.ordinal))
        dimensions = [item.dimension for item in ordered]
        ordinals = [item.ordinal for item in ordered]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("dimension registry contains duplicate dimension IDs")
        if ordinals != list(range(len(ordered))):
            raise ValueError("dimension ordinals must be contiguous and start at zero")
        self._contracts = ordered
        self._by_dimension = {item.dimension: item for item in ordered}
        self.contract_version = contract_version

    def __len__(self) -> int:
        return len(self._contracts)

    def __getitem__(
        self, index: int | slice
    ) -> DimensionContract | tuple[DimensionContract, ...]:
        return self._contracts[index]

    def __iter__(self) -> Iterator[DimensionContract]:
        return iter(self._contracts)

    @property
    def expected_dimension_count(self) -> int:
        return len(self)

    @property
    def dimension_ids(self) -> tuple[str, ...]:
        return tuple(item.dimension for item in self._contracts)

    def get(self, dimension: str) -> DimensionContract:
        try:
            return self._by_dimension[dimension]
        except KeyError as exc:
            raise KeyError(f"unknown market-data dimension: {dimension}") from exc

    def contract_payload(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "expected_dimension_count": self.expected_dimension_count,
            "dimensions": [item.model_dump(mode="json") for item in self._contracts],
        }


def _contract(
    dimension: str,
    ordinal: int,
    snapshot_type: str,
    label: str,
    description: str,
    required_fields: tuple[str, ...],
    freshness_seconds: int,
    history_requirement: str,
    *,
    min_evidence_count: int = 1,
    empty: VerifiedEmptyPolicy = "never",
    empty_requirement: str | None = None,
    empty_window_days: int = 0,
    item_counts: tuple[ItemCountRequirement, ...] = (),
    item_statuses: tuple[ItemStatusRequirement, ...] = (),
    field_queries: tuple[FieldQueryRequirement, ...] = (),
    history_spans: tuple[HistorySpanRequirement, ...] = (),
    coverage_values: tuple[CoverageValueRequirement, ...] = (),
    field_values: tuple[FieldValueRequirement, ...] = (),
) -> DimensionContract:
    return DimensionContract(
        dimension=dimension,
        ordinal=ordinal,
        snapshot_type=snapshot_type,
        label=label,
        description=description,
        required_fields=required_fields,
        freshness_seconds=freshness_seconds,
        history_requirement=history_requirement,
        min_evidence_count=min_evidence_count,
        verified_empty_policy=empty,
        verified_empty_requirement=empty_requirement,
        verified_empty_minimum_window_days=empty_window_days,
        item_count_requirements=item_counts,
        item_status_requirements=item_statuses,
        field_query_requirements=field_queries,
        history_span_requirements=history_spans,
        coverage_value_requirements=coverage_values,
        field_value_requirements=field_values,
    )


def _value(
    path: str,
    *allowed_types: FieldValueType,
    number: NumberConstraint = "any",
    item_types: tuple[FieldValueType, ...] = (),
    item_required: tuple[str, ...] = (),
    item_fields: dict[str, tuple[FieldValueType, ...]] | None = None,
    item_numbers: dict[str, NumberConstraint] | None = None,
    item_any: tuple[AlternativeFieldRequirement, ...] = (),
) -> FieldValueRequirement:
    return FieldValueRequirement(
        path=path,
        allowed_types=allowed_types,
        number_constraint=number,
        item_allowed_types=item_types,
        item_required_keys=item_required,
        item_field_types=item_fields or {},
        item_number_constraints=item_numbers or {},
        item_required_any=item_any,
    )


# This order is the backend-owned UZI v1 acquisition contract.  The three
# report-derived dimensions (20/21/22) intentionally do not appear here.
DEFAULT_DIMENSION_CONTRACTS: tuple[DimensionContract, ...] = (
    _contract(
        "0_basic",
        0,
        "market",
        "基础行情",
        "标的身份、行业、价格、币种、市值与股本。",
        (
            "symbol",
            "market",
            "industry",
            "currency",
            "price",
            "market_cap",
            "shares_outstanding",
            "observed_at",
        ),
        3600,
        "cutoff 处的最新可见观测",
        field_values=(
            _value("symbol", "string"),
            _value("market", "string"),
            _value("industry", "string"),
            _value("currency", "string"),
            _value("price", "number", number="positive"),
            _value("market_cap", "number", number="nonnegative"),
            _value("shares_outstanding", "number", number="positive"),
            _value("observed_at", "datetime"),
        ),
    ),
    _contract(
        "1_financials",
        1,
        "fundamental",
        "财务报表",
        "已披露的三表、盈利和现金流历史。",
        ("periods",),
        86400,
        "最近六个已披露年期，或上市以来全部年期",
        item_counts=(
            ItemCountRequirement(
                path="periods",
                minimum=6,
                alternative_coverage_claim="financials_all_public_periods",
            ),
        ),
        field_values=(
            _value(
                "periods",
                "collection",
                item_types=("object",),
                item_required=("period",),
                item_fields={"period": ("string",)},
                item_any=(
                    AlternativeFieldRequirement(
                        keys=(
                            "revenue",
                            "net_profit",
                            "operating_cash_flow",
                            "cash_flow",
                        ),
                        allowed_types=("number",),
                    ),
                ),
            ),
        ),
    ),
    _contract(
        "2_kline",
        2,
        "kline",
        "K 线与公司行动",
        "未复权 OHLCV、公司行动和独立复权因子。",
        ("ohlcv", "corporate_actions", "adjustment_factors"),
        3600,
        "至少 260 个交易日",
        item_counts=(ItemCountRequirement(path="ohlcv", minimum=260),),
        field_queries=(
            FieldQueryRequirement(path="corporate_actions", mode="always"),
            FieldQueryRequirement(path="adjustment_factors", mode="always"),
        ),
        coverage_values=(
            CoverageValueRequirement(
                key="ohlcv_adjustment",
                allowed_values=("none", "unadjusted", "raw"),
            ),
        ),
        field_values=(
            _value(
                "ohlcv",
                "collection",
                item_types=("object",),
                item_required=("date", "open", "high", "low", "close", "volume"),
                item_fields={
                    "date": ("date",),
                    "open": ("number",),
                    "high": ("number",),
                    "low": ("number",),
                    "close": ("number",),
                    "volume": ("number",),
                },
                item_numbers={
                    "open": "positive",
                    "high": "positive",
                    "low": "positive",
                    "close": "positive",
                    "volume": "nonnegative",
                },
            ),
            _value(
                "corporate_actions",
                "collection",
                item_types=("object",),
                item_required=("date", "type"),
                item_fields={"date": ("date",), "type": ("string",)},
            ),
            _value(
                "adjustment_factors",
                "collection",
                item_types=("object",),
                item_required=("date", "factor"),
                item_fields={"date": ("date",), "factor": ("number",)},
                item_numbers={"factor": "positive"},
            ),
        ),
    ),
    _contract(
        "3_macro",
        3,
        "macro",
        "宏观环境",
        "截止日前一年宏观值和可追溯证据。",
        ("observations",),
        43200,
        "截止日前 365 日",
        min_evidence_count=2,
        field_queries=(
            FieldQueryRequirement(
                path="observations",
                mode="always",
                allow_empty=False,
                minimum_window_days=365,
            ),
        ),
        field_values=(
            _value("observations", "collection", item_types=("object", "string")),
        ),
    ),
    _contract(
        "4_peers",
        4,
        "peers",
        "可比公司",
        "目标公司和至少三家同口径可比公司。",
        ("target", "peers", "classification_version"),
        43200,
        "cutoff 可见的最新成分版本",
        item_counts=(ItemCountRequirement(path="peers", minimum=3),),
        field_values=(
            _value("target", "object"),
            _value(
                "peers",
                "collection",
                item_types=("object",),
                item_required=("currency",),
                item_fields={"currency": ("string",)},
                item_any=(
                    AlternativeFieldRequirement(
                        keys=("symbol", "ticker", "code"), allowed_types=("string",)
                    ),
                    AlternativeFieldRequirement(
                        keys=("reporting_period", "period"), allowed_types=("string",)
                    ),
                ),
            ),
            _value("classification_version", "string"),
        ),
    ),
    _contract(
        "5_chain",
        5,
        "chain",
        "产业链",
        "业务分部、上下游和集中度。",
        ("segments", "upstream", "downstream", "concentration"),
        43200,
        "最新已披露信息",
        field_values=(
            _value("segments", "collection", item_types=("object", "string")),
            _value("upstream", "object", "collection", "string"),
            _value("downstream", "object", "collection", "string"),
            _value("concentration", "object"),
        ),
    ),
    _contract(
        "6_research",
        6,
        "research",
        "研究资料",
        "固定来源近十二个月研报。",
        ("reports", "query_status"),
        43200,
        "近 12 个月固定来源",
        empty="bounded_zero",
        empty_requirement="固定来源全部成功且返回零份",
        empty_window_days=365,
        item_counts=(
            ItemCountRequirement(
                path="reports", minimum=1, zero_requires_verified_empty=True
            ),
        ),
        field_queries=(
            FieldQueryRequirement(
                path="reports", mode="always", minimum_window_days=365
            ),
        ),
        field_values=(
            _value("reports", "collection", item_types=("object",)),
            _value("query_status", "object"),
        ),
    ),
    _contract(
        "7_industry",
        7,
        "industry",
        "行业研究",
        "行业分类、估值、增长、TAM、渗透率和周期。",
        ("classification", "valuation", "growth", "tam", "penetration", "cycle"),
        43200,
        "cutoff 可见的最新行业口径",
        min_evidence_count=2,
        field_values=(
            _value("classification", "string", "object"),
            _value("valuation", "number", "object"),
            _value("growth", "number", "object"),
            _value("tam", "number", "object"),
            _value("penetration", "number", "object"),
            _value("cycle", "string", "object"),
        ),
    ),
    _contract(
        "8_materials",
        8,
        "materials",
        "原材料",
        "成本暴露与相关品种历史价格。",
        ("exposure", "prices"),
        43200,
        "相关品种至少 250 个交易日",
        empty="bounded_zero",
        empty_requirement="证据证明没有重大可交易原料暴露",
        item_counts=(
            ItemCountRequirement(
                path="prices", minimum=250, zero_requires_verified_empty=True
            ),
        ),
        field_values=(
            _value("exposure", "object"),
            _value("prices", "collection", item_types=("number", "object")),
        ),
    ),
    _contract(
        "9_futures",
        9,
        "futures",
        "期货映射",
        "相关合约映射和价格历史。",
        ("contract_mapping", "prices"),
        43200,
        "相关合约至少 60 个交易日",
        empty="bounded_zero",
        empty_requirement="证据证明没有相关上市期货",
        item_counts=(
            ItemCountRequirement(
                path="prices", minimum=60, zero_requires_verified_empty=True
            ),
        ),
        field_values=(
            _value("contract_mapping", "string", "object"),
            _value("prices", "collection", item_types=("number", "object")),
        ),
    ),
    _contract(
        "10_valuation",
        10,
        "valuation",
        "估值",
        "PE/PB、五年历史/分位、行业 PE、分母和股本。",
        (
            "pe",
            "pb",
            "history",
            "percentiles",
            "industry_pe",
            "denominators",
            "shares_outstanding",
        ),
        43200,
        "五年历史并按 cutoff 计算",
        history_spans=(
            HistorySpanRequirement(
                path="history",
                minimum_days=1825,
                minimum_points=5,
                allow_query_window_alternative=True,
            ),
        ),
        field_values=(
            _value("pe", "number"),
            _value("pb", "number"),
            _value("history", "collection", item_types=("object",)),
            _value("percentiles", "object"),
            _value("industry_pe", "number"),
            _value("denominators", "object"),
            _value("shares_outstanding", "number", number="positive"),
        ),
    ),
    _contract(
        "11_governance",
        11,
        "governance",
        "治理",
        "质押、增减持、处罚和实控人。",
        ("controller", "pledges", "ownership_changes", "penalties"),
        43200,
        "事件近 1 年",
        empty="bounded_zero",
        empty_requirement="规定来源全部成功且事件为零",
        empty_window_days=365,
        field_queries=(
            FieldQueryRequirement(
                path="pledges", mode="always", minimum_window_days=365
            ),
            FieldQueryRequirement(
                path="ownership_changes", mode="always", minimum_window_days=365
            ),
            FieldQueryRequirement(
                path="penalties", mode="always", minimum_window_days=365
            ),
        ),
        field_values=(
            _value("controller", "string", "object"),
            _value("pledges", "collection", item_types=("object",)),
            _value("ownership_changes", "collection", item_types=("object",)),
            _value("penalties", "collection", item_types=("object",)),
        ),
    ),
    _contract(
        "12_capital_flow",
        12,
        "capital_flow",
        "资金与持有人",
        "资金、北向、两融、户数、机构和事件。",
        (
            "money_flow",
            "northbound",
            "margin",
            "shareholder_counts",
            "institutions",
            "events",
        ),
        3600,
        "资金 20 日、户数 8 次、机构 8 季、事件前后 12 月",
        empty="market_not_applicable",
        empty_requirement="契约明确不支持该市场",
        item_counts=(
            ItemCountRequirement(path="money_flow", minimum=20),
            ItemCountRequirement(
                path="northbound", minimum=20, empty_query_alternative=True
            ),
            ItemCountRequirement(
                path="margin", minimum=20, empty_query_alternative=True
            ),
            ItemCountRequirement(path="shareholder_counts", minimum=8),
            ItemCountRequirement(path="institutions", minimum=8),
        ),
        field_queries=(
            FieldQueryRequirement(path="northbound", minimum_window_days=20),
            FieldQueryRequirement(path="margin", minimum_window_days=20),
            FieldQueryRequirement(path="events", minimum_window_days=365),
        ),
        field_values=(
            _value("money_flow", "collection", item_types=("object",)),
            _value("northbound", "collection", item_types=("object",)),
            _value("margin", "collection", item_types=("object",)),
            _value("shareholder_counts", "collection", item_types=("object",)),
            _value("institutions", "collection", item_types=("object",)),
            _value("events", "collection", item_types=("object",)),
        ),
    ),
    _contract(
        "13_policy",
        13,
        "policy",
        "政策",
        "有效的监管、扶持、补贴和反垄断政策。",
        ("policies",),
        86400,
        "近 365 日且 cutoff 时仍有效",
        min_evidence_count=2,
        field_queries=(
            FieldQueryRequirement(
                path="policies",
                mode="always",
                allow_empty=False,
                minimum_window_days=365,
            ),
        ),
        field_values=(
            _value("policies", "collection", item_types=("object", "string")),
        ),
    ),
    _contract(
        "14_moat",
        14,
        "moat",
        "护城河",
        "四因子和研发强度的证据化输入。",
        ("factors", "research_and_development"),
        43200,
        "最新已披露输入",
        field_values=(
            _value("factors", "object"),
            _value("research_and_development", "number", "object"),
        ),
    ),
    _contract(
        "15_events",
        15,
        "events",
        "事件",
        "公告与新闻完整来源及截断信息。",
        ("events", "query_status", "truncation"),
        86400,
        "截止日前 180 日",
        empty="bounded_zero",
        empty_requirement="规定来源全部成功且零事件",
        empty_window_days=180,
        item_counts=(
            ItemCountRequirement(
                path="events", minimum=1, zero_requires_verified_empty=True
            ),
        ),
        field_queries=(
            FieldQueryRequirement(
                path="events", mode="always", minimum_window_days=180
            ),
        ),
        field_values=(
            _value("events", "collection", item_types=("object", "string")),
            _value("query_status", "object"),
            _value("truncation", "object"),
        ),
    ),
    _contract(
        "16_lhb",
        16,
        "lhb",
        "龙虎榜",
        "目标证券所有上榜日和席位。",
        ("records", "query_status"),
        3600,
        "过去 30 个自然日",
        empty="bounded_zero_or_market_not_applicable",
        empty_requirement="A 股权威查询成功且零条；H/U 市场明确 N/A",
        empty_window_days=30,
        item_counts=(
            ItemCountRequirement(
                path="records", minimum=1, zero_requires_verified_empty=True
            ),
        ),
        field_queries=(
            FieldQueryRequirement(
                path="records", mode="always", minimum_window_days=30
            ),
        ),
        field_values=(
            _value("records", "collection", item_types=("object",)),
            _value("query_status", "object"),
        ),
    ),
    _contract(
        "17_sentiment",
        17,
        "sentiment",
        "市场情绪",
        "固定来源帖子、新闻及原始查询命中。",
        ("items", "query_status"),
        3600,
        "过去 30 日",
        empty="bounded_zero",
        empty_requirement="有限来源全部成功且零命中",
        empty_window_days=30,
        item_counts=(
            ItemCountRequirement(
                path="items", minimum=1, zero_requires_verified_empty=True
            ),
        ),
        field_queries=(
            FieldQueryRequirement(path="items", mode="always", minimum_window_days=30),
        ),
        field_values=(
            _value("items", "collection", item_types=("object", "string")),
            _value("query_status", "object"),
        ),
    ),
    _contract(
        "18_trap",
        18,
        "trap",
        "风险信号",
        "八个杀猪盘信号逐项事实状态。",
        ("signals",),
        43200,
        "八信号全部逐项评估",
        item_counts=(ItemCountRequirement(path="signals", exact=8),),
        item_statuses=(
            ItemStatusRequirement(
                path="signals",
                allowed_values=("hit", "not_hit"),
                id_field="id",
                require_unique_ids=True,
            ),
        ),
        field_values=(
            _value(
                "signals",
                "collection",
                item_types=("object",),
                item_required=("id", "status"),
                item_fields={
                    "id": ("number", "string"),
                    "status": ("string",),
                },
            ),
        ),
    ),
    _contract(
        "19_contests",
        19,
        "contests",
        "竞赛与榜单",
        "规定来源的比赛/榜单结果与授权状态。",
        ("results", "authorization_status", "query_status"),
        43200,
        "cutoff 时规定来源",
        empty="bounded_zero",
        empty_requirement="规定来源全部成功且零命中",
        item_counts=(
            ItemCountRequirement(
                path="results", minimum=1, zero_requires_verified_empty=True
            ),
        ),
        field_queries=(FieldQueryRequirement(path="results", mode="always"),),
        field_values=(
            _value("results", "collection", item_types=("object", "string")),
            _value("authorization_status", "string"),
            _value("query_status", "object"),
        ),
    ),
    _contract(
        "6_fund_holders",
        20,
        "fund_holders",
        "主动基金持仓",
        "季度主动基金全量及 Top100/五年 NAV 数据超集。",
        ("reporting_period", "funds", "top_holders", "nav_history"),
        7776000,
        "最新已公开季度，NAV/业绩五年",
        empty="bounded_zero_or_market_not_applicable",
        empty_requirement="权威季度查询成功零家；或契约明确市场 N/A",
        item_counts=(
            ItemCountRequirement(
                path="top_holders",
                minimum=100,
                zero_requires_verified_empty=True,
                alternative_coverage_claim="fund_holders_all_active_funds",
            ),
        ),
        history_spans=(
            HistorySpanRequirement(
                path="nav_history",
                minimum_days=1825,
                minimum_points=5,
                allow_query_window_alternative=True,
            ),
        ),
        field_values=(
            _value("reporting_period", "string"),
            _value("funds", "collection", item_types=("object",)),
            _value(
                "top_holders",
                "collection",
                item_types=("object",),
                item_any=(
                    AlternativeFieldRequirement(
                        keys=("fund_code", "code", "fund", "name"),
                        allowed_types=("string",),
                    ),
                ),
            ),
            _value("nav_history", "collection", item_types=("object", "number")),
        ),
    ),
    _contract(
        "similar_stocks",
        21,
        "similar_stocks",
        "相似股票",
        "版本化行业映射及 Top4 对标行情。",
        ("classification_version", "stocks"),
        86400,
        "cutoff 时 Top4 的价格/PE/市值",
        empty="market_not_applicable",
        empty_requirement="契约明确不支持该市场",
        item_counts=(
            ItemCountRequirement(
                path="stocks", exact=4, zero_requires_verified_empty=True
            ),
        ),
        field_values=(
            _value("classification_version", "string"),
            _value(
                "stocks",
                "collection",
                item_types=("object",),
                item_any=(
                    AlternativeFieldRequirement(
                        keys=("symbol", "ticker", "code"), allowed_types=("string",)
                    ),
                    AlternativeFieldRequirement(
                        keys=("price", "close"),
                        allowed_types=("number",),
                        number_constraint="positive",
                    ),
                    AlternativeFieldRequirement(
                        keys=("pe", "pe_ttm"), allowed_types=("number",)
                    ),
                    AlternativeFieldRequirement(
                        keys=("market_cap", "market_cap_yi"),
                        allowed_types=("number",),
                        number_constraint="nonnegative",
                    ),
                ),
            ),
        ),
    ),
)


DEFAULT_DIMENSION_REGISTRY = DimensionRegistry(DEFAULT_DIMENSION_CONTRACTS)
registry = DEFAULT_DIMENSION_REGISTRY
EXPECTED_DIMENSION_COUNT = registry.expected_dimension_count
DIMENSION_IDS = registry.dimension_ids


def get_dimension_contract(dimension: str) -> DimensionContract:
    return registry.get(dimension)


__all__ = [
    "DEFAULT_DIMENSION_CONTRACTS",
    "DEFAULT_DIMENSION_REGISTRY",
    "DIMENSION_IDS",
    "EXPECTED_DIMENSION_COUNT",
    "AlternativeFieldRequirement",
    "CoverageValueRequirement",
    "DimensionContract",
    "DimensionRegistry",
    "FieldQueryRequirement",
    "FieldValueRequirement",
    "FieldValueType",
    "HistorySpanRequirement",
    "ItemCountRequirement",
    "ItemStatusRequirement",
    "NumberConstraint",
    "VerifiedEmptyPolicy",
    "get_dimension_contract",
    "registry",
]
