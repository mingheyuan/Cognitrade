"""Offline regression for archived report text lost at the old 2,000-char cut."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as qualitative
from domain_packs.finance_ashare.subagents.uzi_input_views import (
    DEFAULT_PROMPT_VIEW_MAX_CHARACTERS, UziInputBudgetExceeded,
)
from tests.test_uzi_qualitative_company_document_context import (
    CapturingProvider, SHARED_URL, TYPES, apply, document, fixed_rows,
)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("archived-text tests must not call live sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def archived_rows(*, historical=False, length=97_558):
    rows = fixed_rows(historical=historical)
    start = "1/ 166\n仅供离线回归的冻结文档，不是实际发行人披露。\n"
    end = "\n29/ 166\n后部衍生品栏目事实标记\n72/ 166\n后部外币折算政策标记\n96/ 166\n原文在表格中途截断"
    content = start + "冻结原文。" * ((length - len(start) - len(end)) // 5)
    content += "。" * (length - len(content) - len(end)) + end
    assert len(content) == length
    archived = document(SHARED_URL, content)
    next(row for row in rows if row["dimension"] == "8_materials")["source_refs"].append(archived)
    return rows, archived


def shared_record(call):
    serialized = call["system_prompt"].split("FROZEN_STOCK_DOCUMENTS=", 1)[1].split("\nFROZEN_EVIDENCE_BUNDLE=", 1)[0]
    return next(row for row in json.loads(serialized) if row["url"] == SHARED_URL)


@pytest.mark.parametrize("historical", [False, True])
def test_actual_group_prompts_keep_all_frozen_materialized_report_text(historical):
    rows, archived = archived_rows(historical=historical)
    provider = CapturingProvider(urls={key: SHARED_URL for key in TYPES})
    assert apply(rows, provider)["qualitative_agent_execution"]["status"] == "completed"
    assert len(provider.calls) == 3
    for call in provider.calls:
        visible = shared_record(call)
        assert visible["finding"] == archived["content"], "97k frozen report silently collapsed to a 2k prefix"
        assert visible["content_hash"] == archived["content_hash"]
        assert visible["content_ref"] == archived["content_ref"]
        assert visible["content_scope"] == "complete_frozen_materialized_text_not_original_document"
        assert visible["full_document_reading_claimed"] is False
        assert len(call["system_prompt"]) + len(call["user_prompt"]) <= DEFAULT_PROMPT_VIEW_MAX_CHARACTERS


def test_archived_content_wins_over_a_separate_short_finding():
    rows, archived = archived_rows()
    archived["finding"] = "旧摘要不应该替代已冻结的完整材料化正文。"
    provider = CapturingProvider()
    apply(rows, provider)
    assert all(shared_record(call)["finding"] == archived["content"] for call in provider.calls)


def test_non_archived_snippet_keeps_its_existing_bound():
    text = "普通搜索摘要" * 1000
    record = qualitative._extract_evidence_records({"url": SHARED_URL, "finding": text})[0]
    assert record["finding"] == text[:2_000]
    assert "content_scope" not in record


@pytest.mark.parametrize("change", ["foreign_symbol", "unsafe_pit", "missing_hash", "search_snippet"])
def test_full_text_never_bypasses_existing_document_proof_or_scope(change):
    rows, archived = archived_rows(historical=True)
    owner = next(row for row in rows if row["dimension"] == "8_materials")
    if change == "foreign_symbol":
        archived["symbol"] = "000001.SZ"
    elif change == "unsafe_pit":
        owner["query_manifest"]["pit_safe"] = False
    elif change == "missing_hash":
        archived.pop("content_hash")
    else:
        archived["source_kind"] = "search_snippet"
    provider = CapturingProvider()
    apply(rows, provider)
    assert all("后部外币折算政策标记" not in call["system_prompt"] + call["user_prompt"] for call in provider.calls)


def test_over_budget_archive_is_explicit_before_any_model_call():
    assert DEFAULT_PROMPT_VIEW_MAX_CHARACTERS == 500_000
    rows, _ = archived_rows(length=500_001)
    before = deepcopy(rows)
    provider = CapturingProvider()
    with pytest.raises(UziInputBudgetExceeded, match="input_budget_exceeded"):
        apply(rows, provider)
    assert provider.calls == []
    assert rows == before


def test_repair_feedback_counts_toward_same_budget_before_dispatch():
    rows, _ = archived_rows(length=10_000)
    probe = CapturingProvider()
    apply(rows, probe)
    largest = max(len(call["system_prompt"]) + len(call["user_prompt"]) for call in probe.calls)
    # Existing policy bounds each feedback item to 500 chars; retain that
    # policy and place the archive just below the unchanged request ceiling.
    rows, _ = archived_rows(length=10_000 + DEFAULT_PROMPT_VIEW_MAX_CHARACTERS - largest - 100)
    provider = CapturingProvider()
    with pytest.raises(UziInputBudgetExceeded, match="input_budget_exceeded"):
        qualitative.apply_qualitative_deep_dive(
            {"symbol": "688041.SH"}, snapshot_rows=rows, llm_provider=provider,
            repair_feedback=["C · Cost-Transmission:" + "x" * 500_000],
        )
    assert provider.calls == []


def test_late_frozen_content_change_invalidates_group_cache(tmp_path):
    rows, archived = archived_rows()
    first = CapturingProvider(workspace_root=tmp_path)
    apply(rows, first)
    repeat = CapturingProvider(workspace_root=tmp_path)
    apply(rows, repeat)
    assert repeat.calls == []
    archived["content"] = archived["content"].replace("后部外币折算政策标记", "后部外币政策修订标记")
    archived["content_hash"] = sha256(archived["content"].encode()).hexdigest()
    revised = CapturingProvider(workspace_root=tmp_path)
    apply(rows, revised)
    assert len(revised.calls) == 3
    assert all("后部外币政策修订标记" in shared_record(call)["finding"] for call in revised.calls)
