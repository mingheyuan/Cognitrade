"""UZI 3.9.2 reality-check and A-share seat-range capability.

This is a pure DomainPack migration of the logic from UZI source commit
``fce996c33e70eddce8e375f53cd252b549eb3d7c``.  It intentionally has no
dependency on an installed UZI runtime.
"""

from __future__ import annotations

import os
from typing import Any

from domain_packs.finance_ashare.subagents.uzi_panel_profiles import get_profile
from domain_packs.finance_ashare.subagents.uzi_panel_registry import PANEL_REGISTRY


SCHOOL_LABELS = {
    "A": "价值派",
    "B": "成长派",
    "C": "宏观派",
    "D": "技术派",
    "E": "中国价投",
    "F": "A 股游资",
    "G": "量化",
    "H": "科技领袖派",
    "I": "AI 卡位/瓶颈猎手",
}

PANEL_BY_ID = {str(row["id"]): dict(row) for row in PANEL_REGISTRY}

MARKET_SCOPE: dict[str, str] = {
    **{str(row["id"]): "all" for row in PANEL_REGISTRY},
    **{
        investor_id: "A"
        for investor_id in (
            "zhang_mz", "zhao_lg", "fs_wyj", "bj_cj", "yangjia", "sun_ge",
            "chen_xq", "hu_jl", "fang_xx", "zuoshou", "xiao_ey", "jiao_yy",
            "mao_lb", "xiao_xian", "lasa", "chengdu", "sunan", "ningbo_st",
            "liuyi_zl", "liu_sh", "gu_bl", "wang_zr", "xin_dd", "ghzw",
        )
    },
    "thorp": "US",
}

KNOWN_HOLDINGS: dict[str, list[tuple[str, str, str]]] = {
    "buffett": [
        ("AAPL", "held", "伯克希尔第一大持仓，2016年起买入，多次加仓"),
        ("苹果", "held", "同上，中文名匹配"),
        ("KO", "held", "可口可乐，1988年起持有至今"),
        ("BAC", "held", "美国银行，大量持仓"),
        ("OXY", "held", "西方石油，2022年大举买入"),
        ("BYD", "held", "比亚迪，2008年买入港股"),
        ("比亚迪", "held", "同上"),
        ("AXP", "held", "美国运通"),
        ("MCO", "held", "穆迪"),
        ("DVA", "held", "达维塔"),
        ("茅台", "bullish_known", "芒格/段永平圈子都认可，巴菲特对白酒生意模式评价很高"),
    ],
    "munger": [
        ("BABA", "held", "2021年买入阿里巴巴，后减仓"),
        ("阿里", "held", "同上"),
        ("BYD", "held", "比亚迪投资来自芒格推荐"),
        ("比亚迪", "held", "同上"),
        ("好市多", "held", "Costco 董事会成员"),
        ("COST", "held", "同上"),
    ],
    "duan": [
        ("AAPL", "held", "段永平2011年前后买入苹果，长期持有"),
        ("苹果", "held", "同上"),
        ("茅台", "held", "贵州茅台长期持仓"),
        ("600519", "held", "同上"),
        ("腾讯", "held", "长期持有腾讯"),
        ("00700", "held", "同上"),
        ("GOOG", "held", "持有谷歌"),
    ],
    "zhangkun": [
        ("茅台", "held", "易方达蓝筹精选重仓"),
        ("600519", "held", "同上"),
        ("五粮液", "held", "重仓白酒"),
        ("000858", "held", "同上"),
        ("泸州老窖", "held", "重仓"),
        ("腾讯", "held", "港股重仓"),
        ("美团", "held", "港股持仓"),
    ],
    "fengliu": [
        ("海康", "held", "冯柳重仓海康威视"),
        ("002415", "held", "同上"),
        ("紫金矿业", "held", "周期股代表持仓"),
    ],
    "wood": [
        ("TSLA", "held", "ARK 第一大持仓"),
        ("特斯拉", "held", "同上"),
        ("COIN", "held", "加密货币交易所"),
        ("ROKU", "held", "流媒体"),
        ("SQ", "held", "Block/Square"),
        ("RKLB", "held", "火箭实验室 — 太空"),
        ("量子", "bullish_known", "ARK 看好量子计算赛道"),
        ("基因", "bullish_known", "ARK Genomic Revolution"),
        ("AI", "bullish_known", "ARK 五大平台之一"),
    ],
    "lynch": [("消费", "bullish_known", "林奇最擅长在日常消费中发现 tenbagger")],
    "soros": [("NVDA", "held", "索罗斯基金持有英伟达")],
}

