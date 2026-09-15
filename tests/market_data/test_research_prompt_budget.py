"""DC prompt transport must not spend its budget on repeated bodies/holes."""

import json
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from research_system.market_data import research_enrichment as enrichment
from research_system.market_data.contracts import canonical_hash
from research_system.market_data.research_prompt_views import (
    _keyed_tables, _same_value_references, background_view, reference_matching_raw,
)
from domain_packs.finance_ashare.subagents.uzi_input_views import compact_evidence_view
from tests.market_data.research_prompt_fixtures import restore_background
from tests.market_data.test_research_enrichment import _response


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("prompt regression must not request live data or models")
    monkeypatch.setattr("socket.socket.connect", deny)
    monkeypatch.setattr("socket.create_connection", deny)


def document(key, text, original_hash=None):
    return {"evidence_id": key, "document_hash": original_hash or sha256(text.encode()).hexdigest(),
            "inline_content": text, "url": f"https://example.test/{key}.pdf",
            "retrieved_at": "2026-09-12", "published_at": "2026-08-20T00:00:00+00:00"}


def inputs_for(documents):
    dimensions = ("8_materials", "9_futures")
    rows = {key: {"snapshot_id": key, "snapshot_hash": canonical_hash(key),
                  "revision_allowed": True, "raw": {"materials": "铜", "price": 0, "missing": None},
                  "as_of_date": "2026-09-02", "documents": docs,
                  "allowed_urls": [doc["url"] for doc in docs]}
            for key, docs in zip(dimensions, documents, strict=True)}
    rows["_read_only_context"] = {
        "symbol": "688012.SH", "as_of_date": "2026-09-02",
        "cutoff_at": "2026-09-02T15:59:59.999999+00:00",
        "snapshot_inputs": [{"dimension": key, "snapshot_id": row["snapshot_id"],
                             "content_hash": row["snapshot_hash"]} for key, row in rows.items()],
        "dimensions": {key: {"data": deepcopy(row["raw"]), "error": "partial source error",
                             "_timeout": True, "source": "historical_test"} for key, row in rows.items()},
    }
    return rows


def capture(monkeypatch, inputs):
    calls = []
    def parse(provider, **kwargs):
        calls.append(deepcopy(kwargs))
        response = _response(None, inputs["8_materials"]["documents"][0]["url"])
        response["research"]["group_label"] = "C · Cost-Transmission"
        for row, key in zip(response["research"]["dimensions"], ("8_materials", "9_futures"), strict=True):
            row["dimension_id"] = key
        return response
    monkeypatch.setattr(enrichment, "parse_uzi_structured", parse)
    before = deepcopy(inputs)
    result = enrichment.request_fact_proposals(SimpleNamespace(available=True),
        group_label="C · Cost-Transmission", dimensions=("8_materials", "9_futures"),
        inputs=inputs, deadline_at=None)
    assert inputs == before
    return result, calls


def group_view(call):
    return json.loads(call["user_prompt"].split("\n本组证据=", 1)[1].split("\nREAD_ONLY_RESEARCH_CONTEXT=", 1)[0])


def test_same_original_body_is_sent_once_without_losing_binding_or_tail(monkeypatch):
    annual = document("annual", "年报完整原文" * 32_000 + "年报末尾：毛利率25%。")
    half = document("half", "半年报完整原文" * 27_000 + "半年报末尾：公司未使用衍生工具。")
    inputs = inputs_for(([annual, half], [deepcopy(half)]))
    result, calls = capture(monkeypatch, inputs)
    assert len(calls) == 1, result
    assert result["status"] == "completed", result
    prompt = calls[0]["user_prompt"]
    assert prompt.count(half["inline_content"]) == 1
    assert prompt.count(annual["inline_content"]) == 1
    rows = group_view(calls[0])
    shared = rows["9_futures"]["documents"][0]
    assert shared["text_reference"]["dimension"] == "8_materials"
    assert shared["text_reference"]["document_index"] == 1
    assert {k: v for k, v in shared.items() if k != "text_reference"} == {
        k: v for k, v in half.items() if k != "inline_content"}


def test_large_price_arrays_do_not_expand_omission_markers(monkeypatch):
    inputs = inputs_for(([document("one", "完整原文")], []))
    context = inputs["_read_only_context"]
    context["dimensions"]["10_valuation"] = {"data": {"history": list(range(30_000))}}
    result, calls = capture(monkeypatch, inputs)
    assert len(calls) == 1, result
    view = json.loads(calls[0]["user_prompt"].split("\nREAD_ONLY_RESEARCH_CONTEXT=", 1)[1])
    sampled = view["dimensions"]["10_valuation"]["data"]["history"]["__uzi_indexed_sequence__"]
    assert sampled["original_length"] == 30_000
    assert sampled["items"]["0"] == 0
    assert sampled["items"]["29999"] == 29999
    assert set(sampled["items"]) == {str(i) for i in [*range(10), *range(29990, 30000)]}
    assert len(calls[0]["user_prompt"]) < 30_000


