from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DimensionTaxonomyEntry:
    canonical_name: str
    family: str
    category: str
    aliases: tuple[str, ...]
    patterns: tuple[str, ...]
    scenario_tags: tuple[str, ...]
    methodology_tags: tuple[str, ...]


def build_default_dimension_taxonomy() -> list[DimensionTaxonomyEntry]:
    return [
        DimensionTaxonomyEntry(
            canonical_name="AI",
            family="technology_ai",
            category="technology",
            aliases=("artificial intelligence", "人工智能"),
            patterns=(r"\bAI\b", r"artificial intelligence", r"人工智能"),
            scenario_tags=("technology", "efficiency"),
            methodology_tags=("technology", "productivity"),
        ),
        DimensionTaxonomyEntry(
            canonical_name="互联网",
            family="internet_platform",
            category="industry",
            aliases=("internet", "互聯網"),
            patterns=(r"internet", r"互联网", r"互聯網"),
            scenario_tags=("industry", "distribution"),
            methodology_tags=("industry",),
        ),
        DimensionTaxonomyEntry(
            canonical_name="创新药",
            family="innovation_drug",
            category="pipeline",
            aliases=("新药", "innovation drug"),
            patterns=(r"创新药", r"新药", r"innovation drug"),
            scenario_tags=("pipeline", "clinical", "regulation"),
            methodology_tags=("pipeline", "event_driven"),
        ),
        DimensionTaxonomyEntry(
            canonical_name="出海",
            family="global_expansion",
            category="strategy",
            aliases=("global expansion", "overseas expansion"),
            patterns=(r"出海", r"global expansion", r"overseas expansion"),
            scenario_tags=("strategy", "overseas"),
            methodology_tags=("strategy", "growth"),
        ),
        DimensionTaxonomyEntry(
            canonical_name="产品体验",
            family="product_experience",
            category="product",
            aliases=(),
            patterns=(r"产品体验",),
            scenario_tags=("product", "user_value"),
            methodology_tags=("quality",),
        ),
        DimensionTaxonomyEntry(
            canonical_name="效率",
            family="efficiency_improvement",
            category="product",
            aliases=(),
            patterns=(r"效率",),
            scenario_tags=("efficiency", "operations"),
            methodology_tags=("quality", "productivity"),
        ),
        DimensionTaxonomyEntry(
            canonical_name="成本",
            family="cost_control",
            category="economics",
            aliases=(),
            patterns=(r"成本",),
            scenario_tags=("economics", "margin"),
            methodology_tags=("economics",),
        ),
        DimensionTaxonomyEntry(
            canonical_name="用户习惯",
            family="user_habit",
            category="distribution",
            aliases=(),
            patterns=(r"用户习惯",),
            scenario_tags=("distribution", "retention"),
            methodology_tags=("quality",),
        ),
        DimensionTaxonomyEntry(
            canonical_name="临床",
            family="clinical_progress",
            category="pipeline",
            aliases=(),
            patterns=(r"临床",),
            scenario_tags=("clinical", "pipeline"),
            methodology_tags=("event_driven",),
        ),
        DimensionTaxonomyEntry(
            canonical_name="资金流入",
            family="capital_flow",
            category="capital",
            aliases=("capital inflow", "net inflow", "capital_inflow"),
            patterns=(r"资金流入", r"净流入", r"capital inflow", r"net inflow"),
            scenario_tags=("capital", "flow"),
            methodology_tags=("momentum", "liquidity"),
        ),
        DimensionTaxonomyEntry(
            canonical_name="板块热度",
            family="sector_sentiment",
            category="sentiment",
            aliases=("sector heat", "board heat", "sector_heat", "board_heat"),
            patterns=(r"板块热度", r"热度", r"sector heat", r"board heat"),
            scenario_tags=("sentiment", "theme"),
            methodology_tags=("momentum",),
        ),
    ]

