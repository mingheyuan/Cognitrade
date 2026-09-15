"""Resolve model suggestions against frozen inputs; never expand write scope."""

import importlib
from copy import deepcopy
from hashlib import sha256

import pytest
from pydantic import ValidationError

from research_system.market_data.contracts import canonical_hash
from tests.market_data.test_research_enrichment import source as source  # noqa: PLC0414

QUOTE = "2026年上半年，公司采购晶圆，成本占比40%。"
PREFIX = "🚀e\u0301甲\n"
TEXT = PREFIX + QUOTE + "\n公司名称为新名称，属于集成电路设计行业。"


def module():
    return importlib.import_module("research_system.market_data.research_suggestions")


def inputs():
    return {"8_materials": {
        "snapshot_id": "material", "snapshot_hash": "a" * 64,
        "raw": {"core_material": None}, "revision_allowed": True,
        "documents": [{"evidence_id": "proof", "document_hash": "b" * 64, "inline_content": TEXT}],
    }}


def fact(**changes):
    return {"snapshot_id": "material", "field": "core_material", "new_value": "晶圆",
            "unit": "文本", "period_text": "2026年上半年", "finding": "原文披露公司采购晶圆",
            "citations": [{"evidence_id": "proof", "quote": QUOTE}], **changes}


def profile_scope():
    scope = {
        "version": "company-profile-revision-v1", "read_only": True,
        "target": {"snapshot_id": "basic", "snapshot_hash": "c" * 64, "raw": {"industry": None}},
        "allowed_fields": ["name", "industry", "main_business"],
        "source_snapshots": [{"snapshot_id": "source", "snapshot_hash": "d" * 64}],
        "documents": [{"source_snapshot_id": "source", "source_snapshot_hash": "d" * 64,
                       "evidence_id": "proof", "document_hash": "e" * 64, "inline_content": TEXT,
                       "text_content_hash": sha256(TEXT.encode()).hexdigest()}],
    }
    return {**scope, "scope_hash": canonical_hash(scope)}


def profile(**changes):
    return {"field": "industry", "new_value": "集成电路设计", "industry_basis": "issuer_disclosure",
            "citations": [{"source_snapshot_id": "source", "evidence_id": "proof",
                           "quote": "公司名称为新名称，属于集成电路设计行业。"}], **changes}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("suggestion resolution must not contact a source or model")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def test_models_exclude_server_known_fields_and_forbid_extras():
    m = module()
    assert m.SUGGESTIONS_VERSION == "uzi-research-suggestions-v1"
    for cls in (m.FactSuggestion, m.ProfileSuggestion):
        fields = cls.model_json_schema()["properties"]
        assert not {"old_value", "old_exists", "snapshot_hash", "start", "end"} & fields.keys()
        assert cls.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError):
        m.FactSuggestion.model_validate(fact(old_exists=False))
    with pytest.raises(ValidationError):
        m.FactSuggestion.model_validate(fact(citations=[{"evidence_id": "proof", "quote": QUOTE, "start": 0}]))
    with pytest.raises(ValidationError):
        m.ProfileSuggestion.model_validate(profile(snapshot_id="model-target"))


@pytest.mark.parametrize("value", ["40", 40, 40.5, True, None])
def test_fact_scalar_types_are_preserved_not_coerced(value):
    result = module().FactSuggestion.model_validate(fact(new_value=value)).new_value
    assert result == value and type(result) is type(value)


@pytest.mark.parametrize("value", [["晶圆"], {"value": "晶圆"}])
def test_compound_values_are_not_a_patch_protocol(value):
    with pytest.raises(ValidationError):
        module().FactSuggestion.model_validate(fact(new_value=value))
    with pytest.raises(ValidationError):
        module().ProfileSuggestion.model_validate(profile(new_value=123))


@pytest.mark.parametrize("exists", [True, False])
def test_fact_old_null_is_distinct_from_missing_and_offsets_are_unicode_exact(exists):
    frozen = inputs()
    if not exists:
        frozen["8_materials"]["raw"] = {}
    before = deepcopy(frozen)
    result = module().resolve_fact_suggestions([fact()], inputs=frozen)
    assert result["rejected"] == []
    p = result["proposals"][0]
    assert p["old_exists"] is exists and p["old_value"] is None
    assert p["snapshot_hash"] == "a" * 64 and p["dimension"] == "8_materials"
    assert p["period_start"] == "2026-01-01" and p["period_end"] == "2026-06-30"
    assert p["unit"] == "文本" and p["new_value"] == "晶圆"
    assert p["citations"] == [{"evidence_id": "proof", "document_hash": "b" * 64,
                                "start": len(PREFIX), "end": len(PREFIX) + len(QUOTE), "quote": QUOTE}]
    assert frozen == before


