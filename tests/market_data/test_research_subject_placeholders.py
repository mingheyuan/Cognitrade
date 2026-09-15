"""Offline regressions for fallback labels used as company relevance proof."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import (
    ArchiveCandidate,
    ArchiveEvidenceError,
    _relevance_basis,
)
from research_system.market_data.research_collection import recover_research_documents
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import AS_OF, _input, _payload


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("subject tests must not call a source or model")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("requests.Session.request", forbidden)


# Exact excerpts read from the completed 01M27SWGQX1HF362C9HB5KFMD2 archive.
# Both original metadata.relevance.matched_subject_terms were ["综合"].
# The tests need no production database, downloaded body, or current website.
POLICY_EXAMPLES = [
    (
        "https://www.gov.cn/zhengce/202608/content_7079098.htm",
        "9b4e9c38f17d88ad916fceddf5ec7a8ad6ffdc950a22ef32a5de5ab70d2a39cb",
        "党政领导干部生态环境损害责任追究办法",
        "监察机关、组织（人事）部门、政府负有生态环境保护监督管理职责的部门应当完善生态环境损害责任追究的沟通协作机制，对生态环境损害问题早发现、早提醒、早纠正，并根据工作需要加强会商研判、综合作出责任追究决定或者提出责任追究建议。",
    ),
    (
        "https://www.gov.cn/zhengce/202609/content_7080613.htm",
        "55819c63427ad5ba348b09b9b7194bffd0af2b88466635ce85566005e58c8142",
        "如何引导更多资金流向“三农”领域",
        "方案提出深入实施金融支持乡村振兴专项行动，综合运用货币政策工具，在不新增地方政府隐性债务前提下，支持金融机构提供信贷支持。",
    ),
]


@pytest.mark.parametrize("url,document_hash,title,excerpt", POLICY_EXAMPLES,
                         ids=["environment-accountability", "rural-funding"])
def test_actual_policy_generic_word_is_not_company_relevance(
    url, document_hash, title, excerpt,
):
    assert len(document_hash) == 64
    assert "综合" in excerpt and "海光信息" not in excerpt
    with pytest.raises(ArchiveEvidenceError) as error:
        _relevance_basis(
            title + "\n" + excerpt, ArchiveCandidate(url, title),
            dimension="13_policy", symbol="688041.SH", subject="综合",
            subject_terms=("综合", "海光信息"),
        )
    assert error.value.code == "archive_subject_unrelated"


@pytest.mark.parametrize("placeholder", ["综合", "未知", "unknown", " UNKNOWN ", "—"])
@pytest.mark.parametrize("dimension", ["13_policy", "8_materials", "9_futures"])
def test_only_placeholders_cannot_bypass_requested_subject_check(placeholder, dimension):
    with pytest.raises(ArchiveEvidenceError) as error:
        _relevance_basis(
            "综合政策通知涉及原材料采购及期货合约规定。",
            ArchiveCandidate("https://www.gov.cn/zhengce/example.htm"),
            dimension=dimension, symbol="688041.SH", subject=placeholder,
            subject_terms=(placeholder,),
        )
    assert error.value.code == "archive_subject_unrelated"


@pytest.mark.parametrize("term", ["海光信息", "集成电路设计", "废弃资源综合利用业", "综合金融"])
def test_real_company_or_specific_industry_is_preserved(term):
    result = _relevance_basis(
        f"本政策规定对{term}适用。",
        ArchiveCandidate("https://www.gov.cn/zhengce/example.htm"),
        dimension="13_policy", symbol="688041.SH", subject="综合",
        subject_terms=("unknown", term),
    )
    assert result["matched_subject_terms"] == [term]
    assert result["subject_basis"] == "body_subject_match"


def test_global_macro_and_existing_unscoped_policy_rules_are_unchanged():
    candidate = ArchiveCandidate("https://www.gov.cn/zhengce/example.htm")
    macro = _relevance_basis(
        "货币政策和利率影响整体经济。", candidate,
        dimension="3_macro", symbol="688041.SH", subject="综合",
        subject_terms=("unknown", "海光信息"),
    )
    assert macro["subject_basis"] == "market_wide_macro"
    assert macro["matched_subject_terms"] == []
    unscoped = _relevance_basis(
        "本政策规定实施办法。", candidate,
        dimension="13_policy", symbol="", subject="",
    )
    assert unscoped["subject_basis"] == "market_wide_macro"


def test_publisher_bound_company_document_does_not_need_industry_match():
    result = _relevance_basis(
        "公司年度报告披露主营业务及行业情况。",
        ArchiveCandidate(
            "https://static.cninfo.com.cn/finalpage/2026-08-14/1225472510.PDF",
            security_code="688041", listing_hash="a" * 64,
        ),
        dimension="7_industry", symbol="688041.SH", subject="综合",
    )
    assert result["subject_basis"] == "publisher_listing_security_match"
    assert result["matched_subject_terms"] == []


@pytest.mark.parametrize("raw_industry,basic_industry,expected", [
    ("综合", None, "海光信息"),
    ("未知", "unknown", "海光信息"),
    (" UNKNOWN ", "—", "海光信息"),
    ("综合", "集成电路设计", "集成电路设计"),
    ("数字芯片设计", None, "数字芯片设计"),
    ("废弃资源综合利用业", None, "废弃资源综合利用业"),
])
def test_real_recovery_selects_useful_subject_without_rewriting_raw(
    tmp_path, raw_industry, basic_industry, expected,
):
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'subjects.sqlite'}")
    database.create_schema()
    calls = []

    def capture(**kwargs):
        calls.append(kwargs)
        return {"status": "evidence_gap", "evidence": [], "attempts": []}

    settings = SimpleNamespace(workspace_root=tmp_path)
    coordinator = MarketDataCollectionCoordinator(database, settings=settings)
    originals = {}
    try:
        with database.session() as session:
            repo = MarketDataRepository(session)
            for dimension, raw in [
                ("0_basic", {"name": "海光信息", "industry": basic_industry}),
                ("13_policy", {"industry": raw_industry}),
                ("7_industry", {"industry": raw_industry}),
            ]:
                payload = _payload(dimension)
                payload["raw"] = raw
                if dimension == "0_basic":
                    payload["symbol"] = "688041.SH"
                item = _input(dimension, payload=payload).model_copy(update={"symbol": "688041.SH"})
                row = SnapshotIngestionService(repo).ingest(item)
                originals[row.id] = (row.content_hash, deepcopy(row.payload_json))
        job = coordinator.create_job(
            tenant_id="tenant", user_id="user", actor_id="user",
            symbols=["688041.SH"], start_date=AS_OF, end_date=AS_OF,
            dimensions=["13_policy"], mode="missing_or_stale", submit=False,
        )
        result = recover_research_documents(
            database, job_id=job["id"], settings=settings,
            registry=DEFAULT_DIMENSION_REGISTRY,
            archive_collector=SimpleNamespace(collect=capture),
        )
        assert result["persisted_snapshot_count"] == 0
        assert len(calls) == 1
        assert calls[0]["subject"] == expected
        assert "海光信息" in calls[0]["subject_terms"]
        assert not {"综合", "未知", "unknown", "UNKNOWN", "—"}.intersection(calls[0]["subject_terms"])
        with database.session() as session:
            repo = MarketDataRepository(session)
            for id, original in originals.items():
                row = repo.require_snapshot(id)
                assert (row.content_hash, row.payload_json) == original
    finally:
        coordinator.close()
        database.close()
