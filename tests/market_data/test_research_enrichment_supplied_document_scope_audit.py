"""Offline regression for supplied-document reuse, not live failure attribution.

The two positive cases first failed before the DC research citation view
shared explicitly supplied documents; immutable red XML records that state.
They do not loosen persistence authority or certify finding semantics.
"""

import json
from copy import deepcopy
from hashlib import sha256
from time import monotonic
from types import SimpleNamespace

import pytest

from research_system.market_data import research_enrichment as enrichment
from research_system.market_data.contracts import cutoff_at_for_date
from tests.market_data.research_suggestion_fixtures import profile_assessments
from tests.market_data.test_company_profile_revision import scope_for, seed
from tests.market_data.test_pit_core import AS_OF
from tests.market_data.test_pit_core import session as session  # noqa: PLC0414

LABEL = "B · Industry-Events"
DIMENSIONS = ("7_industry", "15_events")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("citation-scope audit must not call real sources or models")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.fixture
def frozen(session, tmp_path):
    state = seed(session, tmp_path)
    document = enrichment._research_documents(
        state.source, [state.proof], cutoff_at_for_date(AS_OF, "Asia/Shanghai"),
    )[0]
    inputs = {
        dimension: {
            "snapshot_id": state.source.id if dimension == "7_industry" else "fixed-events",
            "snapshot_hash": state.source.content_hash if dimension == "7_industry" else "a" * 64,
            "revision_allowed": dimension == "7_industry",
            "raw": deepcopy(state.source.payload_json["raw"]) if dimension == "7_industry" else {},
            "as_of_date": AS_OF.isoformat(),
            "documents": [deepcopy(document)] if dimension == "7_industry" else [],
            "allowed_urls": [document["url"]] if dimension == "7_industry" else [],
        }
        for dimension in DIMENSIONS
    }
    return SimpleNamespace(state=state, document=document, inputs=inputs, scope=scope_for(state))


class CapturingProvider:
    available = True
    settings = SimpleNamespace(uzi_llm_transient_retries=0)

    def __init__(self, document, *, cited_url=None, proposals=()):
        self.calls = []
        self.document = document
        self.cited_url = cited_url or document["url"]
        self.proposals = list(proposals)

    def parse(self, **kwargs):
        self.calls.append({key: value for key, value in kwargs.items()
                           if key in {"schema", "system_prompt", "user_prompt"}})
        response = {
            "research": {
                "agent_reviewed": True, "group_label": LABEL,
                "dimensions": [{
                    "dimension_id": dimension,
                    "evidence": [{
                        "source": "离线固定公司原文",
                        "url": self.cited_url,
                        "finding": finding,
                        "retrieved_at": self.document["retrieved_at"],
                    } for finding in ("公司自述所属行业为集成电路设计。", "公司主营集成电路芯片设计及销售。")],
                    "associations": [],
                    "conclusion": "离线引用目录回归；结构可用不代表该维度分析已完成。",
                } for dimension in DIMENSIONS],
            },
            "proposals": deepcopy(self.proposals), "unresolved": [],
        }
        if kwargs["schema"] is enrichment.CompanyResearchSuggestionGroup:
            response.update(company_profile_proposals=[], company_profile_assessments=profile_assessments([]))
        return response


def request(inputs, provider):
    before = deepcopy(inputs)
    result = enrichment.request_fact_proposals(
        provider, group_label=LABEL, dimensions=DIMENSIONS, inputs=inputs,
        deadline_at=monotonic() + 30,
    )
    assert len(provider.calls) == 1
    assert inputs == before, "prompt sharing must not mutate fixed input or write authority"
    return result


def test_same_source_role_guard_applies_to_dc_and_report(frozen):
    class MisattributingProvider(CapturingProvider):
        def parse(self, **kwargs):
            result = super().parse(**kwargs)
            result["research"]["dimensions"][0]["evidence"][0]["finding"] = (
                "本次固定数据未完成行业—原材料—合约映射，未执行价格查询。"
            )
            return result

    result = request(frozen.inputs, MisattributingProvider(frozen.document))
    assert result["status"] == "model_failed"
    assert result["failure"]["validation_reasons"] == [
        {"field": "dimensions.0.evidence", "code": "qualitative_source_attribution_conflict"},
    ]
    assert result["proposals"] == []


def assert_ungrounded(result):
    assert result["status"] == "model_failed", result
    assert result["failure"]["validation_reasons"] == [
        {"field": "dimensions.0.evidence", "code": "qualitative_ungrounded_url"},
    ]
    assert result["proposals"] == []


