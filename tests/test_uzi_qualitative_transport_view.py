"""Report prompts must not repeat materializer audit/transport payloads."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as q
from tests.test_uzi_qualitative_company_document_context import (
    CapturingProvider,
    apply,
    attach_shared_document,
    company_context,
    fixed_rows,
)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("prompt projection must not fetch sources or call a live model")
    monkeypatch.setattr("socket.socket.connect", blocked)
    monkeypatch.setattr("socket.getaddrinfo", blocked)


def transport_rows():
    rows = fixed_rows()
    # Real materialized rows repeat the same native root in every dimension,
    # once directly and again inside the normalized audit payload.
    transport = {"fund_managers": {"present": True, "value": [
        {"code": str(index), "description": "固定基金信息" * 160} for index in range(1000)
    ]}, "similar_stocks": {"present": True, "value": {"error": "recorded timeout", "_timeout": True}}}
    for row in rows:
        facts = row["facts"]
        facts["_uzi_raw_result"].update(source="fixed_provider", error="original_partial", _timeout=False)
        facts["_uzi_top_level_fields"] = deepcopy(transport)
        facts["_uzi_root_fields"] = {"ticker": {"present": True, "value": row["symbol"]}}
        facts["_market_data_audit"] = {
            "normalized_payload": deepcopy(facts), "evidence_grade": "A",
            "validation": {"strict_complete": False, "missing_fields": ["known_gap"]},
        }
        facts["_unknown_provider_field"] = {"new_fact": 12.5}
    document = attach_shared_document(rows)
    document["content"] = "固定正文" * 40000 + "FINAL_FROZEN_PAGE"
    document["content_hash"] = sha256(document["content"].encode()).hexdigest()
    return rows


def test_realistic_transport_does_not_exhaust_qualitative_budget_or_cut_documents():
    rows = transport_rows()
    provider = CapturingProvider()
    result = apply(rows, provider)
    assert result["qualitative_agent_execution"]["status"] == "completed"
    assert len(provider.calls) == 3
    for call in provider.calls:
        assert len(call["system_prompt"]) + len(call["user_prompt"]) < 500_000
        context = company_context(call)
        # One shared root value, explicit references instead of per-row copies.
        transports = context["shared_native_transport"]
        assert len(transports) == 2
        assert sum("fund_managers" in item for item in transports.values()) == 1
        assert "recorded timeout" in json.dumps(transports)
        docs = json.loads(call["system_prompt"].split("FROZEN_STOCK_DOCUMENTS=", 1)[1].split("\nFROZEN_EVIDENCE_BUNDLE=", 1)[0])
        assert any(item["finding"].endswith("FINAL_FROZEN_PAGE") and len(item["finding"]) > 160000 for item in docs)
        for row in context["snapshots"]:
            facts = row["facts"]
            assert facts["_uzi_raw_result"]["source"] == "fixed_provider"
            assert facts["_uzi_raw_result"]["error"] == "original_partial"
            assert facts["_uzi_raw_result"]["_timeout"] is False
            assert facts["_unknown_provider_field"] == {"new_fact": 12.5}
            assert "normalized_payload" not in facts["_market_data_audit"]
            assert facts["_market_data_audit"]["validation"]["missing_fields"] == ["known_gap"]
            metadata = row["materialized_transport_view"]
            assert metadata["normalized_payload"]["content_hash"]
            assert all(ref in transports for ref in metadata["native_transport_refs"].values())


def test_different_native_root_versions_are_retained_not_first_value_wins():
    rows = transport_rows()
    rows[1]["facts"]["_uzi_top_level_fields"]["similar_stocks"]["value"] = []
    _, context, _ = q._fixed_report_inputs(rows, {})
    transports = context["shared_native_transport"]
    assert len(transports) == 3
    values = [item["similar_stocks"]["value"] for item in transports.values() if "similar_stocks" in item]
    assert [] in values and {"error": "recorded timeout", "_timeout": True} in values


def test_audit_only_change_invalidates_cache_without_becoming_report_fact(tmp_path):
    rows = transport_rows()
    first = CapturingProvider(workspace_root=tmp_path)
    apply(rows, first)
    repeated = CapturingProvider(workspace_root=tmp_path)
    apply(rows, repeated)
    assert repeated.calls == []
    rows[0]["facts"]["_market_data_audit"]["normalized_payload"]["hidden_audit_marker"] = "audit_change"
    revised = CapturingProvider(workspace_root=tmp_path)
    apply(rows, revised)
    assert len(revised.calls) == 3
    assert "audit_change" not in revised.calls[0]["system_prompt"]


def test_provider_native_nested_fields_are_not_recursively_stripped():
    rows = fixed_rows()
    native = rows[0]["facts"]["_uzi_raw_result"]["data"]
    native["_market_data_audit"] = {"normalized_payload": {"actual_native_value": 42}}
    native["_uzi_top_level_fields"] = {"actual_native_value": 43}
    before = deepcopy(rows)
    _, context, _ = q._fixed_report_inputs(rows, {})
    assert context["snapshots"][0]["facts"]["_uzi_raw_result"]["data"] == native
    assert rows == before


def test_group_prompt_references_shared_evidence_without_serializing_it_twice():
    provider = CapturingProvider()
    result = apply(fixed_rows(), provider)
    assert result["qualitative_agent_execution"]["status"] == "completed"
    for call in provider.calls:
        shared = json.loads(call["system_prompt"].split("\nFROZEN_EVIDENCE_BUNDLE=", 1)[1])
        assert "\n本组证据=" not in call["user_prompt"]
        references = json.loads(call["user_prompt"].split("\n本组证据引用=", 1)[1])
        assert references["container"] == "FROZEN_EVIDENCE_BUNDLE"
        assert len(references["dimensions"]) == 2
        for dimension in references["dimensions"]:
            assert shared[dimension]["allowed_urls"]
            assert shared[dimension]["evidence_records"]
        assert "NATIVE_TASK_2_5_PROTOCOL_BEGIN" in call["user_prompt"]


def test_flattened_native_facts_share_exact_data_without_losing_types_or_conflicts():
    rows = fixed_rows()
    facts = rows[0]["facts"]
    data = facts["_uzi_raw_result"]["data"]
    data.update(large_peers={"rows": [{"name": "同行", "value": 3}]}, nullable=None,
                boolean=True, numeric=1, conflicting="native")
    facts.update(large_peers=deepcopy(data["large_peers"]), nullable=None,
                 boolean=True, numeric=True, conflicting="normalized")
    before = deepcopy(rows)
    projected, _ = q._materialized_transport_view(rows)
    view = projected[0]
    refs = view["materialized_transport_view"].get("native_data_field_refs", {})
    assert refs == {key: ["_uzi_raw_result", "data", key] for key in ("large_peers", "nullable", "boolean")}
    for key in refs:
        assert key not in view["facts"]
    assert view["facts"]["_uzi_raw_result"] == before[0]["facts"]["_uzi_raw_result"]
    assert view["facts"]["numeric"] is True  # bool and number are not the same JSON value.
    assert view["facts"]["conflicting"] == "normalized"
    assert rows == before


def test_large_full_archive_and_duplicate_native_data_fit_without_truncating_body():
    rows = fixed_rows()
    peers = {str(index): "同行完整字段" + str(index) + "x" * 780 for index in range(120)}
    facts = rows[0]["facts"]
    facts["_uzi_raw_result"]["data"]["peers"] = peers
    facts["peers"] = deepcopy(peers)
    document = attach_shared_document(rows)
    document["content"] = "固定原文内容" * 50_000 + "LAST_FROZEN_PAGE_UNCHANGED"
    document["content_hash"] = sha256(document["content"].encode()).hexdigest()
    provider = CapturingProvider()
    result = apply(rows, provider)
    assert result["qualitative_agent_execution"]["status"] == "completed"
    assert len(provider.calls) == 3
    for call in provider.calls:
        assert len(call["system_prompt"]) + len(call["user_prompt"]) <= 500_000
        docs = json.loads(call["system_prompt"].split("FROZEN_STOCK_DOCUMENTS=", 1)[1].split("\nFROZEN_EVIDENCE_BUNDLE=", 1)[0])
        assert any(item["finding"] == document["content"] for item in docs)
        basic = company_context(call)["snapshots"][0]
        assert basic["facts"]["_uzi_raw_result"]["data"]["peers"] == peers
        assert basic["materialized_transport_view"]["native_data_field_refs"]["peers"] == ["_uzi_raw_result", "data", "peers"]
