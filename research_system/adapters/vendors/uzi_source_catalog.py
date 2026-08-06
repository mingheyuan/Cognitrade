from __future__ import annotations

from typing import Final


# Exact source identifiers and metadata shipped by UZI-Skill 3.9.2.  The
# upstream registry is deliberately a catalogue (not a claim that every source
# returned data for a particular run).  Runtime adapters must still emit their
# actual source trace.
UZI_DIMENSION_MAP: Final[dict[str, tuple[str, ...]]] = {
    "0_basic": ("market",),
    "1_financials": ("fundamental",),
    "2_kline": ("market", "kline"),
    "3_macro": ("macro",),
    "4_peers": ("peers",),
    "5_chain": ("chain",),
    "6_research": ("research",),
    "7_industry": ("industry",),
    "8_materials": ("materials",),
    "9_futures": ("futures",),
    "10_valuation": ("valuation",),
    "11_governance": ("governance",),
    "12_capital_flow": ("capital_flow",),
    "13_policy": ("policy",),
    "14_moat": ("moat",),
    "15_events": ("events",),
    "16_lhb": ("lhb",),
    "17_sentiment": ("sentiment",),
    "18_trap": ("trap",),
    "19_contests": ("contests",),
}

UZI_MARKET_MAP: Final[dict[str, tuple[str, ...]]] = {
    "A": ("cn", "cn_equity"),
    "H": ("hk_equity",),
    "U": ("us_equity",),
}

