"""The report must receive retrieval metadata from the exact frozen document."""
import json
from copy import deepcopy

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
