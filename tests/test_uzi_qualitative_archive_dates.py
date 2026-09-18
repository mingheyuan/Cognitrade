"""The report must receive retrieval metadata from the exact frozen document."""
import json
from copy import deepcopy
from hashlib import sha256

import pytest

from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as q
from tests.test_uzi_qualitative_company_document_context import (
    CapturingProvider,
    apply,
    attach_shared_document,
    fixed_rows,
)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("archive metadata projection must not fetch documents or call live models")
    monkeypatch.setattr("socket.socket.connect", blocked)
    monkeypatch.setattr("socket.getaddrinfo", blocked)


def test_every_group_receives_document_observation_not_just_snapshot_date():
    rows = fixed_rows()
    doc = attach_shared_document(rows)
    doc.update(provider="fixed_archive_provider", observed_at="2026-09-07T03:04:05+00:00")
    provider = CapturingProvider()
    apply(rows, provider)
    for call in provider.calls:
        docs = json.loads(call["system_prompt"].split("FROZEN_STOCK_DOCUMENTS=", 1)[1].split("\nFROZEN_EVIDENCE_BUNDLE=", 1)[0])
        item = next(item for item in docs if item["url"] == doc["url"])
        assert item["observed_at"] == doc["observed_at"]
        assert item["published_at"] == doc["published_at"]
        assert item["evidence_id"] == doc["id"]
        assert item["provider"] == doc["provider"]
        assert item["grade"] == doc["grade"]


@pytest.mark.parametrize("missing", ["published_at", "observed_at"])
def test_missing_document_time_is_not_filled_from_today_url_or_snapshot(missing):
    rows = fixed_rows()
    doc = attach_shared_document(rows)
    doc.pop(missing)
    _, _, documents = q._fixed_report_inputs(rows, {})
    item = next(item for item in documents if item["url"] == doc["url"])
    assert missing in item and item[missing] is None


def test_equal_document_bodies_preserve_all_fixed_source_bindings():
    rows = fixed_rows()
    doc = attach_shared_document(rows)
    later = deepcopy(doc)
    later.update(id="another-fixed-evidence", observed_at="2026-09-08T09:55:00+00:00")
    rows[1]["source_refs"].append(later)
    before = deepcopy(rows)
    _, _, documents = q._fixed_report_inputs(rows, {})
    same = [item for item in documents if item["url"] == doc["url"]]
    assert len(same) == 1
    bindings = same[0]["archive_bindings"]
    assert {item["evidence_id"] for item in bindings} == {doc["id"], later["id"]}
    assert {item["observed_at"] for item in bindings} == {doc["observed_at"], later["observed_at"]}
    assert {item["snapshot_id"] for item in bindings} == {rows[1]["snapshot_id"], rows[5]["snapshot_id"]}
    assert rows == before


def test_utc_boundary_exposes_market_day_in_every_group_without_changing_frozen_input():
    rows = fixed_rows()
    doc = attach_shared_document(rows)
    doc["published_at"] = "2026-08-31T16:00:00+00:00"
    before = deepcopy(rows)
    provider = CapturingProvider()
    apply(rows, provider)
    for call in provider.calls:
        docs = json.loads(call["system_prompt"].split("FROZEN_STOCK_DOCUMENTS=", 1)[1].split("\nFROZEN_EVIDENCE_BUNDLE=", 1)[0])
        item = next(item for item in docs if item["url"] == doc["url"])
        assert item["published_at"] == "2026-08-31T16:00:00+00:00"
        assert item["published_at_market"] == "2026-09-01T00:00:00+08:00"
        assert item["market_publication_date"] == "2026-09-01"
        assert item["market_timezone"] == "Asia/Shanghai"
        assert item["archive_bindings"][0]["market_publication_date"] == "2026-09-01"
        assert "不要直接截取 UTC 日期" in call["system_prompt"]
    assert rows == before