# id, Chinese name, base URL, markets, upstream dimensions, tier, access,
# upstream health.  Keep this tuple synchronized with the pinned UZI commit.
UZI_SOURCE_ROWS: Final[tuple[tuple[object, ...], ...]] = (
    ("em_push2", "东方财富 push2", "https://push2.eastmoney.com/api/qt/stock/get", ("A", "H", "U"), ("0_basic", "2_kline", "10_valuation", "12_capital_flow"), 1, "http", "blocked_often"),
    ("em_quote", "东方财富 quote 页", "https://quote.eastmoney.com/", ("A", "H", "U"), ("0_basic", "2_kline"), 1, "http", "known_good"),
    ("em_data", "东方财富 data 子域", "https://data.eastmoney.com/", ("A",), ("4_peers", "7_industry", "11_governance", "12_capital_flow", "16_lhb", "15_events", "6_research"), 1, "http", "known_good"),
    ("xq_api", "雪球 akshare backend", "https://stock.xueqiu.com/", ("A", "H"), ("0_basic", "1_financials", "2_kline", "15_events", "17_sentiment"), 1, "akshare", "known_good"),
    ("tencent_qt", "腾讯行情 qt", "https://qt.gtimg.cn/", ("A", "H", "U"), ("0_basic", "2_kline"), 1, "http", "known_good"),
    ("sina_quote", "新浪财经行情", "https://finance.sina.com.cn/", ("A", "H", "U"), ("0_basic", "2_kline", "15_events"), 1, "http", "flaky"),
    ("cninfo", "巨潮资讯", "http://www.cninfo.com.cn/", ("A",), ("15_events", "7_industry", "1_financials"), 1, "http", "known_good"),
    ("hkexnews", "HKEXNews 港交所披露易", "https://www1.hkexnews.hk/", ("H",), ("15_events", "11_governance"), 1, "http", "known_good"),
    ("aastocks", "AASTOCKS 港股", "https://www.aastocks.com/", ("H",), ("0_basic", "4_peers", "12_capital_flow", "15_events"), 1, "http", "flaky"),
    ("cls", "财联社 7x24 电报", "https://www.cls.cn/", ("A", "H", "U"), ("15_events", "3_macro"), 1, "http", "known_good"),
    ("yicai", "第一财经", "https://www.yicai.com/", ("A", "H"), ("15_events", "3_macro", "7_industry"), 1, "http", "known_good"),
    ("wallstreetcn", "华尔街见闻", "https://wallstreetcn.com/", ("A", "H", "U"), ("3_macro", "17_sentiment"), 1, "http", "flaky"),
    ("cfi", "中财网", "https://quote.cfi.cn/", ("A",), ("0_basic", "1_financials", "15_events"), 1, "http", "known_good"),
    ("hexun", "和讯网", "https://stock.hexun.com/", ("A",), ("6_research", "15_events"), 1, "http", "known_good"),
    ("163money", "网易财经", "https://money.163.com/", ("A", "U"), ("0_basic", "15_events"), 1, "http", "known_good"),
    ("jrj", "金融界", "https://stock.jrj.com.cn/", ("A",), ("15_events", "7_industry"), 1, "http", "known_good"),
    ("investing", "Investing.com", "https://www.investing.com/", ("U",), ("3_macro", "9_futures"), 1, "http", "known_good"),
    ("mx_api", "东方财富妙想 Skills Hub", "https://mkapi2.dfcfs.com/finskillshub/", ("A", "H", "U"), ("0_basic", "1_financials", "15_events"), 1, "mx_api", "known_good"),
    ("akshare_lhb", "akshare 龙虎榜", "https://akshare.akfamily.xyz/", ("A",), ("16_lhb",), 1, "akshare", "known_good"),
    ("baostock", "BaoStock", "http://baostock.com/", ("A",), ("2_kline",), 1, "akshare", "known_good"),
    ("yfinance", "Yahoo Finance", "https://finance.yahoo.com/", ("U", "H"), ("0_basic", "1_financials", "2_kline"), 1, "akshare", "known_good"),
    ("ddgs", "DuckDuckGo 搜索", "https://duckduckgo.com/", ("A", "H", "U"), ("3_macro", "13_policy", "14_moat", "15_events", "17_sentiment"), 1, "ddgs", "flaky"),
    ("yahoo_chart_v8", "Yahoo Finance Chart v8 (HTTP)", "https://query1.finance.yahoo.com/v8/finance/chart/", ("U", "H"), ("2_kline",), 1, "http", "known_good"),
    ("tencent_hk_quote", "腾讯港股实时 qt.gtimg.cn", "http://qt.gtimg.cn/q=hk00700", ("H",), ("0_basic",), 1, "http", "known_good"),
    ("coingecko_simple_price", "CoinGecko Simple Price", "https://api.coingecko.com/api/v3/simple/price", ("U",), ("3_macro",), 1, "http", "known_good"),
    ("coingecko_markets", "CoinGecko Markets", "https://api.coingecko.com/api/v3/coins/markets", ("U",), ("3_macro",), 1, "http", "known_good"),
    ("okx_spot_tickers", "OKX 现货 tickers (API v5)", "https://www.okx.com/api/v5/market/tickers?instType=SPOT", ("U",), ("3_macro",), 1, "http", "known_good"),
    ("kucoin_stats", "KuCoin 24h 统计", "https://api.kucoin.com/api/v1/market/stats", ("U",), ("3_macro",), 1, "http", "known_good"),
    ("kraken_trades", "Kraken 公开成交", "https://api.kraken.com/0/public/Trades", ("U",), ("3_macro",), 1, "http", "known_good"),
    ("gemini_ticker", "Gemini 行情", "https://api.gemini.com/v2/ticker/btcusd", ("U",), ("3_macro",), 1, "http", "known_good"),
    ("coinlore_tickers", "CoinLore 全量币种", "https://api.coinlore.net/api/tickers/", ("U",), ("3_macro",), 1, "http", "known_good"),
    ("geckoterminal_networks", "GeckoTerminal DEX Networks", "https://api.geckoterminal.com/api/v2/networks", ("U",), ("3_macro",), 1, "http", "known_good"),
    ("jin10_flash", "金十数据实时快讯", "https://www.jin10.com/flash_newest.js", ("A", "H", "U"), ("3_macro", "13_policy", "15_events", "17_sentiment"), 1, "http", "known_good"),
    ("em_kuaixun", "东财快讯 (kuaixun) · 类财联社", "https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html", ("A", "H", "U"), ("15_events", "17_sentiment"), 1, "http", "known_good"),
    ("em_stock_ann", "东财上市公司公告", "https://np-anotice-stock.eastmoney.com/api/security/ann", ("A",), ("15_events",), 1, "http", "known_good"),
    ("qh99_inventory", "99 期货网 · 库存/现货/基差", "https://www.99qh.com/", ("A",), ("8_materials", "9_futures"), 1, "http", "known_good"),
    ("cfachina", "中国期货业协会", "http://www.cfachina.org/", ("A",), ("9_futures", "13_policy"), 1, "http", "known_good"),
    ("ths_news_today", "同花顺今日财经快讯", "http://news.10jqka.com.cn/today_list/", ("A", "H"), ("15_events", "17_sentiment"), 1, "http", "known_good"),
    ("iwencai", "问财（同花顺 NLP 筛选）", "https://www.iwencai.com/", ("A",), ("4_peers", "5_chain", "7_industry"), 2, "playwright", "needs_browser"),
    ("ths_f10", "同花顺 F10", "https://stockpage.10jqka.com.cn/", ("A", "H"), ("0_basic", "4_peers", "5_chain", "11_governance", "14_moat"), 2, "playwright", "needs_browser"),
    ("xueqiu_f10", "雪球 F10 / 讨论", "https://xueqiu.com/", ("A", "H", "U"), ("11_governance", "15_events", "17_sentiment"), 2, "playwright", "needs_browser"),
    ("legulegu", "乐咕乐股估值历史", "https://legulegu.com/", ("A",), ("10_valuation", "7_industry"), 2, "playwright", "needs_browser"),
    ("stockstar", "证券之星", "https://stock.stockstar.com/", ("A",), ("6_research", "15_events"), 2, "playwright", "needs_browser"),
    ("futu", "富途牛牛", "https://www.futunn.com/", ("H", "U"), ("0_basic", "1_financials", "17_sentiment"), 2, "playwright", "needs_browser"),
    ("yuncaijing", "云财经龙虎榜", "https://www.yuncaijing.com/", ("A",), ("16_lhb",), 2, "playwright", "flaky"),
    ("guba_em_list", "东财股吧 list 页（按股票代码）", "https://guba.eastmoney.com/list,{code}.html", ("A", "H"), ("17_sentiment", "18_trap", "19_contests"), 2, "http", "known_good"),
    ("jisilu", "集思录", "https://www.jisilu.cn/", ("A",), ("17_sentiment", "19_contests"), 2, "ddgs", "flaky"),
    ("fx678", "汇通财经", "https://www.fx678.com/", ("A", "U"), ("3_macro", "8_materials", "9_futures"), 2, "ddgs", "flaky"),
    ("cmc", "CompaniesMarketCap", "https://companiesmarketcap.com/", ("H", "U"), ("0_basic", "10_valuation"), 2, "http", "known_good"),
    ("sse", "上海证券交易所", "https://www.sse.com.cn/", ("A",), ("15_events", "11_governance"), 3, "http", "known_good"),
    ("szse", "深圳证券交易所", "https://www.szse.cn/", ("A",), ("15_events", "11_governance"), 3, "http", "known_good"),
    ("csrc", "中国证监会", "http://www.csrc.gov.cn/", ("A",), ("13_policy",), 3, "http", "known_good"),
    ("gov_cn", "国务院政策", "https://www.gov.cn/zhengce/", ("A", "H"), ("13_policy", "3_macro"), 3, "http", "known_good"),
    ("miit", "工信部", "https://www.miit.gov.cn/", ("A",), ("13_policy", "7_industry"), 3, "http", "known_good"),
    ("ndrc", "发改委", "https://www.ndrc.gov.cn/", ("A",), ("13_policy", "3_macro"), 3, "http", "known_good"),
    ("samr", "市场监管总局", "https://www.samr.gov.cn/", ("A",), ("13_policy",), 3, "http", "known_good"),
    ("shfe", "上海期货交易所", "https://www.shfe.com.cn/", ("A",), ("8_materials", "9_futures"), 3, "http", "known_good"),
    ("dce", "大连商品交易所", "https://www.dce.com.cn/", ("A",), ("8_materials", "9_futures"), 3, "http", "known_good"),
    ("czce", "郑州商品交易所", "https://www.czce.com.cn/", ("A",), ("8_materials", "9_futures"), 3, "http", "known_good"),
    ("100ppi", "生意社现货", "https://www.100ppi.com/", ("A",), ("8_materials",), 3, "http", "known_good"),
    ("cnstock", "中国证券网", "https://www.cnstock.com/", ("A", "H"), ("15_events", "6_research", "17_sentiment", "13_policy"), 3, "ddgs", "known_good"),
    ("cs_cn", "中证网", "https://www.cs.com.cn/", ("A", "H"), ("15_events", "13_policy", "17_sentiment"), 3, "ddgs", "known_good"),
    ("stcn", "证券时报", "https://www.stcn.com/", ("A", "H"), ("15_events", "17_sentiment", "13_policy"), 3, "ddgs", "known_good"),
    ("nbd", "每日经济新闻", "https://www.nbd.com.cn/", ("A", "H", "U"), ("15_events", "17_sentiment", "18_trap", "14_moat"), 3, "ddgs", "known_good"),
    ("pbc", "中国人民银行", "http://www.pbc.gov.cn/", ("A", "H"), ("3_macro", "13_policy"), 3, "ddgs", "known_good"),
    ("safe", "国家外汇管理局", "https://www.safe.gov.cn/", ("A", "H", "U"), ("3_macro", "13_policy"), 3, "ddgs", "known_good"),
    ("stats_gov", "国家统计局", "http://www.stats.gov.cn/", ("A", "H"), ("3_macro", "7_industry"), 3, "ddgs", "known_good"),
    ("chinamoney", "中国货币网", "https://www.chinamoney.com.cn/", ("A",), ("3_macro", "12_capital_flow"), 3, "ddgs", "known_good"),
    ("chinabond", "中国债券信息网", "https://yield.chinabond.com.cn/", ("A",), ("3_macro", "10_valuation"), 3, "http", "known_good"),
    ("ine", "上海国际能源交易中心", "https://www.ine.cn/", ("A",), ("8_materials", "9_futures"), 3, "http", "known_good"),
)


def mapped_dimensions(upstream_dims: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(dim for key in upstream_dims for dim in UZI_DIMENSION_MAP.get(key, ())))


def mapped_markets(upstream_markets: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(market for key in upstream_markets for market in UZI_MARKET_MAP.get(key, ())))
