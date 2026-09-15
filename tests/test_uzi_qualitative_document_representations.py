"""Offline provider-capture regressions for one PDF's explicit text versions."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as q
from tests.test_uzi_qualitative_company_document_context import (
    CapturingProvider, SHARED_URL, TYPES, apply, document, fixed_rows,
)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("representation projection must not fetch sources or call a real model")
    monkeypatch.setattr("socket.socket.connect", blocked)
    monkeypatch.setattr("socket.create_connection", blocked)
    monkeypatch.setattr("socket.getaddrinfo", blocked)


def fixture(*, old_length=2_200, full_length=4_400):
    rows = fixed_rows(historical=True)
    owner = next(row for row in rows if row["dimension"] == "8_materials")
    prefix = "OFFLINE_ARCHIVE_BODY_NOT_ISSUER_FACTS\n"
    full = prefix + "文" * (full_length - len(prefix) - len("FINAL_PAGE_MARKER")) + "FINAL_PAGE_MARKER"
    old = document(SHARED_URL, full[:old_length])
    old["id"] = "old-evidence-id"
    old["content_hash"] = sha256(b"fixed original PDF bytes").hexdigest()
    old["content_ref"] = "/fixed/" + old["content_hash"] + ".pdf"
    new = deepcopy(old)
    new.update(id="new-evidence-id", content=full)
    text_hash = sha256(full.encode()).hexdigest()
    binding = {
        "version": "research-text-v1", "source_content_hash": old["content_hash"],
        "text_content_hash": text_hash,
        "text_content_ref": f"/fixed/{text_hash[:2]}/{text_hash}.txt",
        "text_utf8_bytes": len(full.encode()), "text_characters": len(full),
        "complete": True, "full_document_reading_claimed": False,
        "inline_utf8_bytes": len(old["content"].encode()), "inline_truncated": True,
        "extractor": "pypdf", "extractor_version": "6.14.2",
        "page_count": 166, "pages_processed": 166, "pages_without_text": [],
    }
    new["text_extraction"] = binding
    owner["source_refs"].extend([old, new])
    owner["query_manifest"]["research_archive"] = {
        "version": "research-archive-v3-full-text",
        "text_extractions": [{"evidence_key": "e" * 64, "binding": deepcopy(binding)}],
    }
    return rows, owner, old, new


def shared(call):
    return json.loads(call["system_prompt"].split("FROZEN_STOCK_DOCUMENTS=", 1)[1].split("\nFROZEN_EVIDENCE_BUNDLE=", 1)[0])


def report_records(call):
    return [record for record in shared(call) if record["url"] == SHARED_URL]


def test_public_provider_gets_one_bound_full_representation_and_both_lineages():
    rows, owner, old, new = fixture()
    provider = CapturingProvider(urls={key: SHARED_URL for key in TYPES})
    assert apply(rows, provider)["qualitative_agent_execution"]["status"] == "completed"
    assert len(provider.calls) == 3
    for call in provider.calls:
        records = report_records(call)
        assert len(records) == 1, "same original PDF was sent as two factual bodies"
        record = records[0]
        assert record["finding"] == new["content"]
        assert record["text_extraction"] == new["text_extraction"]
        view = record["representation_view"]
        assert view["status"] == "bound_full_text_replaces_legacy_body_in_prompt_only"
        assert view["facts_are_not_additional_evidence"] is True
        assert {row["evidence_id"] for row in view["source_bindings"]} == {old["id"], new["id"]}
        legacy = next(row for row in view["source_bindings"] if row["evidence_id"] == old["id"])
        assert legacy["text_sha256"] == sha256(old["content"].encode()).hexdigest()
        assert legacy["snapshot_id"] == owner["snapshot_id"]
        assert legacy["dimension"] == "8_materials"
        assert legacy["published_at"] == old["published_at"]
        assert legacy["observed_at"] == old["observed_at"]


def test_real_apply_budget_is_not_spent_on_superseded_prefix():
    rows, _, _, new = fixture(old_length=220_000, full_length=300_000)
    provider = CapturingProvider()
    apply(rows, provider)
    assert len(provider.calls) == 3
    for call in provider.calls:
        assert len(call["system_prompt"]) + len(call["user_prompt"]) < 500_000
        assert report_records(call)[0]["finding"] == new["content"]


@pytest.mark.parametrize("damage", [
    "no_manifest", "mismatched_manifest", "source_hash", "text_hash", "byte_count",
    "char_count", "incomplete", "future_version", "bad_path", "different_original", "different_url", "invalid_source_hash",
])
def test_unverified_or_different_original_is_never_substituted(damage):
    rows, owner, old, new = fixture()
    binding = new["text_extraction"]
    if damage == "no_manifest":
        owner["query_manifest"].pop("research_archive")
    elif damage == "mismatched_manifest":
        owner["query_manifest"]["research_archive"]["text_extractions"][0]["binding"]["page_count"] = 99
    elif damage == "source_hash":
        binding["source_content_hash"] = "f" * 64
    elif damage == "text_hash":
        binding["text_content_hash"] = "f" * 64
    elif damage == "byte_count":
        binding["text_utf8_bytes"] += 1
    elif damage == "char_count":
        binding["text_characters"] += 1
    elif damage == "incomplete":
        binding["complete"] = False
    elif damage == "future_version":
        binding["version"] = "research-text-v999"
    elif damage == "bad_path":
        binding["text_content_ref"] = "/not-content-addressed.txt"
    elif damage == "different_original":
        new["content_hash"] = "f" * 64
        binding["source_content_hash"] = new["content_hash"]
    elif damage == "different_url":
        new["url"] = "https://archive.example/688041/another-frozen-report.pdf"
    elif damage == "invalid_source_hash":
        old["content_hash"] = new["content_hash"] = binding["source_content_hash"] = "not-a-sha256"
    if damage not in {"no_manifest", "mismatched_manifest"}:
        owner["query_manifest"]["research_archive"]["text_extractions"][0]["binding"] = deepcopy(binding)
    provider = CapturingProvider()
    apply(rows, provider)
    for call in provider.calls:
        records = [record for record in shared(call) if record["url"] in {old["url"], new["url"]}]
        assert len(records) == 2
        assert {record["finding"] for record in records} == {old["content"], new["content"]}
        assert all("representation_view" not in record for record in records)


def test_old_manifest_without_explicit_new_binding_stays_byte_identical():
    rows, owner, old, new = fixture()
    owner["source_refs"].remove(new)
    owner["query_manifest"].pop("research_archive")
    first, second = CapturingProvider(), CapturingProvider()
    apply(rows, first)
    # An unrelated file is not discovered or parsed by this pure prompt view.
    apply(deepcopy(rows), second)
    assert {row["group"]: row["system_prompt"] for row in first.calls} == {row["group"]: row["system_prompt"] for row in second.calls}
    assert all(report_records(call)[0]["finding"] == old["content"] for call in first.calls)
    assert all("representation_view" not in report_records(call)[0] for call in first.calls)


def test_conflicting_bound_full_text_versions_are_not_arbitrarily_ranked():
    rows, owner, old, new = fixture()
    other = deepcopy(new)
    other["id"] = "another-new-evidence"
    other["content"] = other["content"].replace("FINAL_PAGE_MARKER", "OTHER_PAGE_MARKER")
    binding = other["text_extraction"]
    binding["text_content_hash"] = sha256(other["content"].encode()).hexdigest()
    binding["text_content_ref"] = f"/fixed/{binding['text_content_hash'][:2]}/{binding['text_content_hash']}.txt"
    owner["source_refs"].append(other)
    owner["query_manifest"]["research_archive"]["text_extractions"].append({"evidence_key": "d" * 64, "binding": deepcopy(binding)})
    provider = CapturingProvider()
    apply(rows, provider)
    assert all(len(report_records(call)) == 3 for call in provider.calls)


def test_hidden_legacy_lineage_change_still_invalidates_prompt_cache(tmp_path):
    rows, _, old, _ = fixture()
    first = CapturingProvider(workspace_root=tmp_path)
    apply(rows, first)
    repeated = CapturingProvider(workspace_root=tmp_path)
    apply(rows, repeated)
    assert repeated.calls == []
    old["id"] = "changed-fixed-legacy-evidence-id"
    revised = CapturingProvider(workspace_root=tmp_path)
    apply(rows, revised)
    assert len(revised.calls) == 3


@pytest.mark.parametrize("boundary", ["same_stock_other_dimension", "foreign_stock", "unsafe_pit", "future_snapshot"])
def test_new_representation_cannot_cross_stock_or_pit_boundaries(boundary):
    rows, owner, old, new = fixture()
    other = deepcopy(next(row for row in rows if row["dimension"] == "7_industry"))
    other["source_refs"] = [new]
    other["snapshot_id"] = "different-owner-new-text"
    other["query_manifest"]["research_archive"] = owner["query_manifest"].pop("research_archive")
    owner["source_refs"].remove(new)
    if boundary == "foreign_stock":
        other["symbol"] = "000001.SZ"
    elif boundary == "unsafe_pit":
        other["query_manifest"]["pit_safe"] = False
    elif boundary == "future_snapshot":
        other["snapshot_date"] = "2026-09-09"
    rows.append(other)
    provider = CapturingProvider(urls={key: SHARED_URL for key in TYPES})
    assert apply(rows, provider)["qualitative_agent_execution"]["status"] == "completed"
    for call in provider.calls:
        record = report_records(call)[0]
        if boundary == "same_stock_other_dimension":
            assert len(report_records(call)) == 1
            assert record["finding"] == new["content"]
            assert {entry["dimension"] for entry in record["representation_view"]["source_bindings"]} == {"8_materials", "7_industry"}
        else:
            assert record["finding"] == old["content"]
            assert "FINAL_PAGE_MARKER" not in call["system_prompt"]


def test_two_representations_do_not_allow_repeated_finding_to_count_twice():
    rows, _, _, _ = fixture()

    class RepeatedFinding(CapturingProvider):
        def parse(self, **kwargs):
            result = super().parse(**kwargs)
            for dimension in result.dimensions:
                dimension.evidence[1] = dimension.evidence[0].model_copy(deep=True)
            return result

    provider = RepeatedFinding(urls={key: SHARED_URL for key in TYPES})
    result = apply(rows, provider)
    assert result["qualitative_agent_execution"]["status"] == "incomplete"
    assert result["qualitative_agent_execution"]["completed_group_count"] == 0
    assert all("qualitative_repeated_finding" in json.dumps(record) for record in result["qualitative_agent_execution"]["failure_records"])