@pytest.mark.parametrize("value,zone", [(None, "Asia/Shanghai"), ("2026-09-01", "Asia/Shanghai"),
    ("2026-09-01T00:00:00", "Asia/Shanghai"), ("bad date", "Asia/Shanghai"),
    ("2026-08-31T16:00:00Z", None), ("2026-08-31T16:00:00Z", "Not/AZone")])
def test_unknown_or_naive_publication_never_gains_an_invented_local_date(value, zone):
    assert q.market_publication_time_view(value, zone) == {}


@pytest.mark.parametrize("value,expected", [("2026-01-02T02:00:00Z", "2026-01-01T21:00:00-05:00"),
    ("2026-07-02T02:00:00Z", "2026-07-01T22:00:00-04:00")])
def test_market_date_conversion_uses_actual_dst_offset(value, expected):
    assert q.market_publication_time_view(value, "America/New_York")["published_at_market"] == expected


def test_observed_only_document_does_not_gain_a_publication_date():
    rows = fixed_rows()
    doc = attach_shared_document(rows)
    doc.update(published_at=None, publisher_published_at=None,
               visible_by="2026-08-31T16:00:00Z", publication_time_basis="observed_upper_bound")
    _, _, documents = q._fixed_report_inputs(rows, {})
    item = next(item for item in documents if item["url"] == doc["url"])
    assert item["published_at"] is None and "market_publication_date" not in item
    assert item["visible_by"] == doc["visible_by"]
    assert item["archive_bindings"][0]["publication_time_basis"] == "observed_upper_bound"


def test_draft_cover_month_remains_distinct_from_archive_publication_day():
    rows = fixed_rows()
    doc = attach_shared_document(rows)
    doc.update(published_at="2026-08-31T16:00:00+00:00",
               content="限制性股票激励计划（草案）\n2026年8月\n董事会\n2026年9月1日")
    doc["content_hash"] = sha256(doc["content"].encode()).hexdigest()
    before = deepcopy(rows)
    provider = CapturingProvider()
    apply(rows, provider)
    for call in provider.calls:
        prompt = call["system_prompt"]
        docs = json.loads(prompt.split("FROZEN_STOCK_DOCUMENTS=", 1)[1].split("\nFROZEN_EVIDENCE_BUNDLE=", 1)[0])
        item = next(item for item in docs if item["url"] == doc["url"])
        assert item["finding"] == doc["content"]
        assert item["market_publication_date"] == "2026-09-01"
        assert "草案封面的编制月份不是公开发布月份" in prompt
        assert "不能写‘8月发布’" in prompt
    assert rows == before


@pytest.mark.parametrize("workflow", ["report", "data_center"])
def test_event_group_checks_action_date_in_body_without_inventing_one(workflow):
    # A publication timestamp is not the board meeting timestamp. These are
    # prompt instructions, not a claim that semantic correctness is guaranteed.
    evidence = {key: {} for key in ("7_industry", "15_events")}
    prompt = q._group_user_prompt(group_label="B · Industry-Events",
        dimensions=("7_industry", "15_events"), evidence_bundle=evidence, workflow=workflow)
    assert "会议召开、审议通过、签约等动作的发生日取自正文" in prompt
    assert "不能从公告发布日期或落款推导" in prompt
    assert "正文未明确发生日时只写‘公告日披露……’" in prompt
    assert "不补造发生日期" in prompt
    assert "NATIVE_TASK_2_5_PROTOCOL_BEGIN" in prompt


@pytest.mark.parametrize("label,dimensions", [
    ("A · Macro-Policy", ("3_macro", "13_policy")),
    ("C · Cost-Transmission", ("8_materials", "9_futures")),
])
def test_event_prompt_correction_does_not_invalidate_other_group_prompts(label, dimensions):
    prompt = q._group_user_prompt(group_label=label, dimensions=dimensions,
        evidence_bundle={key: {} for key in dimensions})
    assert "叙述事件前核对正文中该动作对应的日期" not in prompt