INDUSTRY_AFFINITY: dict[str, dict[str, list[str]]] = {
    "buffett": {
        "love": ["消费", "保险", "银行", "金融", "饮料", "食品", "零售", "能源", "白酒"],
        "hate": ["生物科技", "半导体", "量子", "加密", "区块链", "mRNA"],
        "note": "能力圈：消费品 + 金融 + 能源。科技股只买苹果（特例）",
    },
    "graham": {
        "love": ["银行", "保险", "公用事业", "铁路", "基础设施"],
        "hate": ["科技", "互联网", "AI", "半导体", "量子", "生物"],
        "note": "只看低PE低PB的传统行业",
    },
    "wood": {
        "love": ["AI", "人工智能", "量子", "基因", "机器人", "自动驾驶", "电池", "锂电", "新能源", "储能", "3D打印", "太空", "卫星", "区块链", "数字货币", "mRNA", "脑机", "半导体", "软件", "云"],
        "hate": ["银行", "保险", "石油", "煤炭", "钢铁", "地产", "白酒"],
        "note": "只看颠覆式创新，传统行业完全不碰",
    },
    "thiel": {
        "love": ["AI", "太空", "量子", "生物科技", "加密", "国防", "数据"],
        "hate": ["传统制造", "零售", "地产"],
        "note": "0到1，只看垄断性创新",
    },
    "lynch": {
        "love": ["消费", "零售", "餐饮", "医药", "成长"],
        "hate": [],
        "note": "逛商场选股，关注日常生活能接触到的公司",
    },
    "duan": {
        "love": ["消费电子", "互联网", "白酒", "平台"],
        "hate": ["周期", "化工", "钢铁", "航运"],
        "note": "段永平：看得懂的好生意，长期持有",
    },
    "soros": {
        "love": ["宏观", "外汇", "商品", "金融", "科技"],
        "hate": [],
        "note": "宏观对冲，什么都做但核心是宏观趋势",
    },
}

YOUZI_FIT_RULES: dict[str, dict[str, Any]] = {
    "章盟主": {"min_mcap": 20_000_000_000, "trend": "up", "style_match": "trend"},
    "孙哥": {"min_mcap": 10_000_000_000, "is_sector_leader": True},
    "赵老哥": {"is_first_or_second_board": True, "is_sector_leader": True},
    "佛山无影脚": {"max_mcap": 8_000_000_000, "is_oversold": True},
    "炒股养家": {"sentiment_cycle": True},
    "陈小群": {"is_sector_leader": True, "is_hot_theme": True},
    "呼家楼": {"is_hottest_in_sector": True},
    "方新侠": {"min_turnover": 1_000_000_000, "trend": "up"},
    "作手新一": {"is_sector_leader": True},
    "小鳄鱼": {"min_fundamental_score": 70},
    "交易猿": {"min_mcap": 15_000_000_000, "is_sector_leader": True},
    "毛老板": {"is_ai_theme": True, "min_mcap": 10_000_000_000},
    "消闲派": {"is_accelerating": True},
    "拉萨天团": {"short_term_only": True},
    "成都帮": {"is_oversold": True},
    "苏南帮": {"max_mcap": 5_000_000_000},
    "宁波桑田路": {"is_continuous_limit_up": True},
    "六一中路": {"is_hot_theme": True, "is_sector_leader": True},
    "流沙河": {"is_hot_theme": True},
    "古北路": {"is_sector_leader": True},
    "北京炒家": {"min_mcap": 2_000_000_000, "max_mcap": 8_000_000_000, "is_first_board": True, "max_institution_pct": 10},
    "瑞鹤仙": {"is_hot_theme": True, "is_sector_leader": True},
    "鑫多多": {"is_hot_theme": True, "is_first_or_second_board": True},
}

