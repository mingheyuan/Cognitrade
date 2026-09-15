"""Regression boundary: frozen evidence -> independent artifacts -> Agent input."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from domain_packs.finance_ashare.subagents import runtime
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    _dimension_context,
)
from research_system.market_data.report_inputs import (
    ReportInputError,
    slots_for_manifest,
)
from tests.test_market_data_report_inputs import (
    _ArtifactBackedCollectContext,
    _manifest,
)


def research_ref(index=1, **extra):
    return {
        "type": "market_data_evidence", "id": f"proof-{index}",
        "provider": "official_archive", "source_kind": "research_document",
        "url": f"https://example.org/archives/report-{index}.pdf",
        "title": f"历史研究原文 {index}", "content": f"已冻结正文与摘录 {index}",
        "content_hash": str(index) * 64, "content_ref": f"archive/{index}.pdf",
        "evidence_mode": "public_archive_reconstruction",
        "proof_basis": {"document_id": str(index), "version": "published_original"},
        **extra,
    }


def test_frozen_references_survive_collect_artifact_and_actual_agent_input():
    manifest = _manifest("600519.SH")
    for index, snapshot in enumerate(manifest["items"][0]["snapshots"]):
        snapshot["source_refs"] = [research_ref(1), research_ref(2)]
        snapshot["query_manifest"] = {"pit_mode": "historical_reconstruction"}
    original = deepcopy(manifest)
    context = _ArtifactBackedCollectContext(slots_for_manifest(manifest))
    context.node.inputs["intent"] = "uzi_panel_only"  # This fixture has the two required inputs.
    context.call_tool = Mock(side_effect=AssertionError("report cannot fetch data"))
    output = runtime.collect_snapshot(context)
    artifacts = [SimpleNamespace(**row.model_dump(), artifact_id=f"artifact-{i}")
                 for i, row in enumerate(output.artifacts)]
    assert len([row for row in artifacts if row.artifact_type == "snapshot"]) == 2
    for row in artifacts:
        if row.artifact_type == "snapshot":
            assert all(ref in row.content["source_refs"] for ref in original["items"][0]["snapshots"][0]["source_refs"])
            assert row.content["source_refs"] == row.source_refs
    agent_context = SimpleNamespace(
        node=SimpleNamespace(inputs={"research_profile": "medium"}),
        artifacts=lambda kind: [row for row in artifacts if row.artifact_type == kind],
    )
    with patch.object(runtime, "execute_native_analysis", return_value={"results": []}) as analyze:
        runtime.execute_uzi_native_full_analysis(agent_context)
    rows, symbols = analyze.call_args.args
    assert len(rows) == 2
    assert len(symbols) == 1
    assert all(research_ref(1) in row["source_refs"] for row in rows)
    assert manifest == original
    context.call_tool.assert_not_called()


@pytest.mark.parametrize("snapshot_type,dimension", [("industry", "7_industry"), ("events", "15_events")])
def test_archived_documents_reach_qualitative_prompt(snapshot_type, dimension):
    snapshot = {
        "snapshot_type": snapshot_type, "symbol": "688041.SH", "facts": {},
        "query_manifest": {"pit_mode": "historical_reconstruction"},
        "source_refs": [research_ref(1), research_ref(2)],
    }
    original = deepcopy(snapshot)
    content = runtime._snapshot_artifact_content(snapshot)
    actual = _dimension_context([content], dimension)
    assert actual["allowed_urls"] == [ref["url"] for ref in original["source_refs"]]
    assert [ref["finding"] for ref in actual["evidence_records"]] == [ref["content"] for ref in original["source_refs"]]
    assert [ref["content_hash"] for ref in actual["evidence_records"]] == [ref["content_hash"] for ref in original["source_refs"]]
    content["source_refs"][0]["proof_basis"]["version"] = "modified"
    assert snapshot == original


def test_reference_deduplication_keeps_distinct_evidence_uses_and_uri_only_refs():
    first = research_ref(1, relation_id="first", field_path="facts.a", purpose="support")
    second = research_ref(1, relation_id="second", field_path="facts.b", purpose="contrast")
    uri_only = {"type": "document", "uri": "https://example.org/document"}
    refs = runtime._snapshot_artifact_source_refs({
        "symbol": "688041.SH", "snapshot_type": "industry",
        "source_refs": [first, second, deepcopy(first), uri_only],
    })
    assert refs[:3] == [first, second, uri_only]
    assert len(refs) == 4  # Three fixed uses plus a local snapshot provenance ref.


def test_symbol_aliases_do_not_add_fake_missing_dimensions():
    rows = [{"symbol": "688041.SH", "snapshot_type": f"dimension-{i}", "facts": {"i": i}} for i in range(22)]
    result = runtime._ensure_requested_snapshot_records(
        rows, requested_types=[row["snapshot_type"] for row in rows],
        symbols=["688041", "688041.SH"], sector=None, intent="stock_check",
    )
    assert result == rows


@pytest.mark.parametrize("left,right", [("BRK.A", "BRK.B"), ("00700.HK", "00700.US"), ("000001", "00001.HK")])
def test_missing_dimension_detection_does_not_merge_other_markets(left, right):
    result = runtime._ensure_requested_snapshot_records(
        [{"symbol": left, "snapshot_type": "market", "facts": {"price": 1}}],
        requested_types=["market"], symbols=[right], sector=None, intent="stock_check",
    )
    assert len(result) == 2
    assert result[1]["symbol"] == right


def test_pinned_old_runtime_is_rejected_before_transport_or_provider_calls():
    manifest = _manifest("600519")
    manifest["workflow_pins"] = {"workflow_commit": "old", "model_id": "model", "prompt_hash": "0" * 64}
    context = _ArtifactBackedCollectContext(slots_for_manifest(manifest))
    context.call_tool = Mock(side_effect=AssertionError("cannot collect"))
    with pytest.raises(ReportInputError, match="执行版本"):
        runtime.collect_snapshot(context)
    context.call_tool.assert_not_called()


def test_restored_snapshots_cannot_bypass_version_guard_before_model_calls():
    context = _ArtifactBackedCollectContext({"report_data_manifest": {
        "workflow_pins": {"workflow_commit": "old", "model_id": "model", "prompt_hash": "0" * 64},
    }})
    with patch.object(runtime, "execute_native_analysis") as analyze:
        with pytest.raises(ReportInputError, match="执行版本"):
            runtime.execute_uzi_native_full_analysis(context)
    analyze.assert_not_called()


def test_transport_source_is_in_published_execution_hash():
    from research_system.strategies.runtime_pins import deployed_prompt_hash

    original_reader = Path.read_bytes
    target = Path(runtime.__file__).resolve()
    deployed_prompt_hash.cache_clear()
    baseline = deployed_prompt_hash()
    try:
        with patch.object(Path, "read_bytes", lambda path: original_reader(path) + (b"\n# transport change" if path == target else b"")):
            deployed_prompt_hash.cache_clear()
            assert deployed_prompt_hash() != baseline
    finally:
        deployed_prompt_hash.cache_clear()


def test_collector_capability_version_invalidates_old_artifact_cache():
    import yaml

    from research_system.runtime.execution.core.graph_runner import ExecutionGraphRunner
    from research_system.schemas.execution_graph import ExecutionNode

    manifest = yaml.safe_load((Path(runtime.__file__).parents[1] / "domain.yaml").read_text())
    capability = next(row for row in manifest["capabilities"] if row["capability"] == "finance.ashare.snapshot.collect")
    assert capability["capability_version"] == "2"
    runner = object.__new__(ExecutionGraphRunner)
    snapshot = SimpleNamespace(graph=SimpleNamespace(domain_id="finance_ashare", metadata={}))
    node = ExecutionNode(
        node_id="collector", node_kind="subagent", executor="stock_snapshot_collector",
        capability=capability["capability"], cache_scope="run",
        metadata={"capability_version": capability["capability_version"], "policy_version": capability["policy_version"]},
    )
    old = node.model_copy(update={"metadata": {"capability_version": "1"}})
    assert runner._cache_context(snapshot, node, []) != runner._cache_context(snapshot, old, [])


def test_native_node_receives_frozen_request_for_resume_identity_guard():
    """The real runner only exposes declared ports, unlike loose test contexts."""
    import yaml

    manifest = yaml.safe_load((Path(runtime.__file__).parents[1] / "domain.yaml").read_text())
    capability = next(row for row in manifest["capabilities"]
                      if row["capability"] == "finance.uzi.native.analyze_stock")
    assert capability["capability_version"] == "2"
    assert {"artifact_type": "user_request", "required": False, "multiple": False} in capability["consumes"]