@pytest.mark.parametrize("allowed", [False, None, "true", 1])
def test_only_explicit_authorized_target_can_produce_fact(allowed):
    frozen = inputs()
    frozen["8_materials"]["revision_allowed"] = allowed
    result = module().resolve_fact_suggestions([fact()], inputs=frozen)
    assert result == {"proposals": [], "rejected": [{"suggestion_index": 0, "reason": "snapshot_outside_requested_scope"}]}


@pytest.mark.parametrize("change,reason", [
    ({"snapshot_id": "other"}, "snapshot_outside_requested_scope"),
    ({"field": "linked_contract"}, "field_not_supported"),
    ({"field": "raw.core_material"}, "field_not_supported"),
    ({"period_text": "报告期"}, "period_not_supported_or_mismatch"),
    ({"period_text": "2026-02-30"}, "period_not_supported_or_mismatch"),
    ({"citations": [{"evidence_id": "other", "quote": QUOTE}]}, "evidence_not_bound"),
    ({"citations": [{"evidence_id": "proof", "quote": QUOTE.replace("，", " ")}]}, "quote_not_found"),
])
def test_fact_failures_have_only_static_codes(change, reason):
    result = module().resolve_fact_suggestions([fact(**change)], inputs=inputs())
    assert result == {"proposals": [], "rejected": [{"suggestion_index": 0, "reason": reason}]}


def test_other_dimensions_documents_do_not_extend_citation_authority():
    frozen = inputs()
    frozen["9_futures"] = {"snapshot_id": "futures", "snapshot_hash": "c" * 64,
                            "raw": {}, "revision_allowed": True,
                            "documents": [{"evidence_id": "other", "document_hash": "d" * 64, "inline_content": TEXT}]}
    result = module().resolve_fact_suggestions([fact(citations=[{"evidence_id": "other", "quote": QUOTE}])], inputs=frozen)
    assert result["rejected"][0]["reason"] == "evidence_not_bound"


@pytest.mark.parametrize("text,quote", [(TEXT + QUOTE, QUOTE), ("aaaa", "aaa")])
def test_duplicate_quotes_including_overlaps_are_not_arbitrarily_located(text, quote):
    frozen = inputs()
    frozen["8_materials"]["documents"][0]["inline_content"] = text
    result = module().resolve_fact_suggestions([fact(citations=[{"evidence_id": "proof", "quote": quote}])], inputs=frozen)
    assert result["rejected"][0]["reason"] == "quote_ambiguous"


def test_profile_identity_old_null_document_hash_and_offsets_come_only_from_scope():
    frozen = profile_scope()
    before = deepcopy(frozen)
    result = module().resolve_profile_suggestions([profile()], scope=frozen)
    assert result["rejected"] == []
    p = result["proposals"][0]
    assert p["snapshot_id"] == "basic" and p["snapshot_hash"] == "c" * 64
    assert p["old_exists"] is True and p["old_value"] is None
    citation = p["citations"][0]
    assert citation["document_hash"] == "e" * 64 and citation["source_snapshot_hash"] == "d" * 64
    assert citation["text_content_hash"] == sha256(TEXT.encode()).hexdigest()
    assert TEXT[citation["start"]:citation["end"]] == citation["quote"]
    assert frozen == before


def test_profile_missing_old_field_and_classification_citations_keep_their_own_source():
    frozen = profile_scope()
    classification = "独立分类将集成电路设计归入申万行业分类。"
    frozen["documents"].append({"source_snapshot_id": "classification", "source_snapshot_hash": "f" * 64,
                               "evidence_id": "classification-proof", "document_hash": "1" * 64,
                               "inline_content": classification, "text_content_hash": sha256(classification.encode()).hexdigest()})
    p = module().resolve_profile_suggestions([profile(
        industry_basis="classification", classification_system="申万行业分类",
        classification_citations=[{"source_snapshot_id": "classification", "evidence_id": "classification-proof", "quote": classification}],
    )], scope=frozen)["proposals"][0]
    assert p["classification_citations"][0]["source_snapshot_hash"] == "f" * 64
    assert p["classification_system"] == "申万行业分类"
    named = module().resolve_profile_suggestions([profile(field="name", new_value="新名称", industry_basis=None)], scope=frozen)["proposals"][0]
    assert named["old_exists"] is False and named["old_value"] is None


@pytest.mark.parametrize("changes,reason", [
    ({"citations": [{"source_snapshot_id": "unbound", "evidence_id": "proof", "quote": QUOTE}]}, "evidence_not_bound"),
    ({"citations": [{"source_snapshot_id": "source", "evidence_id": "proof", "quote": "不存在的敏感文本"}]}, "quote_not_found"),
])
def test_profile_cannot_rebind_source_or_emit_model_text_in_rejection(changes, reason):
    result = module().resolve_profile_suggestions([profile(**changes)], scope=profile_scope())
    assert result == {"proposals": [], "rejected": [{"suggestion_index": 0, "reason": reason}]}