def test_same_pdf_hash_different_extractions_are_not_merged(monkeypatch):
    first = document("legacy", "旧提取文本", "f" * 64)
    second = document("full", "旧提取文本；新增完整正文。", "f" * 64)
    result, calls = capture(monkeypatch, inputs_for(([first], [second])))
    assert result["status"] == "completed", result
    view = group_view(calls[0])
    assert view["8_materials"]["documents"][0]["inline_content"] == first["inline_content"]
    assert view["9_futures"]["documents"][0]["inline_content"] == second["inline_content"]


def test_distinct_oversized_originals_still_fail_before_model(monkeypatch):
    inputs = inputs_for(([document("one", "一" * 260_000)], [document("two", "二" * 260_000)]))
    result, calls = capture(monkeypatch, inputs)
    assert calls == []
    assert result["failure"]["error_type"] == "UziInputBudgetExceeded"
    assert result["status"] == "input_invalid"
    assert result["model_request_started"] is False


@pytest.mark.parametrize("keyed", [False, True])
def test_lossless_tables_keep_types_dates_empty_values_and_original_order(keyed):
    rows = [{"report_date": "2026-06-30", "quarter_name": "2026二季度", "unit": "万元",
             "value": value, "old_value": None, "empty": ""}
            for value in [False, 0, 0.0, True, 1, 1.0, None, "", "0", "-2.39亿"]]
    source = {"financial": {f"line/{i}~": row for i, row in enumerate(rows)} if keyed else rows}
    converted = _keyed_tables(source)
    assert converted != source
    assert canonical_hash(restore_background(converted)) == canonical_hash(source)


def test_exact_value_references_are_reversible_with_escaped_keys():
    record = {"series": ["历史数据" * 150, 0, False, None, "", 3.2]}
    source = {"a/~": deepcopy(record), "another": deepcopy(record),
              "different": {"series": ["历史数据" * 150, False, 0, None, "", 3.2]}}
    converted = _same_value_references(source)
    assert converted["another"] == {"__uzi_same_value_reference__": "/a~1~0"}
    assert "__uzi_same_value_reference__" not in converted["different"]
    assert canonical_hash(restore_background(converted)) == canonical_hash(source)
    assert source["another"] == record


def test_background_transport_roundtrips_old_sampled_financial_view():
    inputs = inputs_for(([document("one", "完整原文")], []))
    original = inputs["_read_only_context"]
    original["dimensions"]["1_financials"] = {"data": {
        "observations": [{"period": "2026-06-30", "description": "分部业务说明" * 200,
                          "value": i, "flag": False, "empty": None} for i in range(100)],
        "summary": {"net_profit": 0, "verified": False, "unknown": None}},
        "error": "original_error", "_timeout": True, "source": "bound_source"}
    original["dimensions"]["12_capital_flow"] = {"data": {"amount": 100},
        "error": "provider_unavailable", "_timeout": True, "source": "capital_source"}
    before = deepcopy(original)
    view = background_view(original, enrichment_version="test")
    restored = restore_background(view)
    expected = compact_evidence_view(original["dimensions"]["1_financials"],
                                     max_sequence_items=20, max_text_chars=800)
    assert canonical_hash(restored["dimensions"]["1_financials"]) == canonical_hash(expected)
    assert restored["snapshot_inputs"] == original["snapshot_inputs"]
    assert view["prompt_view"]["fixed_input_hash"] == canonical_hash(original)
    capital = restored["dimensions"]["12_capital_flow"]
    assert capital == {"error": "provider_unavailable", "_timeout": True, "source": "capital_source",
                       "facts_view_status": "outside_company_background_view_not_missing"}
    assert original == before


@pytest.mark.parametrize("mismatch", ["snapshot_id", "snapshot_hash", "raw", "missing"])
def test_group_raw_reference_never_substitutes_different_or_unbound_input(mismatch):
    inputs = inputs_for(([document("one", "完整原文")], []))
    context = inputs["_read_only_context"]
    group = {"8_materials": deepcopy(inputs["8_materials"])}
    if mismatch == "snapshot_id":
        context["snapshot_inputs"][0]["snapshot_id"] = "other"
    elif mismatch == "snapshot_hash":
        context["snapshot_inputs"][0]["content_hash"] = "0" * 64
    elif mismatch == "raw":
        context["dimensions"]["8_materials"]["data"]["price"] = False
    else:
        context["dimensions"].pop("8_materials")
    assert reference_matching_raw(deepcopy(group), context) == group