YOUZI_SEAT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "章盟主": ("国泰君安证券股份有限公司上海江苏路证券营业部", "国泰君安证券股份有限公司宁波彩虹北路证券营业部", "中信证券股份有限公司杭州延安路证券营业部"),
    "孙哥": ("中信证券股份有限公司上海溧阳路证券营业部", "中信证券股份有限公司上海古北路证券营业部", "中信证券股份有限公司上海分公司"),
    "赵老哥": ("浙商证券股份有限公司绍兴解放北路证券营业部", "中国银河证券股份有限公司绍兴证券营业部", "中国银河证券股份有限公司北京阜成路证券营业部"),
    "佛山无影脚": ("光大证券股份有限公司佛山绿景路证券营业部", "光大证券股份有限公司佛山季华六路证券营业部", "湘财证券股份有限公司佛山祖庙路证券营业部"),
    "炒股养家": ("华鑫证券有限责任公司上海红宝石路证券营业部", "华鑫证券有限责任公司上海宛平南路证券营业部"),
    "陈小群": ("中国银河证券股份有限公司大连黄河路证券营业部",),
    "呼家楼": ("中信证券股份有限公司上海凯滨路证券营业部", "中信证券股份有限公司北京总部", "中信建投证券股份有限公司北京朝外大街证券营业部"),
    "方新侠": ("兴业证券股份有限公司陕西分公司", "中信证券股份有限公司西安朱雀大街证券营业部"),
    "作手新一": ("国泰君安证券股份有限公司南京太平南路证券营业部",),
    "小鳄鱼": ("南京证券股份有限公司南京大钟亭证券营业部", "中金财富证券有限公司南京龙蟠中路证券营业部"),
    "交易猿": ("华泰证券股份有限公司天津东丽开发区二纬路证券营业部", "招商证券股份有限公司福州六一中路证券营业部"),
    "毛老板": ("国泰君安证券股份有限公司北京光华路证券营业部", "方正证券股份有限公司乐山龙游路证券营业部", "广发证券股份有限公司上海东方路证券营业部"),
    "消闲派": ("华泰证券股份有限公司浙江分公司",),
    "拉萨天团": ("东方财富证券股份有限公司拉萨",),
    "成都帮": ("华泰证券股份有限公司成都南一环路第二证券营业部",),
    "苏南帮": ("华泰证券股份有限公司无锡", "华泰证券股份有限公司镇江", "华泰证券股份有限公司南京"),
    "宁波桑田路": ("国盛证券有限责任公司宁波桑田路证券营业部",),
    "六一中路": ("招商证券股份有限公司福州六一中路证券营业部",),
    "流沙河": ("招商证券股份有限公司北京车公庄西路证券营业部", "华泰证券股份有限公司上海武定路证券营业部"),
    "古北路": ("中信证券股份有限公司上海古北路证券营业部",),
    "北京炒家": ("首板专精，无固定席位",),
    "瑞鹤仙": ("银河证券", "招商证券深圳"),
    "鑫多多": ("华鑫证券", "招商证券", "中信证券"),
}

FALLBACK_YOUZI_MAX_MCAP_YUAN = 50_000_000_000
MEGA_CAP_ALLOWLIST = frozenset({"章盟主"})


def get_locked_school() -> str:
    raw = (os.environ.get("UZI_SCHOOL") or "").strip().upper()
    return raw if raw in SCHOOL_LABELS else ""


def investor_group(investor_id: str) -> str:
    return str(PANEL_BY_ID.get(investor_id, {}).get("group") or "")


def investor_name(investor_id: str) -> str:
    return str(PANEL_BY_ID.get(investor_id, {}).get("name") or "")