def test_profile_allowed_fields_remain_narrow():
    frozen = profile_scope()
    frozen["allowed_fields"] = ["name"]
    result = module().resolve_profile_suggestions([profile()], scope=frozen)
    assert result["rejected"][0]["reason"] == "field_not_supported"


def test_bad_dto_isolated_from_valid_suggestion_without_echoing_input():
    result = module().resolve_fact_suggestions([fact(unknown="sensitive"), fact()], inputs=inputs())
    assert len(result["proposals"]) == 1
    assert result["rejected"] == [{"suggestion_index": 0, "reason": "suggestion_invalid"}]


def test_resolver_does_not_silently_change_units_or_ground_a_value():
    result = module().resolve_fact_suggestions([fact(new_value="完全未披露材料", unit="亿元")], inputs=inputs())
    assert result["rejected"] == []
    assert result["proposals"][0]["new_value"] == "完全未披露材料"
    assert result["proposals"][0]["unit"] == "亿元"


def test_title_quote_does_not_bypass_existing_profile_value_validator():
    from research_system.market_data.company_profile_revision import (
        CompanyProfileRefusal,
        _validate_proposal,
    )

    frozen = profile_scope()
    p = module().resolve_profile_suggestions([profile(new_value="不存在行业", citations=[{
        "source_snapshot_id": "source", "evidence_id": "proof", "quote": QUOTE,
    }])], scope=frozen)["proposals"][0]
    with pytest.raises(CompanyProfileRefusal, match="value_not_grounded"):
        _validate_proposal(p, frozen)


def test_resolved_excerpt_still_rejected_by_actual_fact_persistence_when_value_unsupported(source):
    from research_system.market_data import research_enrichment
    from research_system.market_data.dimension_contracts import (
        DEFAULT_DIMENSION_REGISTRY,
    )
    from research_system.market_data.repository import MarketDataRepository
    from tests.market_data.test_research_archive import LATER

    database, snapshot_id, _ = source
    with database.session() as session:
        repo = MarketDataRepository(session)
        snapshot = repo.require_snapshot(snapshot_id)
        proof = repo.list_snapshot_evidence(snapshot.id)[0]
        frozen = {"7_industry": {"snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash,
                                  "revision_allowed": True, "raw": deepcopy(snapshot.payload_json["raw"]),
                                  "documents": [{"evidence_id": proof.id, "document_hash": proof.content_hash,
                                                 "inline_content": proof.inline_content}]}}
        original_hash = snapshot.content_hash
        suggestion = fact(snapshot_id=snapshot.id, field="growth", new_value="晶圆", citations=[{
            "evidence_id": proof.id, "quote": proof.inline_content,
        }])
        resolved = module().resolve_fact_suggestions([suggestion], inputs=frozen)
        assert resolved["rejected"] == []  # Binding is not semantic acceptance.
        result = research_enrichment.persist_fact_revisions(
            repo, proposals=resolved["proposals"], registry=DEFAULT_DIMENSION_REGISTRY,
            job_id="offline-suggestion-test", now=LATER, allowed_snapshot_ids={snapshot.id},
        )
        assert result["revisions"] == []
        assert result["rejected"][0]["reason"] == "value_not_grounded"
        assert repo.require_snapshot(snapshot_id).content_hash == original_hash


@pytest.mark.parametrize("quote", ["", " \n\t "])
def test_blank_quotes_rejected_without_trimming_them(quote):
    result = module().resolve_fact_suggestions([fact(citations=[{"evidence_id": "proof", "quote": quote}])], inputs=inputs())
    assert result["rejected"] == [{"suggestion_index": 0, "reason": "suggestion_invalid"}]


def test_unicode_normalization_and_repeated_profile_quotes_are_not_repaired():
    frozen = profile_scope()
    bad = profile(citations=[{"source_snapshot_id": "source", "evidence_id": "proof", "quote": "🚀é甲"}])
    assert module().resolve_profile_suggestions([bad], scope=frozen)["rejected"][0]["reason"] == "quote_not_found"
    frozen["documents"][0]["inline_content"] = TEXT + QUOTE
    repeated = profile(citations=[{"source_snapshot_id": "source", "evidence_id": "proof", "quote": QUOTE}])
    assert module().resolve_profile_suggestions([repeated], scope=frozen)["rejected"][0]["reason"] == "quote_ambiguous"


def test_duplicate_document_binding_and_profile_text_hash_conflict_are_explicit():
    frozen = inputs()
    frozen["8_materials"]["documents"].append(deepcopy(frozen["8_materials"]["documents"][0]))
    assert module().resolve_fact_suggestions([fact()], inputs=frozen)["rejected"][0]["reason"] == "evidence_ambiguous"
    scope = profile_scope()
    scope["documents"][0]["text_content_hash"] = "f" * 64
    assert module().resolve_profile_suggestions([profile()], scope=scope)["rejected"][0]["reason"] == "text_hash_mismatch"
