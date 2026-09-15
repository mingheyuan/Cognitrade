"""Frozen-report progress never adds a model request to the analysis path."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from research_system.runtime.agent_os import AgentOS
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.schemas.execution_graph import ExecutionGraph, ExecutionNode


def make_runner(tmp_path, monkeypatch, payload, events=None):
    import research_system.runtime.agent_os as module

    agent = object.__new__(AgentOS)
    agent.run_store = SimpleNamespace(load_run_paths=lambda _: SimpleNamespace(
        root=tmp_path / "run", request=tmp_path / "run" / "request.json",
    ))
    agent.registry = DomainPackRegistry.load(Path(__file__).resolve().parents[1] / "domain_packs")
    agent.plan_agent = agent.memory = None
    agent.settings = SimpleNamespace(memory_root=tmp_path / "memory")
    agent.provider = SimpleNamespace(parse=Mock(return_value={"analysis": "unchanged"}))
    agent.main_agent = SimpleNamespace(
        observe_graph_progress=Mock(return_value={"current": "模型进度摘要", "next": "继续处理结果"}),
        tools=SimpleNamespace(invoke=Mock(return_value=SimpleNamespace(status="completed", output={"ok": True}, error=None))),
    )
    monkeypatch.setattr(module, "read_json", lambda _: payload)
    runner = agent._runner("run", event_callback=events.append if events is not None else None)
    return agent, runner


def test_validated_completion_counts_survive_event_and_latest_projection(tmp_path, monkeypatch):
    events = []
    _agent, runner = make_runner(tmp_path, monkeypatch, {"constraints": {"data_source_mode": "bundle_only"}}, events)
    runner._emit_observer_progress({
        "node_id": "test_node", "phase": "uzi_native_analysis",
        "current_action": "UZI 第 1 轮已完成 6/9：buy_zone_youzi",
        "next_action": "继续剩余结构化阶段及最终自审",
        "stage_index": 9, "stage_total": 9, "completed_stages": 6,
        "stage_phase": "validated", "progress_percent": 55,
        "progress_scope": "stock_agent_stages", "workflow_phase": "analysis",
        "workflow_completed_stages": 14, "workflow_total_stages": 17,
        "analysis_cycle": 2,
        "request_timing": {"executor_wait_seconds": 40, "queue_seconds": 2,
                           "stage_elapsed_seconds": 42, "remaining_budget_seconds": 1158},
    })
    latest = json.loads((runner.run_root / "execution_progress.json").read_text())
    event = next(row for row in events if row.get("event_type") == "agent_progress")
    for row in (latest, event):
        assert row["stage_index"] == 9  # Ordinal remains metadata.
        assert row["completed_stages"] == 6
        assert row["stage_phase"] == "validated"
        assert row["progress_percent"] == 55
        assert row["progress_scope"] == "stock_agent_stages"
        assert row["workflow_phase"] == "analysis"
        assert row["workflow_completed_stages"] == 14
        assert row["workflow_total_stages"] == 17
        assert row["analysis_cycle"] == 2
        assert row["request_timing"]["executor_wait_seconds"] == 40


def test_runtime_recovery_cycle_does_not_retain_previous_cycle_high_watermark(monkeypatch):
    from domain_packs.finance_ashare.subagents import runtime
    events = []
    context = SimpleNamespace(node=SimpleNamespace(node_id="native", inputs={"research_profile": "medium"}),
                              artifacts=lambda _: [], progress_emitter=events.append)
    monkeypatch.setattr(runtime, "_assert_frozen_workflow_identity", lambda _: None)
    monkeypatch.setattr(runtime, "_snapshot_context", lambda _: ([], ["600000"], "", [], []))

    def analysis(*args, progress_callback, **kwargs):
        progress_callback({"stage": "analysis_cycle", "cycle": 1, "progress_percent": 5})
        progress_callback({"stage": "self_review", "cycle": 1, "progress_percent": 90})
        progress_callback({"stage": "analysis_cycle", "cycle": 2, "progress_percent": 5})
        progress_callback({"progress_scope": "stock_agent_stages", "workflow_phase": "qualitative",
                           "workflow_completed_stages": 0, "workflow_total_stages": 17,
                           "cycle": 2, "progress_percent": 8})
        return {"results": []}

    monkeypatch.setattr(runtime, "execute_native_analysis", analysis)
    runtime.execute_uzi_native_full_analysis(context)
    assert [row["progress_percent"] for row in events] == [5, 90, 5, 8]
    assert events[-1]["workflow_completed_stages"] == 0
    assert events[-1]["analysis_cycle"] == 2


@pytest.mark.parametrize("wrapped", [False, True])
def test_bundle_only_disables_progress_observer_and_preserves_identity(tmp_path, monkeypatch, wrapped):
    request = {"constraints": {"data_source_mode": "bundle_only"}, "user_id": "user", "tenant_id": "tenant"}
    payload = {"request": request} if wrapped else request
    original = deepcopy(payload)
    agent, runner = make_runner(tmp_path, monkeypatch, payload)
    assert runner.progress_observer is None
    assert runner._observe_main_agent_progress({"event": "subagent_completed"}) is None
    agent.main_agent.observe_graph_progress.assert_not_called()
    assert runner.llm_provider is agent.provider
    assert (runner._user_id, runner._tenant_id) == ("user", "tenant")
    assert payload == original


@pytest.mark.parametrize("payload", [
    None, {}, [], "legacy", {"request": None}, {"request": []}, {"request": {}},
    {"constraints": None}, {"constraints": []}, {"constraints": "bundle_only"},
    {"constraints": {"data_source_mode": "live"}},
    {"request": {"constraints": {"data_source_mode": "live"}}},
])
def test_non_frozen_and_incomplete_requests_preserve_default_observer(tmp_path, monkeypatch, payload):
    agent, runner = make_runner(tmp_path, monkeypatch, payload)
    assert runner.progress_observer is agent.main_agent.observe_graph_progress
    assert runner._observe_main_agent_progress({"event": "subagent_completed"})["current"] == "模型进度摘要"
    agent.main_agent.observe_graph_progress.assert_called_once()


@pytest.mark.parametrize("frozen", [True, False])
def test_real_runner_tool_and_node_progress_without_extra_frozen_model_calls(tmp_path, monkeypatch, frozen):
    events = []
    constraints = {"data_source_mode": "bundle_only"} if frozen else {}
    agent, runner = make_runner(tmp_path, monkeypatch, {"request": {"constraints": constraints}}, events)
    # Use a non-finance domain to prove selection depends only on the request
    # mode. No actual skill executor, network or model exists in this test.
    runner.skill_runtime_factory = None
    seen_contexts = []

    def executor(context):
        seen_contexts.append(context)
        assert context.call_tool("test.echo").output == {"ok": True}
        assert context.llm_provider.parse() == {"analysis": "unchanged"}
        return {"messages": ["结构化节点已完成。"], "artifacts": []}

    monkeypatch.setattr(agent.registry, "resolve_executor", lambda *_args, **_kwargs: executor)
    graph = ExecutionGraph(domain_id="legal_contract", objective="Test progress only", nodes=[
        ExecutionNode(node_id="test_node", node_kind="subagent", executor="legal_clause_extractor",
                      capability="legal.contract.clause.extract", tools=["test.echo"]),
    ], governance={"allowed_tools": ["test.echo"], "cache_enabled": False, "idempotency_required": False})
    result = runner.run(graph=graph, run_id="run", thread_id="thread", request_id="request", seed_artifacts=[])
    assert result.snapshot.status == "completed"
    assert len(seen_contexts) == 1
    agent.provider.parse.assert_called_once()  # Actual analysis provider remains usable.
    agent.main_agent.tools.invoke.assert_called_once()
    progress = [event for event in events if event.get("phase") == "subagent_result"]
    assert len(progress) == 1
    assert progress[0]["current_action"] and progress[0]["next_action"]
    if frozen:
        assert seen_contexts[0].progress_observer is None
        agent.main_agent.observe_graph_progress.assert_not_called()
        assert progress[0]["progress_source"] == "runtime"
        assert not [event for event in events if event.get("progress_source") == "llm"]
    else:
        assert seen_contexts[0].progress_observer is agent.main_agent.observe_graph_progress
        assert {call.args[0]["event"] for call in agent.main_agent.observe_graph_progress.call_args_list} == {
            "tool_completed", "subagent_completed",
        }
        assert progress[0]["progress_source"] == "llm"