def decision_profile(investor_id: str) -> dict[str, str]:
    return get_profile(investor_id, investor_group(investor_id))


def market_match(investor_id: str, market: str) -> bool:
    scope = MARKET_SCOPE.get(investor_id, "all")
    return scope == "all" or str(market).upper() in scope.upper()


def check_known_holdings(investor_id: str, ticker: str, name: str) -> tuple[str, str] | None:
    for pattern, attitude, note in KNOWN_HOLDINGS.get(investor_id, []):
        if pattern in ticker or pattern in name:
            return attitude, note
    return None


def compute_affinity(investor_id: str, industry: str, name: str = "") -> int:
    info = INDUSTRY_AFFINITY.get(investor_id)
    if not info:
        return 0
    text = f"{industry} {name}".lower()
    love_hits = sum(1 for keyword in info.get("love", []) if keyword.lower() in text)
    hate_hits = sum(1 for keyword in info.get("hate", []) if keyword.lower() in text)
    return min(10, love_hits * 4) - min(10, hate_hits * 5)


def reality_check(investor_id: str, market: str, ticker: str, name: str, industry: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "should_evaluate": True,
        "skip_reason": None,
        "holding_match": None,
        "affinity_adjust": 0,
        "override_signal": None,
    }
    if not market_match(investor_id, market):
        result["should_evaluate"] = False
        result["skip_reason"] = f"不看{market}市场"
        return result
    holding = check_known_holdings(investor_id, ticker, name)
    if holding:
        result["holding_match"] = holding
        if holding[0] in ("held", "bullish_known"):
            result["override_signal"] = "bullish"
            result["affinity_adjust"] = 15
    result["affinity_adjust"] += compute_affinity(investor_id, industry, name)
    return result


def _is_in_youzi_range(nickname: str, features: dict[str, Any]) -> bool:
    if nickname not in YOUZI_FIT_RULES:
        return False
    rules = YOUZI_FIT_RULES[nickname]
    market_cap = features.get("market_cap", 0) or 0
    if "min_mcap" in rules and market_cap < rules["min_mcap"]:
        return False
    if "max_mcap" in rules and market_cap > rules["max_mcap"]:
        return False
    if "max_mcap" not in rules and nickname not in MEGA_CAP_ALLOWLIST and market_cap > FALLBACK_YOUZI_MAX_MCAP_YUAN:
        return False
    for key, expected in rules.items():
        if key.startswith(("min_", "max_")):
            continue
        if key in features and features[key] != expected:
            return False
    return True


def is_youzi_out_of_range(investor_id: str, features: dict[str, Any]) -> tuple[bool, str]:
    if investor_group(investor_id) != "F":
        return False, ""
    nickname = investor_name(investor_id)
    if not nickname or nickname not in YOUZI_FIT_RULES:
        return False, ""
    market_cap = features.get("market_cap") or 0
    if not market_cap and features.get("market_cap_yi"):
        market_cap = float(features["market_cap_yi"]) * 1e8
    probe = dict(features)
    probe["market_cap"] = market_cap
    if _is_in_youzi_range(nickname, probe):
        return False, ""
    matched = features.get("matched_youzi") or []
    if nickname in matched:
        return False, ""
    return True, f"市值 {market_cap / 1e8 if market_cap else 0:.0f} 亿不在 {nickname} 射程"


def match_youzi_seats(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Mirror UZI 3.9.2's substring matching for disclosed LHB seats."""

    matches: dict[str, list[dict[str, Any]]] = {}
    for nickname, keywords in YOUZI_SEAT_KEYWORDS.items():
        hits = [
            row
            for row in records
            if any(keyword in " ".join(str(value) for value in row.values()) for keyword in keywords)
        ]
        if hits:
            matches[nickname] = hits
    return matches


__all__ = [
    "SCHOOL_LABELS",
    "decision_profile",
    "get_locked_school",
    "investor_group",
    "is_youzi_out_of_range",
    "match_youzi_seats",
    "reality_check",
]