def test_supplied_group_document_can_be_reused_by_partner_dimension(frozen):
    provider = CapturingProvider(frozen.document)
    result = request(frozen.inputs, provider)
    prompt = provider.calls[0]["user_prompt"]
    assert frozen.document["inline_content"] in prompt
    assert provider.calls[0]["schema"] is enrichment.ResearchSuggestionGroup
    assert result["status"] == "completed", result
    assert result["proposals"] == []


def test_explicit_profile_scope_document_is_available_for_group_research(frozen):
    # The real PIT/hash-validated scope contains the full text. It is not a
    # READ_ONLY_RESEARCH_CONTEXT snippet and needs no additional download.
    frozen.inputs["7_industry"].update(documents=[], allowed_urls=[])
    frozen.inputs["_company_profile_scope"] = frozen.scope
    provider = CapturingProvider(frozen.document)
    result = request(frozen.inputs, provider)
    prompt = provider.calls[0]["user_prompt"]
    assert frozen.document["inline_content"] in prompt.split("COMPANY_PROFILE_SCOPE=", 1)[1]
    assert provider.calls[0]["schema"] is enrichment.CompanyResearchSuggestionGroup
    assert result["status"] == "completed", result
    assert len(result["company_profile_assessments"]) == 3
    assert result["company_profile_proposals"] == []
    view = json.loads(prompt.split("COMPANY_PROFILE_SCOPE=", 1)[1])
    assert view["documents"][0]["retrieved_at"] == frozen.document["retrieved_at"]
    assert view["documents"][0]["retrieved_at"] == frozen.state.proof.observed_at.date().isoformat()


@pytest.mark.parametrize("origin", ["read_only_background", "raw_snippet", "other_group_document", "external_url"])
def test_urls_not_supplied_as_group_or_profile_documents_remain_rejected(frozen, origin):
    url = "https://archive.example/not-supplied/other.pdf"
    if origin == "read_only_background":
        frozen.inputs["_read_only_context"] = {"dimensions": {"0_basic": {"source_url": url}}}
    elif origin == "raw_snippet":
        frozen.inputs["7_industry"]["raw"]["snippet"] = {"url": url, "content": "仅背景片段"}
    elif origin == "other_group_document":
        frozen.inputs["3_macro"] = {"documents": [{**frozen.document, "url": url}], "allowed_urls": [url]}
    provider = CapturingProvider(frozen.document, cited_url=url)
    result = request(frozen.inputs, provider)
    if origin in {"other_group_document", "external_url"}:
        assert url not in provider.calls[0]["user_prompt"]
    assert_ungrounded(result)


@pytest.mark.parametrize("suffix", ["#page=1", "?download=1"])
def test_unrecorded_url_aliases_are_not_assumed_equivalent(frozen, suffix):
    assert_ungrounded(request(frozen.inputs, CapturingProvider(
        frozen.document, cited_url=frozen.document["url"] + suffix,
    )))


def test_research_citation_sharing_does_not_authorize_partner_fact_writes(frozen):
    frozen.inputs["15_events"].update(
        documents=[deepcopy(frozen.document)], allowed_urls=[frozen.document["url"]],
    )
    proposal = {
        "snapshot_id": "fixed-events", "field": "catalyst", "new_value": "集成电路设计",
        "unit": "文本", "period_text": "2026年上半年", "finding": "离线未授权伙伴字段提案。",
        "citations": [{"evidence_id": frozen.document["evidence_id"], "quote": frozen.document["inline_content"]}],
    }
    provider = CapturingProvider(frozen.document, proposals=[proposal])
    result = request(frozen.inputs, provider)
    assert result["status"] == "completed", result
    assert result["proposals"] == []
    assert result["proposal_rejections"] == [{
        "proposal_kind": "fact", "suggestion_index": 0, "reason": "snapshot_outside_requested_scope",
    }]
    assert provider.calls[0]["schema"] is enrichment.ResearchSuggestionGroup
    assert "company_profile_assessments" not in result


def test_profile_prompt_dedup_retains_exact_text_reference_without_expanding_inputs(frozen):
    frozen.inputs["15_events"].update(
        documents=[deepcopy(frozen.document)], allowed_urls=[frozen.document["url"]],
    )
    frozen.inputs["_company_profile_scope"] = frozen.scope
    provider = CapturingProvider(frozen.document)
    result = request(frozen.inputs, provider)
    assert result["status"] == "completed", result
    view = json.loads(provider.calls[0]["user_prompt"].split("COMPANY_PROFILE_SCOPE=", 1)[1])
    assert "inline_content" not in view["documents"][0]
    assert view["documents"][0]["text_reference"] == {
        "dimension": "7_industry", "evidence_id": frozen.document["evidence_id"],
    }
    assert result["company_profile_proposals"] == []


