import json
from copy import deepcopy

import pytest

from domain_packs.finance_ashare.subagents.uzi_frozen_research import research_documents
from research_system.runtime.providers.codex_frozen_tools import FrozenToolBridge
from research_system.runtime.providers.errors import CodexTurnError, LLMCancelledError
from research_system.runtime.providers.frozen_documents import (
    FrozenDocuments,
    digest,
    encoded,
)


def test_large_text_is_searchable_and_readable_to_the_last_character():
    text = "冻结原文，" * 200_000 + "末尾关键事实：亏损三亿元。"
    source = {"text": text}
    corpus = FrozenDocuments([("annual_report", text), ("same_body_other_binding", text)], input_hash="fixed")
    session = corpus.session()
    source["text"] = "changed"
    assert corpus.identity["documents"] == 1
    match = session.call("frozen_search", {"query": "亏损", "document_id": "", "offset": 0})["matches"][0]
    assert match["start"] > 999_000
    chunk = session.call("frozen_read", {"document_id": match["document_id"], "offset": match["start"], "length": 6000})
    assert chunk["text"].endswith("亏损三亿元。") and chunk["next_offset"] is None
    assert chunk["sha256"] == digest(text)
    assert session.audit()["coverage"][0]["materialized_text_fully_read"] is False
    assert corpus.session().audit()["calls"] == []


def test_all_text_can_be_reassembled_without_loss_or_overlapping_coverage():
    text = "正文汉字\n" * 2200
    session = FrozenDocuments([("text", text)], input_hash="f").session()
    key, offset, chunks = digest(text), 0, []
    while offset is not None:
        result = session.call("frozen_read", {"document_id": key, "offset": offset, "length": 6000})
        chunks.append(result["text"])
        offset = result["next_offset"]
    session.call("frozen_read", {"document_id": key, "offset": 0, "length": 100})
    assert "".join(chunks) == text
    assert session.audit()["coverage"][0]["read_characters"] == len(text)
    assert session.audit()["coverage"][0]["materialized_text_fully_read"]
    assert not session.audit()["original_document_fully_read_claimed"]


@pytest.mark.parametrize("name,args", [
    ("shell", {}), ("frozen_list", {"offset": -1}), ("frozen_list", {"offset": True}),
    ("frozen_read", {"document_id": "/etc/passwd", "offset": 0, "length": 10}),
    ("frozen_read", {"document_id": "", "offset": 0, "length": 10}),
    ("frozen_read", {"document_id": "", "offset": 0, "length": 6001}),
    ("frozen_list", {"offset": 0, "path": "../secret"}),
    ("frozen_search", {"query": " ", "document_id": "", "offset": 0}),
])
def test_no_paths_network_or_unbounded_arguments(name, args):
    with pytest.raises(ValueError, match="frozen_"):
        FrozenDocuments([], input_hash="f").session().call(name, args)


def test_search_is_literal_paginated_and_catalog_is_bounded():
    text = "a.*b " * 30
    session = FrozenDocuments([("long label" * 300, text)], input_hash="f").session()
    first = session.call("frozen_search", {"query": ".*", "document_id": "", "offset": 0})
    assert len(first["matches"]) == 12 and first["next_offset"] == 12
    second = session.call("frozen_search", {"query": ".*", "document_id": "", "offset": 12})
    assert second["matches"][0]["end"] >= first["matches"][0]["end"]
    assert len(encoded(session.call("frozen_list", {"offset": 0}))) < 1200


def test_externalization_deduplicates_body_but_retains_bindings_and_changes_identity():
    body = "原文" * 5000
    docs = [{"url": "https://example.test/one", "finding": body, "archive_bindings": [{"id": "a"}]},
            {"url": "https://example.test/two", "finding": body, "archive_bindings": [{"id": "b"}]}]
    before = deepcopy(docs)
    context = {"fixed_input_hash": "f", "symbol": "600000"}
    corpus = research_documents([], context, docs, {})
    assert docs == before
    assert len([key for key in corpus._bodies if key == digest(body)]) == 1
    assert '"id":"a"' in "".join(corpus._bodies.values())
    assert '"id":"b"' in "".join(corpus._bodies.values())
    docs[1]["finding"] += "新增事实"
    assert research_documents([], context, docs, {}).identity != corpus.identity


def test_bridge_rejects_cross_thread_unknown_and_approval_requests():
    for method, params in [
        ("item/tool/call", {"threadId": "wrong", "callId": "x", "tool": "frozen_list", "arguments": {"offset": 0}}),
        ("item/tool/call", {"threadId": "t", "callId": "x", "tool": "shell", "arguments": {}}),
        ("item/commandExecution/requestApproval", {}),
    ]:
        bridge = FrozenToolBridge(FrozenDocuments([], input_hash="f").session(), lambda: None)
        bridge.thread_id = "t"
        response = bridge.handle(method, params)
        assert isinstance(bridge.failure, CodexTurnError)
        assert response.get("decision") != "accept"


def test_bridge_records_only_executed_calls_and_honors_cancellation():
    session = FrozenDocuments([("label", "body")], input_hash="f").session()
    bridge = FrozenToolBridge(session, lambda: None)
    bridge.thread_id = "t"
    result = bridge.handle("item/tool/call", {"threadId": "t", "callId": "x", "tool": "frozen_list", "arguments": {"offset": 0}})
    assert result["success"] and json.loads(result["contentItems"][0]["text"])["documents"]
    bridge.verify_completed({"id": "x", "tool": "frozen_list"})
    with pytest.raises(CodexTurnError):
        bridge.verify_completed({"id": "fake", "tool": "frozen_list"})
    def cancelled():
        raise LLMCancelledError("cancelled")
    bridge.check = cancelled
    bridge.handle("item/tool/call", {"threadId": "t", "callId": "y", "tool": "frozen_read", "arguments": {}})
    assert isinstance(bridge.failure, LLMCancelledError)
    assert session.audit()["tool_calls"] == 1
