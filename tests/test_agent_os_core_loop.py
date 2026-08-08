from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
from time import sleep

from research_system.runtime.agent_os import AgentOS
from research_system.api.agent_os_admin_service import AgentOSAdminService
from research_system.runtime.core.approvals import HumanApprovalStore
from research_system.runtime.core.tool_registry import (
    CoreToolContext,
    build_core_tool_registry,
    register_mcp_tools,
)
from research_system.runtime.core.mcp_tool_adapter import invoke_mcp_tool
from research_system.adapters.vendors.mcp_source_registry import (
    McpServerSource,
    McpSourceRegistry,
    McpToolDefinition,
)
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.execution.core.graph_runner import ExecutionGraphRunner
from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.jsonl import read_json, read_jsonl
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.execution_graph import ArtifactPort, ExecutionGraph, ExecutionNode, ExecutionGovernance
from research_system.schemas.core_runtime import CoreToolDefinition
from research_system.schemas.protocol import RequestEnvelope


class _NativeToolProvider:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def complete_with_tools(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "kind": "tool_calls",
                "tool_calls": [
                    {
                        "tool_name": "core.file.read",
                        "arguments": {"path": "note.txt"},
                        "call_id": "model-call-1",
                    }
                ],
                "public_progress": {
                    "current": "The request requires reading the requested workspace file.",
                    "next": "Execute core.file.read and use its returned content.",
                },
            }
        tool_rows = [row for row in kwargs["messages"] if row.get("role") == "tool"]
        return {
            "kind": "message",
            "message": f"模型看到了 {tool_rows[-1]['output']['content']}",
            "public_progress": {
                "current": "The file result is available to the Main Agent.",
                "next": "Deliver the answer with the retrieved content.",
            },
        }


class _ClarifyingProvider:
    available = True

    def complete_with_tools(self, **_kwargs):
        return {
            "kind": "clarification",
            "question": "Which file should I inspect?",
            "missing_fields": ["path"],
        }


class _ProgressSummaryProvider:
    available = True

    def complete(self, *, system_prompt, **_kwargs):
        if "负责交付研究结果" in system_prompt:
            return (
                '{"conclusion":"基于当前证据，估值与现金流信号存在支持，但相对估值仍受样本缺口限制。",'
                '"assistant_message":"我已完成本轮研究，结论仍受同行样本和数据完整性约束。",'
                '"follow_up":["补充同行样本并复核最新披露"]}'
            )
        return '{"current":"已收到快照结果并完成证据整理。","next":"继续检查方法论输出和数据缺口。"}'


