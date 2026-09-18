from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from domain_packs.finance_ashare.tools.snapshot_collector import _run_snapshot_skill
from research_system.adapters.vendors.akshare_pit_context_adapter import (
    AkSharePitContextAdapter,
)
from research_system.market_data.uzi_adapter import UziSnapshotAdapter


def _classification_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "新证券简称": "样本公司",
                "证券代码": "688012",
                "分类标准": "证监会行业分类标准（2012）",
                "分类标准编码": "008021",
                "行业编码": "C35",
                "行业门类": "制造业",
                "行业大类": "专用设备制造业",
                "行业中类": None,
                "行业次类": None,
                "变更日期": date(2019, 7, 3),
            },
            {
                "新证券简称": "样本公司",
                "证券代码": "688012",
                "分类标准": "申银万国行业分类标准",
                "分类标准编码": "008003",
                "行业编码": "S270108",
                "行业门类": "电子",
                "行业大类": "半导体设备",
                "行业中类": "半导体设备",
                "行业次类": "半导体",
                "变更日期": date(2021, 7, 30),
            },
            {
                "新证券简称": "样本公司",
                "证券代码": "688012",
                "分类标准": "申银万国行业分类标准",
                "分类标准编码": "008003",
                "行业编码": "S999999",
                "行业门类": "未来分类",
                "行业大类": "未来行业",
                "行业中类": "未来行业",
                "行业次类": "未来行业",
                "变更日期": date(2026, 9, 2),
            },
        ]
    )


def _industry_metrics(data_date: str, net_profit: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "变动日期": data_date,
                "行业编码": "C35",
                "行业名称": "专用设备制造业",
                "行业层级": 2,
                "公司数量": 400,
                "纳入计算公司数量": 320,
                "总市值-静态": 50_000,
                "净利润-静态": net_profit,
                "静态市盈率-加权平均": 45.0,
                "静态市盈率-中位数": 50.0,
                "静态市盈率-算术平均": 80.0,
            }
        ]
    )


def _business_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "股票代码": "688012",
                "报告日期": date(2026, 6, 30),
                "分类类型": "按产品分类",
                "主营构成": "未来半年报产品",
                "主营收入": 200,
                "收入比例": 1.0,
                "主营利润": 80,
                "毛利率": 0.4,
            },
            {
                "股票代码": "688012",
                "报告日期": date(2025, 12, 31),
                "分类类型": "按产品分类",
                "主营构成": "已披露年报产品",
                "主营收入": 100,
                "收入比例": 1.0,
                "主营利润": 35,
                "毛利率": 0.35,
            },
        ]
    )


def test_industry_archive_filters_future_classification_and_uses_cutoff_metrics() -> (
    None
):
    target = date(2026, 9, 1)

    def metrics(*, symbol: str, date: str):
        assert symbol == "证监会行业分类"
        if date == "20260901":
            return _industry_metrics("2026-09-01", 120)
        if date == "20250901":
            return _industry_metrics("2025-09-01", 100)
        return pd.DataFrame()

    with (
        patch(
            "research_system.adapters.vendors.akshare_pit_context_adapter.ak.stock_industry_change_cninfo",
            return_value=_classification_rows(),
        ),
        patch(
            "research_system.adapters.vendors.akshare_pit_context_adapter.ak.stock_industry_pe_ratio_cninfo",
            side_effect=metrics,
        ),
    ):
        result = AkSharePitContextAdapter().get_industry_snapshot(
            symbol="688012.SH",
            as_of_date=target,
        )

    assert result["industry"] == "半导体设备"
    assert result["classification"]["effective_date"] == "2021-07-30"
    assert all(
        row["effective_date"] <= target.isoformat()
        for row in result["classification"]["standards"]
    )
    assert result["valuation"]["data_date"] == "2026-09-01"
    assert result["growth"]["value_pct"] == 20.0
    assert result["cycle"]["stage"] == "expansion"
    assert result["availability"] == "available"
    assert result["historical_evidence"] == []
    assert [row["provider"] for row in result["structured_sources"]] == [
        "cninfo.industry_change", "cninfo.industry_pe",
    ]
    assert all(row["source_kind"] == "structured_api" and "url" not in row
               for row in result["structured_sources"])
    assert result["structured_sources"][1]["query"]["as_of_date"] == target.isoformat()


