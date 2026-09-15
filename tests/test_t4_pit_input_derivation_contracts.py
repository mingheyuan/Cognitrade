"""T4 input-only regressions, using saved inputs without collecting new facts.

The fixture contains selected, exact persisted fields; its source hashes refer
to whole original snapshots, not to these excerpts. Successful replay of those
inputs is not proof of a complete historical peer/moat/trap contract.
"""

from __future__ import annotations

import importlib
import json
import socket
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from domain_packs.finance_ashare.tools.snapshot_collector import (
    _annotate_point_in_time_snapshot,
)
from research_system.market_data.contracts import SnapshotStatus, cutoff_at_for_date
from research_system.market_data.uzi_adapter import UziSnapshotAdapter


AS_OF = date(2026, 9, 2)
SYMBOL = "688041.SH"
FIXTURE = Path(__file__).parent / "fixtures" / "t4_pit_688041_input_excerpts.json"
SKILLS = importlib.import_module(
    "domain_packs.finance_ashare.skills.uzi_source_snapshot.skill"
)


@pytest.fixture
def forbid_external_io(monkeypatch):
    """Count forbidden calls even if an actual skill catches their exception."""
    guards = []
    for owner, attribute in (
        (requests.sessions.Session, "request"),
        (socket, "create_connection"),
        (socket.socket, "connect"),
        (SKILLS.UziSourceSnapshotSkill, "_safe_search"),
        (SKILLS.UziSourceSnapshotSkill, "_safe_browser"),
        (SKILLS.UziSourceSnapshotSkill, "_safe_news"),
        (SKILLS.UziSourceSnapshotSkill, "_safe_hottrend"),
        (SKILLS.AkShareSectorAdapter, "get_sector_snapshot"),
        (SKILLS, "fetch_global_peer_comparison"),
    ):
        guard = Mock(side_effect=AssertionError(f"external call forbidden: {attribute}"))
        monkeypatch.setattr(owner, attribute, guard)
        guards.append(guard)
    yield
    for guard in guards:
        guard.assert_not_called()


@pytest.mark.parametrize(
    ("snapshot_type", "skill_name", "missing_fields"),
    [
        ("peers", "PeersSnapshotSkill", ["peers"]),
        ("moat", "MoatSnapshotSkill", ["factors", "research_and_development"]),
        ("trap", "TrapSnapshotSkill", ["signals"]),
    ],
)
def test_actual_historical_skill_keeps_insufficient_pit_inputs_unavailable(
    monkeypatch, forbid_external_io, snapshot_type, skill_name, missing_fields
):
    saved = json.loads(FIXTURE.read_text(encoding="utf-8"))
    original = deepcopy(saved)
    assert saved["as_of_date"] == AS_OF.isoformat()
    assert saved["symbol"] == SYMBOL
    inputs = {item["dimension_id"]: item["excerpt"] for item in saved["inputs"]}
    fundamental = {
        "facts": inputs["1_financials"],
        # This is replay instrumentation, not a claim of a new provider result.
        "source": {
            "provider_id": "offline.fixed_pit_inputs",
            "source_id": "offline.fixed_pit_inputs",
        },
        "source_trace": [
            {"source_id": "offline.fixed_pit_inputs", "status": "available"}
        ],
    }
    news = [
        {
            "title": row["新闻标题"],
            "content": row["新闻内容"],
            "date": row["发布时间"],
            "source": row["文章来源"],
            "url": row["新闻链接"],
        }
        for row in inputs["17_sentiment"]["news_samples"]
    ]
    # The current provider contract uses `date`; the stored samples use Chinese
    # field names. This explicit transport mapping adds no news or timestamps.
    fundamental_call = Mock(side_effect=lambda **_: deepcopy(fundamental))
    industry_call = Mock(side_effect=lambda *_, **__: deepcopy(inputs["7_industry"]))
    news_call = Mock(side_effect=lambda **_: deepcopy(news))
    monkeypatch.setattr(SKILLS.FundamentalSnapshotSkill, "run", fundamental_call)
    monkeypatch.setattr(SKILLS.UziSourceSnapshotSkill, "_industry_context", industry_call)
    monkeypatch.setattr(SKILLS.AkShareCatalystAdapter, "get_symbol_news", news_call)

    snapshot = getattr(SKILLS, skill_name)().run(
        query="海光信息", symbol=SYMBOL, as_of_date=AS_OF
    )
    annotated = _annotate_point_in_time_snapshot(
        snapshot,
        snapshot_type=snapshot_type,
        as_of_date=AS_OF,
        cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai"),
    )
    unchanged = deepcopy(annotated)
    ingest = UziSnapshotAdapter().adapt(
        annotated,
        symbol=SYMBOL,
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=AS_OF,
    )
    manifest = ingest.query_manifest
    assert annotated == unchanged
    assert saved == original
    assert ingest.status == SnapshotStatus.DATA_GAP
    assert manifest["collection_outcome"] == "unavailable"
    assert manifest["historical_capability"]["implementation_status"] == "inputs_only"
    assert manifest["degradation"]["reason_code"] == "normalization_or_derivation_missing"
    assert manifest["degradation"]["retryable"] is False
    assert manifest["degradation"]["excluded_from_scoring"] is True
    assert "verified_empty_disclosure" not in manifest
    assert "provider_failure" not in manifest
    assert manifest["field_coverage"]["present_fields"] == []
    assert sorted(manifest["field_coverage"]["missing_fields"]) == sorted(missing_fields)

    raw = ingest.payload["raw"]
    if snapshot_type == "peers":
        fundamental_call.assert_called_once()
        industry_call.assert_called_once()
        news_call.assert_not_called()
        assert raw["comparison"]["industry"] == inputs["7_industry"]["industry"]
        assert raw["comparison"]["peers"] == []
        assert len(raw["comparison"]["self_only"]) == 1
        assert not raw.get("peer_table")
        assert not raw.get("peer_comparison")
    elif snapshot_type == "moat":
        fundamental_call.assert_called_once()
        industry_call.assert_not_called()
        news_call.assert_not_called()
        assert raw["financial_abstract"] == inputs["1_financials"]["financial_abstract"]
        assert raw["banking_metrics"] == inputs["1_financials"]["banking_metrics"]
        assert not raw.get("scores")
        assert not raw.get("rd_summary")
    else:
        fundamental_call.assert_not_called()
        industry_call.assert_not_called()
        news_call.assert_called_once_with(symbol=SYMBOL, limit=50)
        # The actual PIT collector sorts eligible rows newest-first. Compare
        # complete rows, including ties, rather than changing recorded values.
        assert raw["news"] == sorted(news, key=lambda row: row["date"], reverse=True)
        assert len(raw["news"]) == 5
        assert len(ingest.payload["signals"]) == 8
        assert all(row["status"] == "insufficient" for row in ingest.payload["signals"])
        assert not raw.get("signals")
        assert not raw.get("signals_hit_detail")
        assert "trap_score" not in raw
        assert "trap_level" not in raw