class AgentOSCoreLoopTests(unittest.TestCase):
    def _settings(self, root: Path) -> Settings:
        repository_root = Path(__file__).resolve().parents[1]
        return Settings.from_env(repository_root).model_copy(
            update={
                "runs_root": root / "runs",
                "memory_root": root / "memory",
                "domain_packs_root": root / "empty_packs",
                "workspace_root": root / "workspace",
                "openai_api_key": None,
                "openai_base_url": None,
            }
        )

    def test_main_agent_generates_public_graph_progress_and_research_synthesis(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            agent_os = AgentOS(self._settings(Path(temporary_directory)))
            agent_os.main_agent.llm_provider = _ProgressSummaryProvider()

            progress = agent_os.main_agent.observe_graph_progress(
                {
                    "event": "subagent_completed",
                    "node_id": "financial_analyst",
                    "status": "completed",
                    "output_artifact_ids": ["financial-analysis-1"],
                }
            )
            self.assertEqual(progress["current"], "已收到快照结果并完成证据整理。")
            self.assertEqual(progress["next"], "继续检查方法论输出和数据缺口。")

            synthesis = agent_os.main_agent.compose_research_synthesis(
                request="分析 300750",
                report={
                    "conclusion": "fixed fallback",
                    "methodologies": [{"methodology_type": "dcf"}],
                    "risk": ["peer_group_missing"],
                },
            )
            self.assertEqual(
                synthesis["conclusion"],
                "基于当前证据，估值与现金流信号存在支持，但相对估值仍受样本缺口限制。",
            )
            self.assertEqual(synthesis["follow_up"], ["补充同行样本并复核最新披露"])

    def test_core_loop_answers_without_a_domain_pack_or_graph_snapshot(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = self._settings(root)
            agent_os = AgentOS(settings)

            response = agent_os.handle_request(
                RequestEnvelope(user_query="Who are you?")
            )

            self.assertEqual(response.status, "completed")
            self.assertEqual(response.result["mode"], "core")
            self.assertIn("Agent OS", response.message)
            run_root = settings.runs_root / str(response.run_id)
            self.assertFalse((run_root / "execution_snapshot.json").exists())
            self.assertTrue((run_root / "main_agent_state.json").exists())
            self.assertTrue(
                any(
                    event.get("event_type") == "tool_call_completed"
                    for event in read_jsonl(run_root / "execution_events.jsonl")
                )
            )
            core_progress = [
                event
                for event in read_jsonl(run_root / "execution_events.jsonl")
                if event.get("event_type") == "agent_progress"
                and event.get("progress_kind") == "main_agent_loop"
            ]
            self.assertTrue(core_progress)
            self.assertTrue(all(event.get("current_action") for event in core_progress))
            self.assertTrue(all(event.get("next_action") for event in core_progress))
            state = read_json(run_root / "main_agent_state.json")
            self.assertEqual(state["status"], "completed")
            self.assertEqual(len(response.artifacts), 1)
            self.assertEqual(response.artifacts[0]["artifact_type"], "assistant_message")

    def test_request_intake_keeps_basic_chat_in_core_and_escalates_pack_requests(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        settings = Settings.from_env(repository_root)
        agent_os = AgentOS(settings)

        basic = agent_os.request_intake_decision(RequestEnvelope(user_query="你是谁"))
        self.assertEqual(basic.request_kind, "conversation")
        self.assertEqual(basic.intent, "core.conversation.answer")

        professional = agent_os.request_intake_decision(
            RequestEnvelope(user_query="compare one bank")
        )
        self.assertEqual(professional.status, "needs_clarification")
        self.assertEqual(professional.request_kind, "domain_task")
        self.assertEqual(professional.intent, "stock_compare")

    def test_contextual_research_follow_up_stays_in_the_main_agent_core_loop(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = self._settings(root).model_copy(
                update={"domain_packs_root": repository_root / "domain_packs"}
            )
            agent_os = AgentOS(settings)

            response = agent_os.handle_request(
                RequestEnvelope(
                    user_query="\u521a\u521a\u5728\u7814\u7a76\u4ec0\u4e48\uff1f",
                    conversation_context=[
                        {
                            "type": "chat_thread_turn",
                            "role": "user",
                            "turn_type": "research_query",
                            "content": "\u5206\u6790\u5b81\u5fb7\u65f6\u4ee3",
                        },
                        {
                            "type": "chat_thread_turn",
                            "role": "assistant",
                            "turn_type": "research_result",
                            "content": "300750: DCF proxy is above the latest market price.",
                        },
                    ],
                )
            )

            self.assertEqual(response.status, "completed")
            self.assertEqual(response.mode, "core")
            self.assertIn("300750", response.message)
            run_root = settings.runs_root / str(response.run_id)
            self.assertFalse((run_root / "execution_snapshot.json").exists())
            events = read_jsonl(run_root / "execution_events.jsonl")
            event_types = [event["event_type"] for event in events]
            self.assertLess(
                event_types.index("main_agent_started"),
                event_types.index("main_agent_route_decided"),
            )
            self.assertIn("tool_call_completed", event_types)
            self.assertNotIn("graph_run_started", event_types)

    def test_core_tool_registry_records_and_enforces_tool_calls(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "note.txt"
            target.write_text("Agent OS core tool", encoding="utf-8")
            registry = build_core_tool_registry()
            context = CoreToolContext(
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                run_root=root,
                actor="main_agent",
                allowed_tools=frozenset({"core.file.read"}),
            )

            result = registry.invoke(
                tool_name="core.file.read",
                arguments={"path": str(target)},
                context=context,
            )
            rejected = registry.invoke(
                tool_name="core.run.status",
                arguments={},
                context=context,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.output["content"], "Agent OS core tool")
            self.assertEqual(rejected.status, "rejected")
            events = read_jsonl(root / "execution_events.jsonl")
            self.assertEqual(
                [event["event_type"] for event in events],
                [
                    "tool_call_started",
                    "tool_call_completed",
                    "tool_call_rejected",
                ],
            )

    def test_core_skill_invocation_does_not_load_domain_skills_implicitly(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            registry = build_core_tool_registry()
            context = CoreToolContext(
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                run_root=root,
                actor="main_agent",
                allowed_tools=frozenset({"core.skill.invoke"}),
                allowed_permissions=frozenset({"skill:invoke"}),
            )

            result = registry.invoke(
                tool_name="core.skill.invoke",
                arguments={"skill_name": "market_snapshot_skill"},
                context=context,
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("未注册 Core Skill runtime", result.error or "")

    def test_task_mode_uses_core_tool_for_a_file_request_without_a_pack(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "note.txt").write_text("core file answer", encoding="utf-8")
            agent_os = AgentOS(
                self._settings(root).model_copy(update={"workspace_root": workspace})
            )

            response = agent_os.handle_request(RequestEnvelope(user_query="读取文件 note.txt"))

            self.assertEqual(response.status, "completed")
            self.assertIn("core file answer", response.message)
            self.assertEqual(response.result["mode"], "task")

    def test_tool_registry_enforces_timeout_policy(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            registry = build_core_tool_registry()
            registry.register(
                CoreToolDefinition(
                    name="test.slow",
                    description="Slow test tool.",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    timeout_seconds=0.01,
                ),
                lambda _arguments, _context: (sleep(0.1) or {"ok": True}),
            )
            context = CoreToolContext(
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                run_root=root,
                actor="main_agent",
                allowed_tools=frozenset({"test.slow"}),
            )

            result = registry.invoke(
                tool_name="test.slow",
                arguments={},
                context=context,
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("超时", result.error or "")

    def test_native_main_agent_tool_loop_persists_turns_and_results(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "note.txt").write_text("native tool result", encoding="utf-8")
            settings = self._settings(root).model_copy(update={"workspace_root": workspace})
            agent_os = AgentOS(settings)
            provider = _NativeToolProvider()
            agent_os.main_agent.llm_provider = provider

            response = agent_os.handle_request(RequestEnvelope(user_query="读取文件 note.txt"))

            self.assertEqual(response.status, "completed")
            self.assertEqual(response.result["mode"], "task")
            self.assertIn("native tool result", response.message)
            run_root = settings.runs_root / str(response.run_id)
            state = read_json(run_root / "main_agent_state.json")
            self.assertEqual(state["turn_count"], 2)
            self.assertGreaterEqual(len(state["transcript"]), 3)
            events = read_jsonl(run_root / "execution_events.jsonl")
            event_types = [event["event_type"] for event in events]
            self.assertIn("main_agent_turn_started", event_types)
            self.assertIn("main_agent_tool_calls_requested", event_types)
            self.assertIn("main_agent_tool_loop_result", event_types)
            loop_progress = [
                event
                for event in events
                if event.get("event_type") == "agent_progress"
                and event.get("progress_kind") == "main_agent_loop"
            ]
            self.assertTrue(loop_progress)
            self.assertTrue(all(event.get("current_action") for event in loop_progress))
            self.assertTrue(all(event.get("next_action") for event in loop_progress))
            self.assertTrue(any(event.get("phase") == "tool_result" for event in loop_progress))
            self.assertTrue(
                any(
                    event.get("current_action")
                    == "The request requires reading the requested workspace file."
                    for event in loop_progress
                )
            )
            self.assertIn("assistant_message_ready", event_types)
            tool_result_event = next(
                event for event in events if event["event_type"] == "main_agent_tool_loop_result"
            )
            self.assertIn("native tool result", tool_result_event["output_summary"])

    def test_core_tool_approval_is_durable_and_resumes_the_same_turn(self) -> None:
        class ApprovalProvider:
            available = True

            def __init__(self) -> None:
                self.calls = 0

            def complete_with_tools(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "kind": "tool_calls",
                        "tool_calls": [
                            {
                                "tool_name": "core.test.approval",
                                "arguments": {"value": "approved"},
                                "call_id": "approval-call-1",
                            }
                        ],
                    }
                return {"kind": "message", "message": "approved tool completed"}

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = self._settings(root)
            agent_os = AgentOS(settings)
            provider = ApprovalProvider()
            agent_os.main_agent.llm_provider = provider
            calls: list[dict] = []
            agent_os.main_agent.tools.register(
                CoreToolDefinition(
                    name="core.test.approval",
                    description="A test tool requiring human approval.",
                    input_schema={"type": "object", "required": ["value"]},
                    output_schema={"type": "object"},
                    permissions=["test:write"],
                    requires_approval=True,
                ),
                lambda arguments, _context: (calls.append(arguments) or {"ok": True}),
            )

            first = agent_os.handle_request(
                RequestEnvelope(
                    intent="core.conversation.answer",
                    user_query="use the approved tool",
                )
            )
            self.assertEqual(first.status, "waiting_approval")
            self.assertEqual(len(first.pending_approval_ids), 1)
            run_id = str(first.run_id)
            run_root = settings.runs_root / run_id
            approvals = HumanApprovalStore(run_root / "approvals.jsonl")
            approval_id = first.pending_approval_ids[0]
            pending = approvals.get(approval_id)
            self.assertIsNotNone(pending)
            self.assertEqual(pending.status, "pending")
            event_types = [event["event_type"] for event in read_jsonl(run_root / "execution_events.jsonl")]
            self.assertIn("tool_call_waiting_approval", event_types)

            approved = AgentOSAdminService(agent_os).approve(
                run_id,
                node_id="core_tool:core.test.approval",
                capability="core.test.approval",
                approval_id=approval_id,
                approved_by="test-reviewer",
                reason="approved for the test turn",
            )
            self.assertEqual(approved["status"], "approved")

            resumed = agent_os.resume(run_id)
            self.assertEqual(resumed.status, "completed")
            self.assertEqual(provider.calls, 2)
            self.assertEqual(calls, [{"value": "approved"}])
            self.assertEqual(
                approvals.get(approval_id).status,
                "approved",
            )
            event_types = [event["event_type"] for event in read_jsonl(run_root / "execution_events.jsonl")]
            self.assertIn("tool_call_completed", event_types)
            self.assertEqual(read_json(run_root / "turn_state.json")["status"], "completed")

    def test_core_tool_rejection_gates_without_executing_the_tool(self) -> None:
        class ApprovalProvider:
            available = True

            def __init__(self) -> None:
                self.calls = 0

            def complete_with_tools(self, **_kwargs):
                self.calls += 1
                return {
                    "kind": "tool_calls",
                    "tool_calls": [
                        {
                                "tool_name": "core.test.rejection",
                            "arguments": {},
                            "call_id": "rejection-call-1",
                        }
                    ],
                }

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            agent_os.main_agent.llm_provider = ApprovalProvider()
            calls: list[bool] = []
            agent_os.main_agent.tools.register(
                CoreToolDefinition(
                    name="core.test.rejection",
                    description="A test tool requiring human approval.",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    requires_approval=True,
                ),
                lambda _arguments, _context: (calls.append(True) or {"ok": True}),
            )
            first = agent_os.handle_request(
                RequestEnvelope(
                    intent="core.conversation.answer",
                    user_query="reject the tool",
                )
            )
            run_id = str(first.run_id)
            approval_id = first.pending_approval_ids[0]
            rejected = AgentOSAdminService(agent_os).reject(
                run_id,
                approval_id=approval_id,
                rejected_by="test-reviewer",
                reason="not permitted",
            )
            self.assertEqual(rejected["status"], "rejected")
            resumed = agent_os.resume(run_id)
            self.assertEqual(resumed.status, "gated")
            self.assertEqual(calls, [])
            self.assertIn("审批", resumed.message)

    def test_core_agent_clarification_is_not_reported_as_completed(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            agent_os.main_agent.llm_provider = _ClarifyingProvider()

            response = agent_os.handle_request(
                RequestEnvelope(
                    intent="core.conversation.answer",
                    user_query="Please inspect a file.",
                )
            )

            self.assertEqual(response.status, "needs_clarification")
            self.assertEqual(response.clarification.question, "Which file should I inspect?")
            self.assertEqual(response.clarification.missing_fields, ["path"])
            run_root = self._settings(root).runs_root / str(response.run_id)
            state = read_json(run_root / "main_agent_state.json")
            self.assertEqual(state["status"], "paused")
            self.assertEqual(state["missing_fields"], ["path"])
            self.assertTrue(
                any(
                    event["event_type"] == "main_agent_clarification_requested"
                    for event in read_jsonl(run_root / "execution_events.jsonl")
                )
            )

    def test_request_intake_clarification_resumes_the_same_run_after_a_thread_answer(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                    "openai_api_key": None,
                    "openai_base_url": None,
                }
            )
            agent_os = AgentOS(settings)
            provider_tool = agent_os.tool_registry._tools["financial_data_provider"]
            agent_os.tool_registry._tools["financial_data_provider"] = replace(
                provider_tool,
                handler=lambda _arguments, _context: {
                    "symbols": ["600000"],
                    "snapshots": [
                        {
                            "snapshot_type": "market",
                            "symbol": "600000",
                            "facts": {"availability": "available", "latest_price": 10.0},
                        },
                        {
                            "snapshot_type": "fundamental",
                            "symbol": "600000",
                            "facts": {
                                "availability": "available",
                                "financial_abstract": {"net_profit": 1.0},
                            },
                        },
                    ],
                    "source_refs": [{"type": "test_fixture", "id": "clarification-resume"}],
                },
            )
            first = agent_os.handle_request(
                RequestEnvelope(
                    thread_id="clarification-thread",
                    intent="stock_check",
                    user_query="Analyze a stock.",
                    constraints={
                        "research_profile": "lite",
                        "turn_config": {"timeout_seconds": 300},
                    },
                )
            )

            self.assertEqual(first.status, "needs_clarification")
            self.assertIsNotNone(first.run_id)
            assert first.run_id is not None
            self.assertEqual(first.clarification.missing_fields, ["symbols"])
            run_root = settings.runs_root / first.run_id
            self.assertTrue((run_root / "clarification_pending.json").exists())

            second = agent_os.handle_request(
                RequestEnvelope(
                    thread_id="clarification-thread",
                    user_query="600000",
                ),
                run_id=first.run_id,
            )

            self.assertEqual(second.run_id, first.run_id)
            self.assertEqual(second.status, "gated")
            self.assertFalse((run_root / "clarification_pending.json").exists())
            event_types = [event["event_type"] for event in read_jsonl(run_root / "execution_events.jsonl")]
            self.assertIn("clarification_requested", event_types)
            self.assertIn("clarification_resolved", event_types)

    def test_architecture_question_stays_in_core_conversation(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            response = AgentOS(self._settings(Path(temporary_directory))).handle_request(
                RequestEnvelope(user_query="Explain your architecture.")
            )

            self.assertEqual(response.status, "completed")
            self.assertEqual(response.mode, "core")
            self.assertEqual(response.intent, "core.conversation.answer")
            self.assertTrue(response.message)

    def test_tool_registry_enforces_rate_limit_and_idempotency(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            registry = build_core_tool_registry()
            calls = []
            registry.register(
                CoreToolDefinition(
                    name="test.rate_limited",
                    description="A rate-limited read tool.",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    rate_limit_per_minute=1,
                ),
                lambda _arguments, _context: (calls.append("called") or {"ok": True}),
            )
            context = CoreToolContext(
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                run_root=root,
                actor="main_agent",
                allowed_tools=frozenset({"test.rate_limited"}),
            )
            first = registry.invoke(tool_name="test.rate_limited", arguments={}, context=context)
            second = registry.invoke(tool_name="test.rate_limited", arguments={}, context=context)
            self.assertEqual(first.status, "completed")
            self.assertEqual(second.status, "rejected")
            self.assertEqual(calls, ["called"])

            registry.register(
                CoreToolDefinition(
                    name="test.idempotent",
                    description="An idempotent write tool.",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    idempotency_required=True,
                ),
                lambda _arguments, _context: (calls.append("write") or {"written": True}),
            )
            write_context = context.__class__(
                **{
                    **context.__dict__,
                    "allowed_tools": frozenset({"test.idempotent"}),
                    "idempotency_key": "write-1",
                }
            )
            written = registry.invoke(tool_name="test.idempotent", arguments={}, context=write_context)
            replayed = registry.invoke(tool_name="test.idempotent", arguments={}, context=write_context)
            self.assertEqual(written.status, "completed")
            self.assertEqual(replayed.status, "completed")
            self.assertTrue(replayed.metadata["idempotency_hit"])
            self.assertEqual(calls.count("write"), 1)

    def test_configured_mcp_tool_is_registered_with_core_governance_contract(self) -> None:
        source = McpSourceRegistry(
            servers=[
                McpServerSource(
                    id="market_server",
                    name="Market MCP",
                    transport="stdio",
                    command="python",
                    tools=(
                        McpToolDefinition(
                            name="lookup_quote",
                            summary="Read a quote from MCP.",
                            input_json_schema={
                                "type": "object",
                                "required": ["symbol"],
                            },
                        ),
                    ),
                )
            ]
        )
        registry = register_mcp_tools(
            build_core_tool_registry(),
            source_registry=source,
        )

        definition = registry.definition("mcp.market_server.lookup_quote")
        self.assertEqual(definition.permissions, ["mcp:server:market_server"])
        self.assertEqual(definition.side_effect_level, "read_only")
        self.assertTrue(definition.source_refs_required)
        self.assertEqual(definition.allowed_callers, ["subagent"])

    def test_mcp_stdio_adapter_invokes_a_real_fake_server_and_parses_structured_content(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            server = root / "fake_mcp_server.py"
            server.write_text(
                """
import json
import sys

def encode(payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body

raw = sys.stdin.buffer.read()
offset = 0
messages = []
while offset < len(raw):
    header_end = raw.find(b"\\r\\n\\r\\n", offset)
    if header_end < 0:
        break
    headers = raw[offset:header_end].decode("ascii")
    length = next(int(row.split(":", 1)[1].strip()) for row in headers.split("\\r\\n") if row.lower().startswith("content-length:"))
    start = header_end + 4
    messages.append(json.loads(raw[start:start + length].decode("utf-8")))
    offset = start + length
for message in messages:
    if message.get("id") == 1:
        sys.stdout.buffer.write(encode({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}))
    if message.get("id") == 2:
        sys.stdout.buffer.write(encode({"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": {"price": 10.5}}}))
sys.stdout.buffer.flush()
""".strip(),
                encoding="utf-8",
            )
            result = invoke_mcp_tool(
                server_source=McpServerSource(
                    id="fake_market",
                    name="Fake Market",
                    transport="stdio",
                    command=sys.executable,
                    args=(str(server),),
                ),
                tool_name="lookup_quote",
                arguments={"symbol": "600000"},
                timeout_seconds=5.0,
            )

            self.assertEqual(result["price"], 10.5)

    def test_graph_leaf_uses_the_same_core_tool_registry(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pack_root = root / "packs" / "tool_test"
            for directory in DomainPackRegistry.REQUIRED_DIRECTORIES:
                (pack_root / directory).mkdir(parents=True, exist_ok=True)
            (pack_root / "domain.yaml").write_text(
                """
domain_id: tool_test
version: 1.0.0
description: Shared tool registry test pack.
artifact_types:
  - artifact_type: tool_output
capabilities:
  - subagent_type: tool_reader
    capability: tool_test.read
    implementation: subagents.runtime:read_with_tool
    produces:
      - artifact_type: tool_output
report_contracts:
  - contract_id: tool.output.v1
    artifact_type: tool_output
    required_sections: [value]
    required_source_refs: true
governance:
  allowed_tools: [core.file.read]
  allowed_permissions: [file:read]
""".strip(),
                encoding="utf-8",
            )
            (pack_root / "artifact_schemas" / "tool_output.json").write_text(
                '{"type":"object","required":["value"]}',
                encoding="utf-8",
            )
            (pack_root / "subagents" / "runtime.py").write_text(
                """
from research_system.schemas.artifacts import ArtifactDraft

def read_with_tool(context):
    result = context.call_tool("core.file.read", {"path": "note.txt"})
    if result.status != "completed":
        raise RuntimeError(result.error or "tool failed")
    return ArtifactDraft(
        artifact_type="tool_output",
        content={"value": result.output["content"]},
        source_refs=list(result.source_refs),
        confidence=1.0,
    )
""".strip(),
                encoding="utf-8",
            )
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "note.txt").write_text("from shared registry", encoding="utf-8")
            settings = self._settings(root).model_copy(update={"workspace_root": workspace})
            registry = DomainPackRegistry.load(root / "packs")
            graph = ExecutionGraph(
                domain_id="tool_test",
                objective="Read through the governed Core Tool.",
                governance=ExecutionGovernance(
                    allowed_tools=["core.file.read"],
                    allowed_permissions=["file:read"],
                ),
                nodes=[
                    ExecutionNode(
                        node_id="tool_reader",
                        node_kind="subagent",
                        executor="tool_reader",
                        capability="tool_test.read",
                        tools=["core.file.read"],
                        permissions=["file:read"],
                        produces=[ArtifactPort(artifact_type="tool_output")],
                    )
                ],
            )
            events: list[dict] = []
            runner = ExecutionGraphRunner(
                registry=registry,
                run_root=root / "run",
                event_callback=events.append,
                tool_registry=build_core_tool_registry(),
                settings=settings,
            )

            result = runner.run(
                graph=graph,
                run_id="tool-run",
                thread_id="tool-thread",
                request_id="tool-request",
            )

            self.assertEqual(result.snapshot.status, "completed")
            output = runner.board.get(result.final_artifact_ids[0])
            self.assertIsNotNone(output)
            assert output is not None
            self.assertEqual(output.content["value"], "from shared registry")
            completed = [event for event in events if event["event_type"] == "tool_call_completed"]
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]["actor"], "subagent:tool_reader")
            self.assertEqual(completed[0]["node_id"], "tool_reader")

    def test_core_run_pause_and_resume_restores_the_same_main_agent(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = self._settings(root)
            agent_os = AgentOS(settings)
            request = RequestEnvelope(user_query="Who are you?")
            decision = agent_os.request_intake_decision(request)
            run_id, paths, _ = agent_os.run_store.create_run(
                request,
                "core.conversation.answer",
            )
            thread_id = f"thread_{run_id}"
            resolved = request.model_copy(
                update={"slots": {"run_id": run_id, "thread_id": thread_id}}
            )
            write_json(
                paths.root / "agent_os_request.json",
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "request": resolved.model_dump(mode="json"),
                    "mode": "core",
                    "request_intake": decision.model_dump(mode="json"),
                },
            )
            write_json(
                paths.root / "execution_control.json",
                {"pause_requested": True, "cancel_requested": False},
            )
            paused = agent_os.main_agent.run(
                request=resolved,
                decision=decision,
                run_id=run_id,
                thread_id=thread_id,
            )
            self.assertEqual(paused.status, "paused")
            resumed = agent_os.resume(run_id)
            self.assertEqual(resumed.status, "completed")
            state = read_json(paths.root / "main_agent_state.json")
            self.assertEqual(state["run_id"], run_id)
            self.assertEqual(state["thread_id"], thread_id)
            self.assertEqual(state["status"], "completed")

    def test_request_idempotency_reuses_the_same_persisted_response(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            request = RequestEnvelope(
                user_query="Who are you?",
                idempotency_key="same-request-1",
                tenant_id="tenant-1",
                user_id="user-1",
            )

            first = agent_os.handle_request(request)
            second = agent_os.handle_request(
                request.model_copy(update={"request_id": "different-request-id"})
            )

            self.assertEqual(first.run_id, second.run_id)
            self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
            runs = [path for path in (self._settings(root).runs_root).iterdir() if path.is_dir()]
            self.assertEqual(len(runs), 1)

    def test_request_idempotency_replays_an_inflight_turn_without_new_run(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            request = RequestEnvelope(
                user_query="in flight",
                idempotency_key="in-flight-1",
                tenant_id="tenant-1",
                user_id="user-1",
            )
            run_id, paths, _ = agent_os.run_store.create_run(request, "core.conversation.answer")
            write_json(
                paths.root / "turn_state.json",
                {
                    "run_id": run_id,
                    "thread_id": "thread-1",
                    "request_id": request.request_id,
                    "status": "running",
                },
            )

            replay = agent_os.handle_request(
                request.model_copy(update={"request_id": "different-request-id"})
            )

            self.assertEqual(replay.run_id, run_id)
            self.assertEqual(replay.status, "running")
            self.assertIn("已在执行中", replay.message)
            runs = [path for path in self._settings(root).runs_root.iterdir() if path.is_dir()]
            self.assertEqual(len(runs), 1)

    def test_public_resume_recovers_a_running_core_checkpoint(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            agent_os = AgentOS(self._settings(root))
            request = RequestEnvelope(user_query="recover running core")
            decision = agent_os.request_intake_decision(request)
            run_id, paths, _ = agent_os.run_store.create_run(request, "core.conversation.answer")
            thread_id = f"thread_{run_id}"
            resolved = request.model_copy(update={"thread_id": thread_id})
            write_json(
                paths.root / "agent_os_request.json",
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "request": resolved.model_dump(mode="json"),
                    "mode": "core",
                    "request_intake": decision.model_dump(mode="json"),
                },
            )
            write_json(
                paths.root / "main_agent_state.json",
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "request_id": request.request_id,
                    "status": "running",
                    "mode": "core",
                    "request_kind": "conversation",
                },
            )
            write_json(
                paths.root / "turn_state.json",
                {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "request_id": request.request_id,
                    "status": "running",
                },
            )

            resumed = agent_os.resume(run_id)

            self.assertEqual(resumed.status, "completed")
            events = read_jsonl(paths.root / "execution_events.jsonl")
            self.assertTrue(
                any(event["event_type"] == "turn_recovered_after_worker_restart" for event in events)
            )


if __name__ == "__main__":
    unittest.main()