def test_chain_uses_only_report_visible_before_cutoff() -> None:
    announcements = pd.DataFrame(
        [
            {
                "代码": "688012",
                "公告标题": "2025年<em>年度报告</em>",
                "公告时间": "2026-03-31",
                "公告链接": "https://www.cninfo.com.cn/report-2025",
            }
        ]
    )
    with (
        patch(
            "research_system.adapters.vendors.akshare_pit_context_adapter.ak.stock_zygc_em",
            return_value=_business_rows(),
        ),
        patch(
            "research_system.adapters.vendors.akshare_pit_context_adapter.ak.stock_zh_a_disclosure_report_cninfo",
            return_value=announcements,
        ),
    ):
        result = AkSharePitContextAdapter().get_chain_snapshot(
            symbol="688012.SH",
            as_of_date=date(2026, 8, 15),
            industry_context={"industry": "半导体设备", "_source_trace": []},
        )

    assert result["report_date"] == "2025-12-31"
    assert result["published_at"] == "2026-03-31"
    assert result["publication_basis"] == "cninfo_actual_publication"
    assert result["main_business_breakdown"][0]["name"] == "已披露年报产品"
    assert result["upstream"][0]["company_specific"] is False
    assert result["concentration"]["metric"] == (
        "business_segment_revenue_concentration"
    )


def test_chain_promotes_half_year_only_after_actual_publication() -> None:
    announcements = pd.DataFrame(
        [
            {
                "代码": "688012",
                "公告标题": "2026年半<em>年度报告</em>",
                "公告时间": "2026-08-20",
                "公告链接": "https://www.cninfo.com.cn/report-2026-h1",
            }
        ]
    )
    with (
        patch(
            "research_system.adapters.vendors.akshare_pit_context_adapter.ak.stock_zygc_em",
            return_value=_business_rows(),
        ),
        patch(
            "research_system.adapters.vendors.akshare_pit_context_adapter.ak.stock_zh_a_disclosure_report_cninfo",
            return_value=announcements,
        ),
    ):
        result = AkSharePitContextAdapter().get_chain_snapshot(
            symbol="688012.SH",
            as_of_date=date(2026, 9, 1),
            industry_context={"industry": "半导体设备", "_source_trace": []},
        )

    assert result["report_date"] == "2026-06-30"
    assert result["published_at"] == "2026-08-20"
    assert result["main_business_breakdown"][0]["name"] == "未来半年报产品"


def test_historical_chain_and_industry_enter_real_pit_path() -> None:
    target = date(2026, 9, 1)
    cutoff = datetime(2026, 9, 1, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    industry_facts = {
        "industry": "半导体设备",
        "classification": {"name": "半导体设备", "effective_date": "2021-07-30"},
        "valuation": {"pe_weighted": 45, "data_date": "2026-09-01"},
        "growth": {"value_pct": 20, "current_date": "2026-09-01"},
        "cycle": {"stage": "expansion", "as_of_date": "2026-09-01"},
        "availability": "available",
        "data_gap": [],
        "_source": {
            "vendor": "akshare_cninfo_pit",
            "provider_id": "akshare_cninfo_pit",
            "source_id": "cninfo.industry_change",
        },
        "_source_trace": [
            {"source_id": "cninfo.industry_change", "status": "available", "count": 1}
        ],
    }
    chain_facts = {
        "main_business_raw": [
            {
                "主营构成": "半导体设备",
                "报告日期": "2025-12-31",
                "published_at": "2026-03-31",
            }
        ],
        "upstream": [{"name": "半导体材料"}],
        "downstream": [{"name": "晶圆制造"}],
        "concentration": {"hhi": 1.0},
        "availability": "available",
        "data_gap": [],
        "_source": {
            "vendor": "akshare_cninfo_pit",
            "provider_id": "akshare_cninfo_pit",
            "source_id": "em_data.stock_zygc",
        },
        "_source_trace": [
            {"source_id": "em_data.stock_zygc", "status": "available", "count": 1}
        ],
    }
    with (
        patch.object(
            AkSharePitContextAdapter,
            "get_industry_snapshot",
            return_value=industry_facts,
        ),
        patch.object(
            AkSharePitContextAdapter,
            "get_chain_snapshot",
            return_value=chain_facts,
        ),
    ):
        industry = _run_snapshot_skill(
            "样本公司", "688012.SH", "industry", None, {}, None, target, cutoff
        )
        chain = _run_snapshot_skill(
            "样本公司", "688012.SH", "chain", None, {}, None, target, cutoff
        )

    for snapshot in (industry, chain):
        assert snapshot["quality_status"] == "available"
        assert snapshot["historical_reconstruction"]["safe"] is True
        assert snapshot["historical_reconstruction"]["requested_as_of_date"] == (
            "2026-09-01"
        )
        assert "historical_provider_contract_missing" not in str(snapshot)

    adapted_industry = UziSnapshotAdapter().adapt(
        industry,
        symbol="688012.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=target,
    )
    adapted_chain = UziSnapshotAdapter().adapt(
        chain,
        symbol="688012.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=target,
    )
    assert adapted_industry.query_manifest["collection_outcome"] == "partial"
    assert adapted_industry.query_manifest["field_coverage"][
        "current_only_fields"
    ] == ["tam", "penetration"]
    assert adapted_chain.query_manifest["collection_outcome"] == "available"
    assert adapted_chain.payload["segments"][0]["主营构成"] == "半导体设备"