def test_nested_comparison_peers_preserve_original_row_without_claiming_full_contract():
    """Normalize the source shape without treating one peer as a full contract.

    This fictional shape fixture makes no assertion about real peer membership
    or financial values. One preserved row still fails the three-peer contract.
    """
    row = {
        "symbol": "TEST_PEER",
        "name": "FICTIONAL SHAPE FIXTURE",
        "roe": None,
        "source_note": {"fixture": "not historical evidence", "unknown": None},
    }
    raw = {"comparison": {"industry": "TEST INDUSTRY", "peers": [row]}}
    original = deepcopy(raw)
    payload, errors = UziSnapshotAdapter()._normalize_payload(
        "4_peers",
        raw,
        facts={},
        snapshot={},
        symbol="TEST_TARGET",
        market="CN_EQUITY",
        observed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert raw == original
    assert "fewer than three valid comparable companies" in errors
    assert payload["peers"] == [row]


@pytest.mark.parametrize("preferred_field", ["peer_table", "peers"])
def test_nested_peer_alias_preserves_existing_top_level_precedence(preferred_field):
    native_peer = {"symbol": "TEST_NATIVE", "roe": None}
    raw = {
        preferred_field: [{"symbol": "TEST_TARGET", "is_self": True}, native_peer],
        "comparison": {"peers": [{"symbol": "TEST_NESTED", "roe": None}]},
    }
    if preferred_field == "peer_table":
        raw["peers"] = [{"symbol": "TEST_TOP_LEVEL", "roe": None}]
    original = deepcopy(raw)
    payload, errors = UziSnapshotAdapter()._normalize_payload(
        "4_peers", raw, facts={}, snapshot={}, symbol="TEST_TARGET",
        market="CN_EQUITY", observed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert payload["peers"] == [native_peer]
    assert raw == original
    assert "fewer than three valid comparable companies" in errors


@pytest.mark.parametrize("comparison", [None, [], "invalid", {"peers": "invalid"}])
def test_malformed_nested_peer_shape_does_not_manufacture_rows(comparison):
    payload, errors = UziSnapshotAdapter()._normalize_payload(
        "4_peers", {"comparison": comparison}, facts={}, snapshot={},
        symbol="TEST_TARGET", market="CN_EQUITY",
        observed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert payload["peers"] == []
    assert "fewer than three valid comparable companies" in errors


def test_one_nested_peer_remains_contract_incomplete_after_historical_adaptation():
    peer = {"symbol": "TEST_PEER", "roe": None}
    snapshot = _annotate_point_in_time_snapshot(
        {
            "snapshot_type": "peers",
            "retrieved_at": "2026-09-11T00:00:00+00:00",
            "quality_status": "available",
            "facts": {"comparison": {"peers": [
                {"symbol": "TEST_TARGET", "is_self": True}, peer,
            ]}},
            "source": {"provider_id": "offline.shape_fixture", "source_id": "test"},
            "source_trace": [{"source_id": "test", "status": "available"}],
        },
        snapshot_type="peers", as_of_date=AS_OF,
        cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai"),
    )
    unchanged = deepcopy(snapshot)
    ingest = UziSnapshotAdapter().adapt(
        snapshot, symbol="TEST_TARGET", market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai", as_of_date=AS_OF,
    )
    assert snapshot == unchanged
    assert ingest.payload["peers"] == [peer]
    assert ingest.validation["audit_passed"] is False
    assert "fewer than three valid comparable companies" in ingest.validation["semantic_errors"]
    contract = UziSnapshotAdapter().registry.get("4_peers")
    requirement = next(item for item in contract.item_count_requirements if item.path == "peers")
    assert requirement.minimum == 3
    assert len(ingest.payload["peers"]) < requirement.minimum
