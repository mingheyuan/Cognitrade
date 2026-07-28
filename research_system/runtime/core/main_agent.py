from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.core.tool_registry import (
    CoreToolContext,
    CoreToolRegistry,
    build_core_tool_registry,
)
from research_system.runtime.event_store import append_canonical_event
from research_system.runtime.follow_up import FollowUpRuntime
from research_system.runtime.hooks import StopHookInput, StopHookOutcome
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.core_runtime import (
    AgentTurnResponse,
    MainAgentRunResult,
    MainAgentState,
    PlanDecision,
    RequestIntakeDecision,
)
from research_system.schemas.events import EventEnvelope
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.turn_runtime import TurnConfig


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MainAgent:
    """The single user-facing Agent OS agent for Core Agent Loop execution."""

    def __init__(
        self,
        *,
        settings,
        run_store: RunStore,
        memory_store,
        llm_provider=None,
        tool_registry: CoreToolRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.run_store = run_store
        self.memory_store = memory_store
        self.llm_provider = llm_provider
        self.tools = tool_registry or build_core_tool_registry()
        self._event_lock = RLock()
        self._event_turn_ids: dict[str, str] = {}
        self._turn_runner = None

    def bind_turn_runner(self, turn_runner) -> None:
        """Bind the canonical lifecycle without making TurnRunner a dependency."""
        self._turn_runner = turn_runner

    def run(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        run_id: str,
        thread_id: str,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        resume: bool = False,
        emit_started: bool = True,
        turn_id: str | None = None,
        hook_runtime=None,
        follow_up_runtime: FollowUpRuntime | None = None,
        turn_config: TurnConfig | None = None,
    ) -> MainAgentRunResult:
        """Compatibility facade; all execution is owned by TurnRunner."""
        del emit_started, turn_id, hook_runtime, follow_up_runtime
        if self._turn_runner is None:
            raise RuntimeError(
                "MainAgent.run requires a bound TurnRunner; use AgentOS as the execution entrypoint"
            )
        outcome = self._turn_runner.execute_task(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id=thread_id,
            execution_mode=(
                turn_config.execution_mode
                if turn_config is not None
                else decision.recommended_mode
            ),
            turn_config=turn_config,
            event_callback=event_callback,
            resume=resume,
        )
        return outcome.result

    def route(
        self,
        *,
        request: RequestEnvelope,
        run_id: str,
        thread_id: str,
        intake_resolver: Callable[..., RequestIntakeDecision],
        runtime_context: dict[str, Any] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> RequestIntakeDecision:
        """Let the Main Agent own the first route decision for every request.

        RequestIntake is invoked through a governed Core Tool so the Pack
        catalog is an input to the Main Agent, rather than a pre-router in
        AgentOS.
        """
        paths = self.run_store.load_run_paths(run_id)
        board = ArtifactBoard(paths.artifact_board)
        board.bind_thread(thread_id)
        control_path = paths.root / "execution_control.json"
        if not control_path.exists():
            write_json(
                control_path,
                {
                    "pause_requested": False,
                    "cancel_requested": False,
                    "updated_at": utc_now().isoformat(),
                },
            )
        existing = read_json(paths.root / "main_agent_state.json")
        if isinstance(existing, dict):
            state = MainAgentState.model_validate(existing).model_copy(
                update={
                    "status": "running",
                    "mode": "core",
                    "intent": request.intent,
                    "request_kind": "conversation",
                    "clarification_question": None,
                    "missing_fields": [],
                    "error": None,
                    "updated_at": utc_now(),
                }
            )
        else:
            state = MainAgentState(
                run_id=run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                status="running",
                mode="core",
                intent=request.intent,
                request_kind="conversation",
                tool_loop_limit=self._tool_loop_limit(),
            )
        self._persist_state(paths.root, state)
        self._emit(
            paths.root,
            event_callback,
            "main_agent_started",
            run_id=run_id,
            thread_id=thread_id,
            request_id=request.request_id,
            intent=request.intent,
            mode="core",
            phase="route",
            message="Main Agent started evaluating the request.",
        )
        self._emit(
            paths.root,
            event_callback,
            "main_agent_route_started",
            run_id=run_id,
            thread_id=thread_id,
            request_id=request.request_id,
            actor="main_agent",
            message="Main Agent is deciding whether to answer, use a Core Tool, or enter Graph Mode.",
        )
        context = CoreToolContext(
            run_id=run_id,
            thread_id=thread_id,
            request_id=request.request_id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            run_root=paths.root,
            actor="main_agent",
            artifact_board=board,
            memory_store=self.memory_store,
            settings=self.settings,
            request_intake_supplier=intake_resolver,
            event_callback=event_callback,
            allowed_tools=frozenset({"core.request.intake"}),
        )
        result = self.tools.invoke(
            tool_name="core.request.intake",
            arguments={
                "request": request.model_dump(mode="json"),
                "runtime_context": runtime_context or {},
            },
            context=context,
        )
        if result.status != "completed":
            decision = RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
                intent="core.conversation.answer",
                candidate_capabilities=["core.conversation.answer"],
                confidence=0.0,
                trace={"source": "main_agent_route_fallback", "error": result.error},
            )
        else:
            payload = result.output.get("decision")
            if not isinstance(payload, dict):
                raise RuntimeError("core.request.intake returned no decision")
            decision = RequestIntakeDecision.model_validate(payload)
        decision = self._materialize_intake_clarification(
            request=request,
            decision=decision,
        )
        write_json(
            paths.root / "agent_os_request.json",
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "request": request.model_dump(mode="json"),
                "mode": decision.recommended_mode,
                "request_intake": decision.model_dump(mode="json"),
                "route_owner": "main_agent",
            },
        )
        self._emit(
            paths.root,
            event_callback,
            "main_agent_route_decided",
            run_id=run_id,
            thread_id=thread_id,
            request_id=request.request_id,
            actor="main_agent",
            mode=decision.recommended_mode,
            request_kind=decision.request_kind,
            intent=decision.intent,
            domain_id=decision.domain_id,
            confidence=decision.confidence,
            message=(
                "Main Agent selected Graph Mode."
                if decision.request_kind == "domain_task"
                else "Main Agent selected the Core Agent Loop."
            ),
        )
        return decision

    def observe_graph_progress(self, facts: dict[str, Any]) -> dict[str, str] | None:
        """Ask the Main Agent for a short Chinese status update from runtime facts."""
        provider = self.llm_provider
        if provider is None or not bool(getattr(provider, "available", False)):
            return None
        complete = getattr(provider, "complete", None)
        if not callable(complete):
            return None
        prompt = json.dumps(facts, ensure_ascii=False, default=str)[:24_000]
        try:
            raw = complete(
                system_prompt=(
                    "你是 Cognitrade Agent OS 的 Main Agent。"
                    "你收到的是已经发生的 Tool 或 Subagent 运行事实。"
                    "请只根据这些事实，用中文生成一条面向用户的公开进度摘要。"
                    "不要输出隐藏思维链、猜测或未发生的结果。"
                    "只返回 JSON：{\"current\":\"当前事实\",\"next\":\"下一步实际动作\"}。"
                ),
                user_prompt=f"运行事实：\n{prompt}",
                temperature=0.2,
            )
        except Exception:
            return None
        payload = self._parse_json_object(raw)
        if not isinstance(payload, dict):
            return None
        current = str(payload.get("current") or "").strip()
        next_action = str(payload.get("next") or "").strip()
        if not current or not next_action:
            return None
        return {"current": current[:500], "next": next_action[:500]}

    def compose_research_synthesis(
        self,
        *,
        request: str,
        report: dict[str, Any],
        artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Create a user-facing Chinese synthesis without changing source facts."""
        provider = self.llm_provider
        if provider is None or not bool(getattr(provider, "available", False)):
            return None
        complete = getattr(provider, "complete", None)
        if not callable(complete):
            return None
        facts = json.dumps(
            {
                "report": report,
                "active_board_artifacts": list(artifacts or []),
            },
            ensure_ascii=False,
            default=str,
        )[:48_000]
        try:
            raw = complete(
                system_prompt=(
                    "你是 Cognitrade Agent OS 的 Main Agent，负责交付研究结果。"
                    "请根据提供的结构化证据、方法论输出、风险和数据缺口，独立组织一段中文结论。"
                    "不要照抄固定模板，不要补造缺失数据，不要给出买卖指令。"
                    "必须保留不确定性、降级项和不构成投资建议的边界。"
                    "只返回 JSON："
                    "{\"conclusion\":\"中文最终结论\","
                    "\"assistant_message\":\"给用户的中文回答\","
                    "\"follow_up\":[\"下一步研究动作\"]}。"
                ),
                user_prompt=f"用户请求：{request}\n结构化研究事实：\n{facts}",
                temperature=0.2,
            )
        except Exception:
            return None
        payload = self._parse_json_object(raw)
        if not isinstance(payload, dict):
            return None
        conclusion = str(payload.get("conclusion") or "").strip()
        assistant_message = str(payload.get("assistant_message") or conclusion).strip()
        follow_up = [
            str(item).strip()[:500]
            for item in list(payload.get("follow_up") or [])
            if str(item).strip()
        ][:5]
        if not conclusion or not assistant_message:
            return None
        return {
            "conclusion": conclusion[:2_000],
            "assistant_message": assistant_message[:4_000],
            "follow_up": follow_up,
        }

    def _materialize_intake_clarification(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
    ) -> RequestIntakeDecision:
        """Turn structured Intake gaps into one Main Agent user-facing question."""

        if decision.status != "needs_clarification" or decision.clarification_question:
            return decision
        question = self._generate_intake_clarification(request=request, decision=decision)
        return decision.model_copy(update={"clarification_question": question})

    def _generate_intake_clarification(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
    ) -> str:
        provider = self.llm_provider
        if provider is not None and bool(getattr(provider, "available", False)):
            try:
                response = provider.complete(
                    system_prompt=(
                        "You are the single Main Agent of Cognitrade Agent OS. "
                        "Turn the structured request-intake gaps into one concise "
                        "user-facing clarification question. Answer in the user's "
                        "language. Do not guess missing entities, do not perform the "
                        "professional task, and do not mention internal field names "
                        "or implementation details. If the request is ambiguous, "
                        "briefly present the relevant choices. Return only the question."
                    ),
                    user_prompt=json.dumps(
                        {
                            "user_query": request.user_query,
                            "conversation_context": request.conversation_context[-8:],
                            "intent": decision.intent,
                            "domain_id": decision.domain_id,
                            "slots": decision.slots,
                            "constraints": decision.constraints,
                            "missing_fields": decision.missing_fields,
                            "clarification_context": decision.clarification_context,
                        },
                        ensure_ascii=False,
                    ),
                    temperature=0.2,
                ).strip()
                if response:
                    return response
            except Exception:
                pass
        return self._fallback_intake_clarification(request=request, decision=decision)

    @staticmethod
    def _fallback_intake_clarification(
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
    ) -> str:
        """Generic no-LLM fallback; domain wording remains outside the Pack."""

        labels = {
            "symbols": "标的名称或代码",
            "sector": "板块或行业",
            "review_target_run_id": "需要复核的历史运行",
            "intent_or_target": "任务目标或研究对象",
        }
        missing = [labels.get(field, field) for field in decision.missing_fields]
        requested = "、".join(dict.fromkeys(missing)) or "必要信息"
        scope = str(decision.slots.get("sector") or "").strip()
        if scope and "symbols" in decision.missing_fields:
            requested = f"具体的{scope}标的名称或代码"
        if any("\u4e00" <= char <= "\u9fff" for char in request.user_query):
            return f"为了继续处理，请补充{requested}。"
        return f"To continue, please provide: {requested}."

    def _control_result(
        self,
        *,
        root: Path,
        state: MainAgentState,
        status: str,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        tool_results: list,
        event_callback: Callable[[dict[str, Any]], None] | None,
    ) -> MainAgentRunResult:
        state = state.model_copy(
            update={
                "status": status,
                "tool_call_ids": [item.call_id for item in tool_results],
                "updated_at": utc_now(),
            }
        )
        self._persist_state(root, state)
        self._emit(
            root,
            event_callback,
            f"main_agent_{status}",
            run_id=state.run_id,
            thread_id=state.thread_id,
            request_id=state.request_id,
            mode=state.mode,
        )
        return MainAgentRunResult(
            run_id=state.run_id,
            thread_id=state.thread_id,
            request_id=state.request_id,
            status=status,
            mode=state.mode,
            intent=decision.intent,
            message=(
                "Agent OS Core Agent Loop paused."
                if status == "paused"
                else "Agent OS Core Agent Loop cancelled."
            ),
            artifact_ids=list(state.artifact_ids),
            tool_calls=tool_results,
            state=state,
        )

    def _tool_loop_limit(self) -> int:
        return max(1, min(int(getattr(self.settings, "max_agent_tool_loop_turns", 4)), 32))

    def _apply_stop_hook_candidate(
        self,
        *,
        message: str,
        state: MainAgentState,
        root: Path,
        hook_runtime,
        follow_up_runtime: FollowUpRuntime | None,
        turn_config: TurnConfig | None,
        event_callback: Callable[[dict[str, Any]], None] | None,
        stop_target: str = "stop",
        parent_turn_id: str | None = None,
        agent_id: str | None = None,
        agent_type: str | None = None,
        agent_transcript_ref: str | None = None,
    ) -> tuple[MainAgentState, bool]:
        """Run the execution-time stop decision for a Root or Child Turn."""
        if (
            hook_runtime is None
            or turn_config is None
            or turn_config.hook_config is None
            or not turn_config.hook_config.enabled
        ):
            return state, False
        hook_input = StopHookInput(
            session_id=state.thread_id,
            run_id=state.run_id,
            thread_id=state.thread_id,
            turn_id=self._event_turn_ids.get(state.run_id, state.run_id),
            task_type=turn_config.task_type,
            target=stop_target,
            cwd=str(root),
            transcript_ref=str(root / "task_state.json"),
            parent_turn_id=parent_turn_id,
            agent_id=agent_id,
            agent_type=agent_type,
            agent_transcript_ref=agent_transcript_ref,
            stop_hook_active=state.stop_hook_active,
            last_assistant_message=message,
            hook_iteration=state.hook_iteration,
        )
        decision: StopHookOutcome = hook_runtime.run_stop(
            hook_input,
            policy=turn_config.hook_config.policy,
        )
        if not decision.should_block:
            return state.model_copy(update={"stop_hook_active": False}), False

        iteration = state.hook_iteration + 1
        continuation_text = "; ".join(
            item
            for item in [
                decision.block_reason or "The stop hook requested more work.",
                *[fragment.text for fragment in decision.continuation_fragments],
            ]
            if str(item).strip()
        ).strip()
        continuation = {
            "source": "stop_hook",
            "operation_id": f"stop-hook:{state.run_id}:{iteration}",
            "reason": continuation_text,
            "hook_iteration": iteration,
        }
        max_iterations = turn_config.hook_config.policy.max_stop_continuations
        if iteration > max_iterations:
            state = state.model_copy(
                update={
                    "status": "gated",
                    "stop_hook_active": True,
                    "hook_iteration": iteration,
                    "continuation": continuation,
                    "assistant_message": message,
                    "error": "StopHook continuation limit exceeded.",
                    "updated_at": utc_now(),
                }
            )
            self._persist_state(root, state)
            self._emit(
                root,
                event_callback,
                "stop_hook_limit_exceeded",
                run_id=state.run_id,
                thread_id=state.thread_id,
                request_id=state.request_id,
                turn_id=self._event_turn_ids.get(state.run_id),
                mode=state.mode,
                hook_iteration=iteration,
                max_stop_continuations=max_iterations,
            )
            return state, False

        if follow_up_runtime is not None:
            follow_up_runtime.set_stop_hook_state(
                active=True,
                iteration=iteration,
                continuation=continuation,
            )
            follow_up_runtime.enqueue(
                source="hook",
                kind="hook_prompt",
                text=continuation_text,
                operation_id=str(continuation["operation_id"]),
            )
        state = state.model_copy(
            update={
                "status": "running",
                "stop_hook_active": True,
                "hook_iteration": iteration,
                "follow_up_count": state.follow_up_count + 1,
                "continuation": continuation,
                "transcript": self._trim_transcript(
                    [
                        *list(state.transcript),
                        {
                            "role": "assistant",
                            "content": message,
                        },
                    ]
                ),
                "updated_at": utc_now(),
            }
        )
        self._persist_state(root, state)
        self._emit(
            root,
            event_callback,
            "stop_hook_blocked",
            run_id=state.run_id,
            thread_id=state.thread_id,
            request_id=state.request_id,
            turn_id=self._event_turn_ids.get(state.run_id),
            mode=state.mode,
            hook_iteration=iteration,
            reason=continuation_text,
        )
        return state, True

    @staticmethod
    def _provider_tool_turn(
        *,
        provider,
        request,
        decision,
        transcript,
        native_turn,
        definitions,
    ) -> AgentTurnResponse:
        del decision
        definitions = [
            {
                "name": item.name,
                "description": item.description,
                "input_schema": item.input_schema or {"type": "object"},
            }
            for item in definitions
        ]
        try:
            raw = native_turn(
                system_prompt=(
                    "You are the single Main Agent of Cognitrade Agent OS. "
                    "Use only the supplied tools. Return a final user-facing answer "
                    "when no more tool calls are needed. Do not invent professional facts. "
                    "When useful, include one public progress block in the exact form "
                    '<progress>{"current":"...","next":"..."}</progress>. '
                    "Keep it short, describe only the public operational status, and never "
                    "include hidden reasoning or private chain-of-thought."
                ),
                user_prompt=request.user_query,
                messages=list(transcript),
                tools=definitions,
                temperature=0.2,
            )
        except TypeError:
            raw = native_turn(
                system_prompt=(
                    "You are the single Main Agent of Cognitrade Agent OS. "
                    "Use only the supplied tools and answer the user directly. "
                    "When useful, include one public progress block in the exact form "
                    '<progress>{"current":"...","next":"..."}</progress>. '
                    "Keep it short and operational; never include hidden reasoning."
                ),
                user_prompt=request.user_query,
                tools=definitions,
                temperature=0.2,
            )
        if isinstance(raw, AgentTurnResponse):
            return raw
        if isinstance(raw, str):
            return AgentTurnResponse(message=raw)
        return AgentTurnResponse.model_validate(raw)

    @staticmethod
    def _response_progress(
        response: AgentTurnResponse,
        *,
        fallback_current: str,
        fallback_next: str,
    ) -> tuple[str, str]:
        progress = response.public_progress
        current = str(progress.current if progress else "").strip() or fallback_current
        next_action = str(progress.next if progress else "").strip() or fallback_next
        return current[:500], next_action[:500]

    @staticmethod
    def _parse_json_object(raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if not text:
            return None
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    @staticmethod
    def _trim_tool_output(output: dict[str, Any]) -> dict[str, Any]:
        encoded = repr(output)
        if len(encoded) <= 20_000:
            return dict(output)
        return {"summary": encoded[:20_000], "truncated": True}

    @staticmethod
    def _tool_output_summary(result) -> str:
        if result.status != "completed":
            return str(result.error or result.status)
        output = result.output if isinstance(result.output, dict) else {}
        for key in ("message", "summary", "content", "result"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:2_000]
        try:
            encoded = json.dumps(output, ensure_ascii=False, default=str)
        except TypeError:
            encoded = repr(output)
        return encoded[:2_000]

    @staticmethod
    def _trim_transcript(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = transcript[-100:]
        return [dict(row) for row in rows]

    def _emit_loop_progress(
        self,
        root: Path,
        callback,
        *,
        state: MainAgentState,
        turn: int,
        phase: str,
        current_action: str,
        next_action: str,
        status: str = "running",
    ) -> None:
        """Persist factual loop state without exposing hidden reasoning."""
        current = str(current_action).strip()
        next_step = str(next_action).strip()
        self._emit(
            root,
            callback,
            "agent_progress",
            run_id=state.run_id,
            thread_id=state.thread_id,
            request_id=state.request_id,
            actor="main_agent",
            progress_kind="main_agent_loop",
            phase=phase,
            turn=turn,
            status=status,
            current_action=current,
            next_action=next_step,
            message=f"当前：{current}；下一步：{next_step}",
        )

    @staticmethod
    def _has_deterministic_core_route(query: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(query or "")).lower()
        return any(
            marker in normalized or marker in query
            for marker in (
                "who are you",
                "你是谁",
                "what can you do",
                "你能做什么",
                "怎么用",
                "read file",
                "读取文件",
                "search files",
                "搜索文件",
                "invoke skill",
                "调用 skill",
                "调用技能",
                "run status",
                "运行状态",
                "任务状态",
            )
        )

    def _answer(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        context: CoreToolContext,
        tool_results: list,
    ) -> str:
        query = request.user_query.strip()
        normalized = re.sub(r"\s+", " ", query).lower()
        if any(marker in normalized or marker in query for marker in ("who are you", "你是谁")):
            return (
                "我是 Cognitrade Agent OS 的 Main Agent。"
                "我可以直接处理基础对话和 Core Tool；当任务需要专业 DomainPack、多个 Subagent、"
                "并发或质量 Gate 时，会按需进入动态 ExecutionGraph。"
            )
        if any(
            marker in normalized or marker in query
            for marker in ("what can you do", "你能做什么", "怎么用", "how do i use")
        ):
            return (
                "我可以进行普通对话、读取和检查受权限控制的运行数据、调用 Core Tool，"
                "并在需要时调度 DomainPack Subagent 组成动态研究图。"
            )
        if "run status" in normalized or "运行状态" in query or "任务状态" in query:
            result = self.tools.invoke(
                tool_name="core.run.status",
                arguments={},
                context=context,
            )
            tool_results.append(result)
            if result.status == "completed":
                return f"当前 Agent OS 运行状态：{result.output.get('status', 'unknown')}。"
        file_match = re.search(r"(?:read file|读取文件)\s+(.+)$", query, flags=re.IGNORECASE)
        if file_match:
            result = self.tools.invoke(
                tool_name="core.file.read",
                arguments={"path": file_match.group(1).strip()},
                context=context,
            )
            tool_results.append(result)
            if result.status == "completed":
                return str(result.output.get("content") or "")
            return f"无法读取文件：{result.error or 'Core Tool failed'}"
        search_match = re.search(r"(?:search files|搜索文件)\s+(.+)$", query, flags=re.IGNORECASE)
        if search_match:
            result = self.tools.invoke(
                tool_name="core.file.search",
                arguments={"query": search_match.group(1).strip()},
                context=context,
            )
            tool_results.append(result)
            if result.status == "completed":
                matches = result.output.get("matches") or []
                return f"找到 {len(matches)} 个匹配项：{matches}"
            return f"无法搜索文件：{result.error or 'Core Tool failed'}"
        skill_match = re.search(
            r"(?:invoke skill|调用 skill|调用技能)\s+([A-Za-z0-9_.-]+)",
            query,
            flags=re.IGNORECASE,
        )
        if skill_match:
            result = self.tools.invoke(
                tool_name="core.skill.invoke",
                arguments={"skill_name": skill_match.group(1)},
                context=context,
            )
            tool_results.append(result)
            if result.status == "completed":
                return f"Skill {skill_match.group(1)} 已完成：{result.output.get('result')}"
            return f"无法调用 Skill：{result.error or 'Core Tool failed'}"
        if self._is_context_recall(query):
            context_answer = self._context_recall_answer(request)
            if context_answer:
                return context_answer
        if self.llm_provider is not None and bool(getattr(self.llm_provider, "available", False)):
            try:
                return self.llm_provider.complete(
                    system_prompt=(
                        "You are the single Main Agent of Cognitrade Agent OS. "
                        "Answer the user directly in the user's language. "
                        "Do not claim professional domain conclusions without a DomainPack run."
                    ),
                    user_prompt=self._prompt(request=request, decision=decision),
                    temperature=0.2,
                ).strip()
            except Exception:
                pass
        return (
            "Agent OS Core 已接收这个请求，但当前没有可用的 LLM Provider 或匹配的专业 DomainPack。"
            "你可以继续提供更具体的任务，或安装对应的 DomainPack。"
        )

    @staticmethod
    def _is_context_recall(query: str) -> bool:
        normalized = re.sub(r"\s+", " ", query).casefold()
        chinese_markers = (
            "".join(map(chr, (0x521a, 0x521a, 0x5728, 0x7814, 0x7a76, 0x4ec0, 0x4e48))),
            "".join(map(chr, (0x521a, 0x624d, 0x5728, 0x7814, 0x7a76, 0x4ec0, 0x4e48))),
            "".join(map(chr, (0x521a, 0x521a, 0x5206, 0x6790, 0x4e86, 0x4ec0, 0x4e48))),
            "".join(map(chr, (0x521a, 0x624d, 0x5206, 0x6790, 0x4e86, 0x4ec0, 0x4e48))),
        )
        if any(marker in query for marker in chinese_markers):
            return True
        return any(
            marker in normalized or marker in query
            for marker in (
                "what were we researching",
                "what did we just research",
                "what were we analyzing",
                "what did we just analyze",
                "\\u521a\\u521a\\u5728\\u7814\\u7a76\\u4ec0\\u4e48",
                "\\u521a\\u624d\\u5728\\u7814\\u7a76\\u4ec0\\u4e48",
                "\\u521a\\u521a\\u5206\\u6790\\u4e86\\u4ec0\\u4e48",
                "\\u521a\\u624d\\u5206\\u6790\\u4e86\\u4ec0\\u4e48",
            )
        )

    @staticmethod
    def _legacy_context_recall_answer(request: RequestEnvelope) -> str | None:
        subject = ""
        summary = ""
        for item in reversed(list(request.conversation_context)):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or item.get("summary") or "").strip()
            if not content:
                continue
            role = str(item.get("role") or "").strip()
            turn_type = str(item.get("turn_type") or "").strip()
            if not subject and role == "user" and turn_type == "research_query":
                subject = content
            if not summary and role == "assistant" and turn_type == "research_result":
                summary = content
        if not subject and not summary:
            return None
        if summary:
            summary = summary[:600]
        if subject and summary:
            return f"\\u521a\\u521a\u4e3b\u8981\u7814\u7a76\u7684\u662f：{subject}。\n\n上一轮结果摘要：{summary}"
        return f"\\u521a\\u521a\u4e3b\u8981\u7814\u7a76\u7684是：{subject or summary}"

    @staticmethod
    def _context_recall_answer(request: RequestEnvelope) -> str | None:
        subject = ""
        summary = ""
        for item in reversed(list(request.conversation_context)):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or item.get("summary") or "").strip()
            if not content:
                continue
            role = str(item.get("role") or "").strip()
            turn_type = str(item.get("turn_type") or "").strip()
            if not subject and role == "user" and turn_type == "research_query":
                subject = content
            if not summary and role == "assistant" and turn_type == "research_result":
                summary = content
        if not subject and not summary:
            return None
        prefix = "".join(map(chr, (0x521a, 0x521a, 0x4e3b, 0x8981, 0x7814, 0x7a76, 0x7684, 0x662f)))
        if subject and summary:
            return f"{prefix}: {subject} | Previous result summary: {summary[:600]}"
        return f"{prefix}: {subject or summary}"

    @staticmethod
    def _prompt(*, request: RequestEnvelope, decision: RequestIntakeDecision) -> str:
        return "\n".join(
            [
                f"Request kind: {decision.request_kind}",
                f"Candidate capabilities: {', '.join(decision.candidate_capabilities)}",
                f"User request: {request.user_query}",
                f"Conversation context: {request.conversation_context}",
            ]
        )

    @staticmethod
    def _assistant_artifact(
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        run_id: str,
        thread_id: str,
        message: str,
        tool_results: list,
    ) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_type="assistant_message",
            schema_version="1",
            run_id=run_id,
            thread_id=thread_id,
            request_id=request.request_id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            producer=ArtifactProducer(
                executor="main_agent",
                capability="core.conversation.answer",
                node_id=None,
                subagent_type=None,
                domain_id=None,
                responsibility_node_id="main_agent",
            ),
            content={
                "message": message,
                "request_kind": decision.request_kind,
                "intent": decision.intent,
                "tool_call_ids": [item.call_id for item in tool_results],
            },
            source_refs=[{"type": "request", "id": request.request_id}],
            confidence=decision.confidence or 0.5,
        )

    @staticmethod
    def _persist_state(root: Path, state: MainAgentState) -> None:
        write_json(root / "main_agent_state.json", state.model_dump(mode="json"))

    def _emit(self, root: Path, callback, event_type: str, **payload: Any) -> None:
        event_payload = dict(payload)
        request_payload = read_json(root / "request.json") or read_json(root / "agent_os_request.json")
        if isinstance(request_payload, dict) and isinstance(request_payload.get("request"), dict):
            request_payload = request_payload["request"]
        event_payload.setdefault("run_id", "unknown")
        event_payload.setdefault("thread_id", "unknown")
        event_payload.setdefault("request_id", "unknown")
        event_payload.setdefault(
            "user_id",
            request_payload.get("user_id") if isinstance(request_payload, dict) else None,
        )
        event_payload.setdefault(
            "tenant_id",
            request_payload.get("tenant_id") if isinstance(request_payload, dict) else None,
        )
        event_payload.setdefault("graph_revision", None)
        event_payload.setdefault("actor", "main_agent")
        run_id = str(event_payload["run_id"])
        with self._event_lock:
            event = EventEnvelope(
                event_type=event_type,
                turn_id=self._event_turn_ids.get(run_id),
                sequence=0,
                **event_payload,
            ).model_dump(mode="json")
            event = append_canonical_event(root / "execution_events.jsonl", event)
        if callback is not None:
            callback(event)


class MainAgentPlanRole:
    """Planning role of the existing Main Agent; this is not another Agent."""

    def __init__(self, planner) -> None:
        self.planner = planner

    def build_initial_graph(self, request):
        """Materialize the initial lazy graph as the Main Agent planning role."""
        return self.planner.planner.build_lazy_graph(request)

    def expand_compound(self, **kwargs):
        patch = self.planner.expand_compound(**kwargs)
        return patch.model_copy(update={"actor": "main_agent.plan_agent"})

    def decide_expand_compound(self, **kwargs) -> PlanDecision:
        return self._decision_for_patch(self.expand_compound(**kwargs))

    def evaluate_frontier(self, **kwargs):
        evaluator = getattr(self.planner, "evaluate_frontier", None)
        if evaluator is None:
            return None
        patch = evaluator(**kwargs)
        if patch is None:
            return None
        return patch.model_copy(update={"actor": "main_agent.plan_agent"})

    def decide_frontier(self, **kwargs) -> PlanDecision:
        patch = self.evaluate_frontier(**kwargs)
        if patch is None:
            return PlanDecision(
                action="continue",
                reason="The current frontier satisfies the active capability requirements.",
            )
        return self._decision_for_patch(patch)

    @staticmethod
    def _decision_for_patch(patch) -> PlanDecision:
        action = "continue"
        if patch.decision_type == "replan":
            action = "replan"
        elif patch.decision_type == "stop":
            action = "stop"
        elif any(operation.op_type == "rerun_from" for operation in patch.operations):
            action = "retry"
        return PlanDecision(
            action=action,
            reason=(
                patch.notes[-1]
                if patch.notes
                else "Main Agent planning role updated the execution frontier."
            ),
            graph_patch=patch,
            confidence=patch.confidence,
        )


__all__ = ["MainAgent", "MainAgentPlanRole"]
