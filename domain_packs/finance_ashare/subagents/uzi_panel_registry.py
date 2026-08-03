"""Versioned UZI investor-panel registry owned by the Finance Pack.

The registry is metadata, not a claim that these are live investor opinions.
The evaluator uses the declared source dimensions to produce auditable
methodology perspectives.
"""

from __future__ import annotations


UZI_PANEL_VERSION = "3.9.2-adapted"
UZI_PANEL_SOURCE_COMMIT = "fce996c33e70eddce8e375f53cd252b549eb3d7c"


PANEL_REGISTRY: tuple[dict[str, object], ...] = (
    {"id": "buffett", "name": "巴菲特", "group": "A", "fields": ["1_financials", "10_valuation", "11_governance", "14_moat"]},
    {"id": "graham", "name": "格雷厄姆", "group": "A", "fields": ["1_financials", "10_valuation"]},
    {"id": "fisher", "name": "费雪", "group": "A", "fields": ["1_financials", "4_peers", "11_governance", "14_moat"]},
    {"id": "munger", "name": "芒格", "group": "A", "fields": ["1_financials", "10_valuation", "11_governance", "14_moat"]},
    {"id": "templeton", "name": "邓普顿", "group": "A", "fields": ["10_valuation", "3_macro"]},
    {"id": "klarman", "name": "卡拉曼", "group": "A", "fields": ["10_valuation", "11_governance", "15_events"]},
    {"id": "lynch", "name": "彼得·林奇", "group": "B", "fields": ["1_financials", "7_industry", "10_valuation"]},
    {"id": "oneill", "name": "欧奈尔", "group": "B", "fields": ["1_financials", "2_kline", "12_capital_flow", "15_events"]},
    {"id": "thiel", "name": "彼得·蒂尔", "group": "B", "fields": ["4_peers", "7_industry", "14_moat"]},
    {"id": "wood", "name": "木头姐", "group": "B", "fields": ["7_industry", "13_policy", "14_moat"]},
    {"id": "andreessen", "name": "马克·安德森", "group": "B", "fields": ["7_industry", "14_moat", "13_policy"]},
    {"id": "gurley", "name": "比尔·格利", "group": "B", "fields": ["7_industry", "14_moat", "1_financials"]},
    {"id": "naval", "name": "纳瓦尔", "group": "B", "fields": ["14_moat", "11_governance", "7_industry"]},
    {"id": "gerstner", "name": "布拉德·格斯特纳", "group": "B", "fields": ["7_industry", "1_financials", "10_valuation"]},
    {"id": "chamath", "name": "查马斯", "group": "B", "fields": ["7_industry", "12_capital_flow", "17_sentiment"]},
    {"id": "soros", "name": "索罗斯", "group": "C", "fields": ["3_macro", "12_capital_flow", "17_sentiment"]},
    {"id": "dalio", "name": "达里奥", "group": "C", "fields": ["3_macro", "13_policy"]},
    {"id": "marks", "name": "霍华德·马克斯", "group": "C", "fields": ["10_valuation", "17_sentiment", "3_macro"]},
    {"id": "druck", "name": "德鲁肯米勒", "group": "C", "fields": ["3_macro", "12_capital_flow"]},
    {"id": "robertson", "name": "罗伯逊", "group": "C", "fields": ["4_peers", "1_financials"]},
    {"id": "burry", "name": "迈克尔·伯利", "group": "C", "fields": ["3_macro", "10_valuation", "17_sentiment", "18_trap"]},
    {"id": "chanos", "name": "吉姆·查诺斯", "group": "C", "fields": ["11_governance", "1_financials", "18_trap"]},
    {"id": "livermore", "name": "利弗莫尔", "group": "D", "fields": ["2_kline", "15_events"]},
    {"id": "minervini", "name": "米内尔维尼", "group": "D", "fields": ["2_kline", "1_financials"]},
    {"id": "darvas", "name": "达瓦斯", "group": "D", "fields": ["2_kline"]},
    {"id": "gann", "name": "江恩", "group": "D", "fields": ["2_kline"]},
    {"id": "duan", "name": "段永平", "group": "E", "fields": ["1_financials", "10_valuation", "11_governance", "14_moat"]},
    {"id": "zhangkun", "name": "张坤", "group": "E", "fields": ["1_financials", "14_moat"]},
    {"id": "zhushaoxing", "name": "朱少醒", "group": "E", "fields": ["1_financials", "7_industry"]},
    {"id": "xiezhiyu", "name": "谢治宇", "group": "E", "fields": ["1_financials", "10_valuation"]},
    {"id": "fengliu", "name": "冯柳", "group": "E", "fields": ["10_valuation", "17_sentiment", "15_events"]},
    {"id": "dengxiaofeng", "name": "邓晓峰", "group": "E", "fields": ["1_financials", "5_chain", "7_industry"]},
    {"id": "zhang_lei", "name": "张磊", "group": "E", "fields": ["14_moat", "7_industry", "1_financials", "11_governance"]},
    {"id": "zhang_mz", "name": "章盟主", "group": "F", "fields": ["2_kline", "12_capital_flow", "16_lhb"]},
    {"id": "sun_ge", "name": "孙哥", "group": "F", "fields": ["2_kline", "16_lhb"]},
    {"id": "zhao_lg", "name": "赵老哥", "group": "F", "fields": ["2_kline", "15_events", "16_lhb"]},
    {"id": "fs_wyj", "name": "佛山无影脚", "group": "F", "fields": ["2_kline", "16_lhb"]},
    {"id": "yangjia", "name": "炒股养家", "group": "F", "fields": ["2_kline", "17_sentiment"]},
    {"id": "chen_xq", "name": "陈小群", "group": "F", "fields": ["2_kline", "15_events", "16_lhb"]},
    {"id": "hu_jl", "name": "呼家楼", "group": "F", "fields": ["16_lhb", "12_capital_flow"]},
    {"id": "fang_xx", "name": "方新侠", "group": "F", "fields": ["2_kline", "12_capital_flow"]},
    {"id": "zuoshou", "name": "作手新一", "group": "F", "fields": ["2_kline", "16_lhb"]},
    {"id": "xiao_ey", "name": "小鳄鱼", "group": "F", "fields": ["1_financials", "2_kline", "16_lhb"]},
    {"id": "jiao_yy", "name": "交易猿", "group": "F", "fields": ["2_kline", "12_capital_flow", "16_lhb"]},
    {"id": "mao_lb", "name": "毛老板", "group": "F", "fields": ["2_kline", "7_industry", "16_lhb"]},
    {"id": "xiao_xian", "name": "消闲派", "group": "F", "fields": ["2_kline", "16_lhb"]},
    {"id": "lasa", "name": "拉萨天团", "group": "F", "fields": ["16_lhb", "17_sentiment"]},
    {"id": "chengdu", "name": "成都帮", "group": "F", "fields": ["2_kline", "16_lhb"]},
    {"id": "sunan", "name": "苏南帮", "group": "F", "fields": ["16_lhb"]},
    {"id": "ningbo_st", "name": "宁波桑田路", "group": "F", "fields": ["2_kline", "16_lhb"]},
    {"id": "liuyi_zl", "name": "六一中路", "group": "F", "fields": ["2_kline", "15_events", "16_lhb"]},
    {"id": "liu_sh", "name": "流沙河", "group": "F", "fields": ["2_kline", "16_lhb"]},
    {"id": "gu_bl", "name": "古北路", "group": "F", "fields": ["16_lhb", "12_capital_flow"]},
    {"id": "bj_cj", "name": "北京炒家", "group": "F", "fields": ["2_kline", "15_events", "16_lhb"]},
    {"id": "wang_zr", "name": "瑞鹤仙", "group": "F", "fields": ["2_kline", "16_lhb"]},
    {"id": "xin_dd", "name": "鑫多多", "group": "F", "fields": ["2_kline", "15_events", "16_lhb"]},
    {"id": "ghzw", "name": "股海贼王", "group": "F", "fields": ["2_kline", "15_events", "16_lhb", "7_industry", "17_sentiment"]},
    {"id": "simons", "name": "西蒙斯", "group": "G", "fields": ["2_kline", "9_futures"]},
    {"id": "thorp", "name": "索普", "group": "G", "fields": ["10_valuation", "1_financials"]},
    {"id": "shaw", "name": "大卫·肖", "group": "G", "fields": ["1_financials", "2_kline", "10_valuation"]},
    {"id": "asness", "name": "克利夫·阿斯尼斯", "group": "G", "fields": ["10_valuation", "1_financials", "2_kline"]},
    {"id": "jensen_huang", "name": "黄仁勋", "group": "H", "fields": ["7_industry", "5_chain", "14_moat", "4_peers"]},
    {"id": "musk", "name": "马斯克", "group": "H", "fields": ["7_industry", "14_moat", "13_policy", "15_events"]},
    {"id": "altman", "name": "山姆·奥特曼", "group": "H", "fields": ["7_industry", "14_moat", "5_chain", "11_governance"]},
    {"id": "saylor", "name": "迈克尔·塞勒", "group": "H", "fields": ["3_macro", "10_valuation", "13_policy", "17_sentiment"]},
    {"id": "serenity", "name": "Serenity", "group": "I", "fields": ["5_chain", "7_industry", "14_moat", "13_policy", "15_events"]},
)


def panel_for_profile(profile: str) -> list[dict[str, object]]:
    count = {"quick": 5, "lite": 10, "medium": 66, "deep": 66}.get(str(profile), 10)
    return [dict(item) for item in PANEL_REGISTRY[:count]]


__all__ = [
    "PANEL_REGISTRY",
    "UZI_PANEL_SOURCE_COMMIT",
    "UZI_PANEL_VERSION",
    "panel_for_profile",
]