@pytest.mark.parametrize("missing", ["url", "finding"])
def test_shared_document_does_not_remove_factual_evidence_required_fields(frozen, missing):
    class MissingFieldProvider(CapturingProvider):
        def parse(self, **kwargs):
            response = super().parse(**kwargs)
            response["research"]["dimensions"][0]["evidence"][0][missing] = " " if missing == "finding" else None
            return response

    result = request(frozen.inputs, MissingFieldProvider(frozen.document))
    assert result["status"] == "model_failed"
    assert result["failure"]["validation_reasons"][0]["code"] == "qualitative_factual_evidence_incomplete"
    assert result["proposals"] == []


@pytest.mark.parametrize("omitted", [False, True], ids=["null", "omitted"])
def test_shared_document_optional_retrieval_metadata_never_invents_date(frozen, omitted):
    # Native Task 2.5 lists URL/finding as factual requirements; retrieved_at
    # appears in examples only. This path has no archive-binding parse context,
    # so absent display metadata stays absent rather than using today/as_of.
    class MissingRetrievalProvider(CapturingProvider):
        def parse(self, **kwargs):
            response = super().parse(**kwargs)
            evidence = response["research"]["dimensions"][0]["evidence"][0]
            if omitted:
                evidence.pop("retrieved_at")
            else:
                evidence["retrieved_at"] = None
            return response

    result = request(frozen.inputs, MissingRetrievalProvider(frozen.document))
    assert result["status"] == "completed", result
    evidence = result["research"]["dimensions"][0]["evidence"][0]
    assert evidence["url"] == frozen.document["url"]
    assert evidence["finding"] == "公司自述所属行业为集成电路设计。"
    assert evidence["retrieved_at"] is None
    assert frozen.inputs["7_industry"]["documents"][0]["retrieved_at"] == frozen.document["retrieved_at"]
    assert result["proposals"] == []


def test_urlless_unknown_remains_a_disclosure_not_a_new_factual_citation(frozen):
    class UnknownProvider(CapturingProvider):
        def parse(self, **kwargs):
            response = super().parse(**kwargs)
            for item in response["research"]["dimensions"][1]["evidence"]:
                item.update(source="unknown", url=None, retrieved_at=None)
                item["finding"] = "所给档案未足以验证这一事项：" + item["finding"]
            return response

    result = request(frozen.inputs, UnknownProvider(frozen.document))
    assert result["status"] == "completed", result
    disclosures = result["research"]["dimensions"][1]["evidence"]
    assert all(item["source"] == "unknown" and item["url"] is None for item in disclosures)
    assert result["proposals"] == []


def test_safe_unbound_diagnostic_keeps_actual_dimension_not_just_model_array_index(frozen):
    url = "https://foreign.example/model-only-private-fragment"

    class ReorderedProvider(CapturingProvider):
        def parse(self, **kwargs):
            response = super().parse(**kwargs)
            response["research"]["dimensions"].reverse()
            response["research"]["dimensions"][1]["evidence"][0]["url"] = url
            return response

    result = request(frozen.inputs, ReorderedProvider(frozen.document))
    assert result["status"] == "model_failed"
    assert result["failure"]["validation_reasons"] == [
        {"field": "dimensions.1.evidence", "code": "qualitative_ungrounded_url"},
    ]
    assert result["citation_rejections"] == [
        {"dimension_id": "7_industry", "url_sha256": sha256(url.encode()).hexdigest()},
    ]
    assert result["citation_rejection_count"] == 1
    assert "model-only-private-fragment" not in str(result)
    assert "research" not in result  # Failed answers are not silently stored.


def test_catalog_receipt_is_bounded_and_binds_the_supplied_snapshot(frozen):
    first = request(frozen.inputs, CapturingProvider(frozen.document))["research_citation_scope"]
    assert set(first) == {"version", "document_binding_count", "url_count", "input_hash"}
    assert first["version"] == enrichment.RESEARCH_CITATION_VIEW_VERSION
    assert first["document_binding_count"] == first["url_count"] == 1
    assert frozen.document["url"] not in str(first)
    frozen.inputs["7_industry"]["snapshot_hash"] = "f" * 64
    second = request(frozen.inputs, CapturingProvider(frozen.document))["research_citation_scope"]
    assert first["input_hash"] != second["input_hash"]
