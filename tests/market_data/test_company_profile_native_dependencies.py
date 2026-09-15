"""Profile invalidation follows pinned native reads, not broad topic relevance."""

from copy import deepcopy

import pytest

from research_system.market_data.contracts import canonical_hash
from research_system.market_data.ingestion import SnapshotIngestionService
from tests.market_data.test_company_profile_revision import (
    VALUES, persist, proposal_for, scope_for, seed,
)
from tests.market_data.test_pit_core import _input
from tests.market_data.test_pit_core import session as session  # noqa: PLC0414


INDUSTRY_DEPENDENTS = {
    "3_macro", "4_peers", "7_industry", "8_materials", "9_futures",
    "10_valuation", "13_policy", "similar_stocks",
}
NAME_DEPENDENTS = {"4_peers", "14_moat", "15_events", "17_sentiment", "18_trap"}
DERIVED_STAGES = {"native_modeling", "qualitative_research", "native_panel", "native_review"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("dependency tests must not access sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def revise(session, tmp_path, fields, *, old_industry=None):
    state = seed(session, tmp_path)
    if old_industry is not None:
        payload = deepcopy(state.basic.payload_json)
        payload["raw"]["industry"] = old_industry
        payload["_uzi_raw_result"]["data"]["industry"] = old_industry
        state.basic = SnapshotIngestionService(state.repo).ingest(_input("0_basic", payload=payload))
    scope = scope_for(state)
    proposals = [proposal_for(state, scope, field) for field in fields]
    result = persist(state, scope, proposals)
    assert result["rejected"] == []
    revision = state.repo.require_snapshot(result["revisions"][0]["snapshot_id"])
    return state, scope, proposals, result, revision


@pytest.mark.parametrize("field,old_industry,required", [
    ("industry", "综合", INDUSTRY_DEPENDENTS),
    ("industry", "另一具体行业", INDUSTRY_DEPENDENTS),
    ("industry", None, INDUSTRY_DEPENDENTS | {"16_lhb"}),
    ("industry", "", INDUSTRY_DEPENDENTS | {"16_lhb"}),
    ("industry", " ", INDUSTRY_DEPENDENTS),  # native truthiness, not a new trimming rule
    ("name", None, NAME_DEPENDENTS),
    ("main_business", None, set()),
])
def test_only_actual_changed_field_consumers_require_rebuild(session, tmp_path, field, old_industry, required):
    _, _, _, result, revision = revise(session, tmp_path, [field], old_industry=old_industry)
    summary = result["rebuild_required"]
    assert set(summary["direct_dimensions"]) == required
    assert set(summary["required_dimensions"]) == required
    assert summary["review_dimensions"] == []
    assert set(summary["derived_stages"]) == DERIVED_STAGES
    assert "5_chain" not in summary["required_dimensions"]
    assert revision.query_manifest_json["company_profile_revision"]["rebuild_required"] == summary
    assert result["ready_to_freeze"] is False


def test_simultaneous_changes_union_real_consumers_without_topic_review_gate(session, tmp_path):
    _, _, _, result, _ = revise(session, tmp_path, list(VALUES), old_industry="综合")
    summary = result["rebuild_required"]
    assert set(summary["direct_dimensions"]) == INDUSTRY_DEPENDENTS | NAME_DEPENDENTS
    assert summary["required_dimensions"] == summary["direct_dimensions"]
    assert summary["required_dimensions"][-1] == "similar_stocks"
    assert summary["review_dimensions"] == []


def test_dependency_rule_and_summary_are_bound_into_new_revision_fingerprint(session, tmp_path):
    state, _, proposals, result, revision = revise(session, tmp_path, ["main_business"])
    summary = result["rebuild_required"]
    assert summary["version"] == "native-profile-fetch-dependencies-v1"
    marker = revision.query_manifest_json["company_profile_revision"]
    expected = {
        "version": "company-profile-revision-v1",
        "basic_input": {"snapshot_id": state.basic.id, "snapshot_hash": state.basic.content_hash},
        "profile": {field: revision.payload_json["raw"].get(field) for field in VALUES},
        "source_inputs": [(state.source.id, state.source.content_hash)],
        "facts": marker["facts"],
        "rebuild_required": summary,
    }
    assert result["dependency_fingerprint"] == canonical_hash(expected)
    assert result["dependency_fingerprint"] != canonical_hash({
        key: value for key, value in expected.items() if key != "rebuild_required"
    })
    assert proposals[0]["field"] == "main_business"
