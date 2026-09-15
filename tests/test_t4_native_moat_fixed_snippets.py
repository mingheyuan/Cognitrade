"""Unmodified native moat baseline over fictional, fixed search responses.

This is a tests-only provider shim. It neither supplies historical evidence nor
implements PIT retrieval, source coverage, a host derivation, or new scoring.
"""

from __future__ import annotations

import importlib
import socket
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "domain_packs/finance_ashare/vendor/uzi_3_9_2/scripts"
)
NATIVE_SHA256 = "0d466adca618ee8d34e5c8e18bad597f95a379159652a6b2ef9c76424b919427"
COMPANY = "离线测试甲公司"
QUERY_ENDINGS = {
    "intangible": "专利 核心技术 品牌壁垒 竞争优势",
    "switching": "客户粘性 转换成本 认证壁垒 大客户",
    "network": "平台效应 网络效应 用户生态",
    "scale": "市场份额 行业地位 规模优势 龙头",
    "rd": "研发投入 研发占比 技术实力",
}


@pytest.fixture
def native_moat(monkeypatch):
    guards = []
    for owner, attribute in (
        (requests.sessions.Session, "request"),
        (socket, "create_connection"),
        (socket.socket, "connect"),
    ):
        guard = Mock(side_effect=AssertionError("native baseline must remain offline"))
        monkeypatch.setattr(owner, attribute, guard)
        guards.append(guard)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("fetch_moat")
    assert Path(module.__file__).resolve() == SCRIPTS / "fetch_moat.py"
    assert sha256((SCRIPTS / "fetch_moat.py").read_bytes()).hexdigest() == NATIVE_SHA256
    basic = Mock(return_value={"name": COMPANY, "full_name": COMPANY})
    monkeypatch.setattr(module.ds, "fetch_basic", basic)
    yield module
    basic.assert_called_once()
    for guard in guards:
        guard.assert_not_called()


def test_native_main_fixed_snippets_preserve_filtering_and_keyword_results(
    monkeypatch, native_moat
):
    # Deliberately fictional, no financial quantities or historic safety claims.
    bodies = {
        "intangible": f"{COMPANY} 专利 核心技术 自主 同质。" + "测试摘录。" * 50,
        "switching": f"{COMPANY} 绑定 长期合作 认证 易替换。",
        "network": f"{COMPANY} 平台 生态 网络 单点。",
        "scale": f"{COMPANY} 龙头 第一 份额 追赶。",
        "rd": f"{COMPANY} 研发原文测试摘录，不包含财务数值。",
    }
    responses = {
        key: [
            {"title": COMPANY + "测试标题" * 30, "body": body, "url": "fixture://" + key},
            {"title": "贵州茅台", "body": "贵州茅台 独家 国产替代 最大 用户基数"},
            {"title": COMPANY, "body": "拼音 汉语 甲骨文 独家 最大"},
        ]
        for key, body in bodies.items()
    }
    unchanged = deepcopy(responses)
    calls = []

    def trusted(query, *, dim_key, max_results):
        assert dim_key == "14_moat"
        assert max_results == 6
        key = next(key for key, ending in QUERY_ENDINGS.items() if query.endswith(ending))
        assert query == f"{COMPANY} 上市公司 {QUERY_ENDINGS[key]}"
        calls.append(key)
        return deepcopy(responses[key])

    fallback = Mock(side_effect=AssertionError("three trusted candidates need no fallback"))
    monkeypatch.setattr(native_moat, "search_trusted", trusted)
    monkeypatch.setattr(native_moat, "search", fallback)
    result = native_moat.main("688041.SH")

    assert calls == list(QUERY_ENDINGS)
    assert responses == unchanged
    fallback.assert_not_called()
    data = result["data"]
    assert data["scores"] == {"intangible": 7, "switching": 7, "network": 7, "scale": 7}
    assert data["scores_available"] is True
    assert data["scores_evidence"] == dict.fromkeys(list(QUERY_ENDINGS)[:4], True)
    assert data["rd_summary"] == bodies["rd"][:100]
    for key, body in bodies.items():
        assert data["web_search_snippets"][key] == [{
            "title": responses[key][0]["title"][:80],
            "body": body[:200],
            "url": "fixture://" + key,
        }]
        if key != "rd":
            assert data[key] == body[:100]


def test_native_main_no_snippets_means_unassessed_not_four_neutral_scores(
    monkeypatch, native_moat
):
    trusted, fallback = Mock(return_value=[]), Mock(return_value=[])
    monkeypatch.setattr(native_moat, "search_trusted", trusted)
    monkeypatch.setattr(native_moat, "search", fallback)
    data = native_moat.main("688041.SH")["data"]
    assert trusted.call_count == fallback.call_count == 5
    assert data["scores"] == {}
    assert data["scores_available"] is False
    assert data["scores_evidence"] == dict.fromkeys(list(QUERY_ENDINGS)[:4], False)
    assert data["rd_summary"] == "—"
    assert data["web_search_snippets"] == dict.fromkeys(QUERY_ENDINGS, [])
