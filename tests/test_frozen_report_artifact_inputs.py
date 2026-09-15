"""Trusted preflight slots, not stale nested requests, seed frozen artifacts."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from domain_packs.finance_ashare.subagents import runtime
from research_system.runtime.agent_os import AgentOS
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.protocol import RequestEnvelope

FROZEN_KEYS = ("snapshots", "source_refs", "market_data_bundle", "report_data_manifest")


def materialize_request_artifact(tmp_path, request):
    agent = object.__new__(AgentOS)
    agent._prepared_run_ids = set()
    agent.run_store = SimpleNamespace(
        create_run=Mock(
            return_value=(
                "new-run",
                SimpleNamespace(root=tmp_path),
                None,
            )
        )
    )
    agent._write_graph_agent_state = Mock()
    agent._build_initial_graph = Mock(
        return_value=SimpleNamespace(
            graph_id="graph",
            domain_id="finance_ashare",
            objective="frozen report",
            metadata={},
        )
    )
    agent._run_graph_runner = Mock(side_effect=lambda **kw: kw["seed_artifacts"][0])
    artifact = agent._execute_graph_task(
        request,
        intake_decision=SimpleNamespace(status="accepted"),
    )
    assert isinstance(artifact, ArtifactDraft)
    assert artifact.artifact_type == "user_request"
    return artifact


def request_with_shadow(*, frozen):
    recording = {
        "binding": {"version": "native-quant-aux-v1", "input_hash": "fixed"},
        "receipt": None,
        "responses": {},
    }
    manifest = {
        "manifest_id": "new-fixed",
        "workflow_pins": {
            "native_quant_auxiliary_version": "native-quant-aux-v1",
        },
        "items": [
            {
                "symbol": "688041.SH",
                "context": {
                    "native_quant_auxiliary": recording["binding"],
                },
                "native_quant_auxiliary_recording": recording,
            }
        ],
    }
    return RequestEnvelope(
        intent="stock_check",
        user_query="生成冻结报告",
        constraints={"data_source_mode": "bundle_only"} if frozen else {},
        slots={
            "snapshots": [{"snapshot_id": "new-snapshot"}],
            "source_refs": [{"id": "new-proof"}],
            "market_data_bundle": {"bundle_id": "new-bundle"},
            "report_data_manifest": manifest,
            "user_request": {
                "snapshots": [{"snapshot_id": "old-snapshot"}],
                "source_refs": [{"id": "old-proof"}],
                "market_data_bundle": {"bundle_id": "old-bundle"},
                "report_data_manifest": {
                    "manifest_id": "old-unpinned",
                    "workflow_pins": {},
                },
                "query": "用户保留的显示标题",
                "custom_field": "keep",
            },
        },
    )


def test_new_frozen_manifest_overrides_stale_nested_input_in_real_artifact(tmp_path):
    request = request_with_shadow(frozen=True)
    original = deepcopy(request.model_dump(mode="json"))
    artifact = materialize_request_artifact(tmp_path, request)
    assert all(artifact.content[key] == request.slots[key] for key in FROZEN_KEYS)
    context = SimpleNamespace(
        node=SimpleNamespace(inputs={}),
        artifacts=lambda kind: [artifact] if kind == "user_request" else [],
    )
    frozen = runtime._frozen_quant_auxiliary_inputs(context)
    assert (
        frozen["688041.SH"]["recording"]
        == request.slots["report_data_manifest"]["items"][0][
            "native_quant_auxiliary_recording"
        ]
    )
    assert artifact.content["custom_field"] == "keep"
    assert artifact.content["query"] == "用户保留的显示标题"
    assert request.model_dump(mode="json") == original


def test_non_frozen_nested_input_keeps_original_precedence(tmp_path):
    request = request_with_shadow(frozen=False)
    artifact = materialize_request_artifact(tmp_path, request)
    assert all(
        artifact.content[key] == request.slots["user_request"][key]
        for key in FROZEN_KEYS
    )


@pytest.mark.parametrize("missing", FROZEN_KEYS)
def test_missing_trusted_frozen_field_cannot_fall_back_to_nested_old_data(
    tmp_path, missing
):
    request = request_with_shadow(frozen=True)
    request.slots.pop(missing)
    original = deepcopy(request.model_dump(mode="json"))
    with pytest.raises(
        ValueError, match=f"nested {missing} has no trusted top-level binding"
    ):
        materialize_request_artifact(tmp_path, request)
    assert request.model_dump(mode="json") == original
