"""Independent compile -> real persistence audit, with no source/model calls.

Server-resolved identifiers and offsets are not semantic acceptance or write
authority. These tests deliberately exercise the unchanged persistence gate.
"""

from copy import deepcopy

import pytest

from research_system.market_data import research_enrichment, research_suggestions
from research_system.market_data.contracts import canonical_hash
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.repository import MarketDataRepository
from tests.market_data.test_company_profile_revision import persist, proposal_for, scope_for, seed
from tests.market_data.test_pit_core import session as session  # noqa: PLC0414
from tests.market_data.test_research_archive import LATER
from tests.market_data.test_research_enrichment import source as source  # noqa: PLC0414


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("independent suggestion audit must not call sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.mark.parametrize("damage,reason", [
    ("authorization", "snapshot_outside_requested_scope"),
    ("old_value", "old_value_mismatch"),
    ("snapshot_hash", "input_hash_mismatch"),
    ("body", "excerpt_mismatch"),
    ("value", "value_not_grounded"),
    ("unit", "unit_not_grounded"),
    ("period", "period_after_cutoff"),
])
def test_compiled_fact_does_not_bypass_actual_persist_gate(source, damage, reason):
    database, snapshot_id, original_proposal = source
    with database.session() as db:
        repo = MarketDataRepository(db)
        snapshot = repo.require_snapshot(snapshot_id)
        original_hash, original_payload = snapshot.content_hash, deepcopy(snapshot.payload_json)
        document = repo.list_snapshot_evidence(snapshot.id)[0]
        inputs = {"7_industry": {"snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash,
            "revision_allowed": True, "raw": deepcopy(snapshot.payload_json["raw"]),
            "documents": [{"evidence_id": document.id, "document_hash": document.content_hash,
                           "inline_content": document.inline_content}]}}
        suggestion = {key: original_proposal[key] for key in (
            "snapshot_id", "field", "new_value", "unit", "period_text", "finding")}
        suggestion["citations"] = [{"evidence_id": document.id, "quote": original_proposal["citations"][0]["quote"]}]
        row = inputs["7_industry"]
        if damage == "old_value":
            row["raw"]["growth"] = "not the stored old value"
        elif damage == "snapshot_hash":
            row["snapshot_hash"] = "a" * 64
        elif damage == "body":
            # A valid-looking hash plus attacker-supplied model view is not
            # enough: persistence reads the real frozen document again.
            row["documents"][0]["inline_content"] = "伪前缀" + document.inline_content
        elif damage == "value":
            suggestion["new_value"] = "95%"
        elif damage == "unit":
            suggestion["unit"] = "亿元"
        elif damage == "period":
            suggestion["period_text"] = "2027年"
        compiled = research_suggestions.resolve_fact_suggestions([suggestion], inputs=inputs)
        assert compiled["rejected"] == [] and len(compiled["proposals"]) == 1
        applied = research_enrichment.persist_fact_revisions(
            repo, proposals=compiled["proposals"], registry=DEFAULT_DIMENSION_REGISTRY,
            job_id="independent-offline-audit", now=LATER,
            allowed_snapshot_ids=set() if damage == "authorization" else {snapshot_id},
        )
        assert applied["revisions"] == [] and applied["rejected"][0]["reason"] == reason
        assert snapshot.content_hash == original_hash and snapshot.payload_json == original_payload


@pytest.mark.parametrize("damage,reason", [
    ("authorization", "profile_target_not_authorized"),
    ("scope", "scope_binding_changed"),
    ("classification", "classification_evidence_required"),
    ("value", "value_not_grounded"),
])
def test_compiled_profile_does_not_bypass_fixed_scope_or_semantic_gate(session, tmp_path, damage, reason):
    state = seed(session, tmp_path, full=True)
    scope = scope_for(state)
    proposed = proposal_for(state, scope)
    old_hash, old_payload = state.basic.content_hash, deepcopy(state.basic.payload_json)
    suggestion = {key: proposed[key] for key in ("field", "new_value", "industry_basis")}
    suggestion["citations"] = [{key: ref[key] for key in ("source_snapshot_id", "evidence_id", "quote")}
                               for ref in proposed["citations"]]
    if damage == "scope":
        scope["target"]["raw"]["industry"] = "forged previous industry"
        scope["scope_hash"] = canonical_hash({key: value for key, value in scope.items() if key != "scope_hash"})
    elif damage == "classification":
        suggestion["industry_basis"] = "classification"
        suggestion["classification_system"] = "申万"
    elif damage == "value":
        suggestion["new_value"] = "原文没有的行业"
    compiled = research_suggestions.resolve_profile_suggestions([suggestion], scope=scope)
    assert compiled["rejected"] == [] and len(compiled["proposals"]) == 1
    assert compiled["proposals"][0]["citations"][0]["start"] > len(state.proof.inline_content)
    result = persist(state, scope, compiled["proposals"],
                     authorized_basic_snapshot_ids=set() if damage == "authorization" else {state.basic.id})
    assert result["revisions"] == [] and result["rejected"][0]["reason"] == reason
    assert state.basic.content_hash == old_hash and state.basic.payload_json == old_payload


def test_semantic_binding_limit_is_not_misrepresented_as_fact_correctness(source):
    database, snapshot_id, original = source
    with database.session() as db:
        repo = MarketDataRepository(db)
        snapshot = repo.require_snapshot(snapshot_id)
        evidence = repo.list_snapshot_evidence(snapshot.id)[0]
        frozen = {"7_industry": {"snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash,
            "revision_allowed": True, "raw": deepcopy(snapshot.payload_json["raw"]),
            "documents": [{"evidence_id": evidence.id, "document_hash": evidence.content_hash,
                           "inline_content": evidence.inline_content}]}}
        # The same quote says growth 25%, concentration 40%. Existing lexical
        # validation cannot establish which number belongs to which field.
        wrong = {key: original[key] for key in ("snapshot_id", "field", "unit", "period_text", "finding")}
        wrong.update(new_value="40%", citations=[{"evidence_id": evidence.id, "quote": original["citations"][0]["quote"]}])
        compiled = research_suggestions.resolve_fact_suggestions([wrong], inputs=frozen)
        assert compiled["rejected"] == []
        applied = research_enrichment.persist_fact_revisions(repo, proposals=compiled["proposals"],
            registry=DEFAULT_DIMENSION_REGISTRY, job_id="lexical-limit-audit", now=LATER,
            allowed_snapshot_ids={snapshot_id})
        assert applied["rejected"] == [] and len(applied["revisions"]) == 1
        # This records an existing content-audit limitation, not approval of
        # the wrong finding and not an additional guarantee of this adapter.
        revision = repo.require_snapshot(applied["revisions"][0]["snapshot_id"])
        assert revision.payload_json["raw"]["growth"] == "40%"
