from __future__ import annotations

from pathlib import Path

import yaml

from research_system.runtime.platform.settings import Settings
from research_system.services.routing.tables.route_table_models import IntentRouteRule, IntentRouteTable


def load_route_table_from_file(path: Path) -> IntentRouteTable:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return IntentRouteTable.model_validate(raw)


def build_default_intent_route_table() -> IntentRouteTable:
    return IntentRouteTable(
        version="default-v1",
        rules=[
            IntentRouteRule(rule_id="stock_code_regex", intent="stock_check", match_type="regex", pattern=r"\b\d{6}\b", priority=10, target="query"),
            IntentRouteRule(
                rule_id="stock_compare_cn",
                intent="stock_compare",
                match_type="regex",
                pattern=r"((对比|比较|哪个好|谁更值得|谁更强|哪个更好).*(\d{6}|[一-龥A-Za-z]+).*(\d{6}|[一-龥A-Za-z]+))|((\d{6}|[一-龥A-Za-z]+).*(\d{6}|[一-龥A-Za-z]+).*(对比|比较|哪个好|谁更值得|谁更强|哪个更好))",
                priority=15,
                target="query",
            ),
            IntentRouteRule(
                rule_id="stock_compare_en",
                intent="stock_compare",
                match_type="contains_any",
                keywords=["compare", "comparison", "which is better", "which is best", "better investment"],
                priority=16,
                target="lowered",
            ),
            IntentRouteRule(
                rule_id="stock_screen_cn",
                intent="stock_screen",
                match_type="contains_any",
                keywords=["推荐", "值得", "关注", "选股", "什么股票", "哪些股票"],
                priority=20,
                target="query",
            ),
            IntentRouteRule(
                rule_id="stock_screen_en",
                intent="stock_screen",
                match_type="contains_any",
                keywords=["watchlist", "screen", "candidate", "idea", "worth", "follow", "what stock"],
                priority=21,
                target="lowered",
            ),
            IntentRouteRule(
                rule_id="stock_check_cn",
                intent="stock_check",
                match_type="contains_any",
                keywords=["股票", "个股"],
                priority=30,
                target="query",
            ),
            IntentRouteRule(
                rule_id="stock_check_en",
                intent="stock_check",
                match_type="contains_any",
                keywords=["stock", "ticker"],
                priority=31,
                target="lowered",
            ),
            IntentRouteRule(
                rule_id="sector_research_cn",
                intent="sector_research",
                match_type="contains_any",
                keywords=["行业", "板块", "主题", "指数", "大盘"],
                priority=40,
                target="query",
            ),
            IntentRouteRule(
                rule_id="sector_research_en",
                intent="sector_research",
                match_type="contains_any",
                keywords=["sector", "theme", "industry", "index", "market"],
                priority=41,
                target="lowered",
            ),
            IntentRouteRule(
                rule_id="distill_kol_logic_cn",
                intent="distill_kol_logic",
                match_type="contains_any",
                keywords=["蒸馏", "雪球"],
                priority=50,
                target="query",
            ),
            IntentRouteRule(
                rule_id="distill_kol_logic_en",
                intent="distill_kol_logic",
                match_type="contains_any",
                keywords=["kol", "xueqiu", "logic", "status", "jsonl"],
                priority=51,
                target="lowered",
            ),
            IntentRouteRule(
                rule_id="review_thesis_cn",
                intent="review_thesis",
                match_type="contains_any",
                keywords=["复核", "后验"],
                priority=60,
                target="query",
            ),
            IntentRouteRule(
                rule_id="review_thesis_en",
                intent="review_thesis",
                match_type="contains_any",
                keywords=["review", "review_thesis", "posterior", "validate"],
                priority=61,
                target="lowered",
            ),
        ],
    )


def build_default_intent_route_table_from_settings(settings: Settings | None) -> IntentRouteTable:
    if settings is not None and settings.router_defaults_path.exists():
        try:
            return load_route_table_from_file(settings.router_defaults_path)
        except Exception:
            pass
    return build_default_intent_route_table()
