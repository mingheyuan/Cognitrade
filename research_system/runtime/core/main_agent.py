from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from pathlib import Path
from threading import Event, RLock
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
from research_system.runtime.storage.jsonl import append_jsonl, read_json, read_jsonl, write_json
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.core_runtime import (
    AgentToolCallRequest,
    AgentTurnResponse,
    MainAgentRunResult,
    MainAgentState,
    PlanDecision,
    RequestIntakeDecision,
)
from research_system.schemas.events import EventEnvelope
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.turn_runtime import FollowUpConfig, TurnConfig


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
        """Compatibility facade; standard execution is owned by TurnRunner."""
        if self._turn_runner is not None:
            outcome = self._turn_runner.execute_task(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id=thread_id,
                execution_mode=(turn_config.execution_mode if turn_config else decision.recommended_mode),
                turn_config=turn_config,
                event_callback=event_callback,
                resume=resume,
            )
            return outcome.result
        return self._run_core_task(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id=thread_id,
            event_callback=event_callback,
            resume=resume,
            emit_started=emit_started,
            turn_id=turn_id,
            hook_runtime=hook_runtime,
            follow_up_runtime=follow_up_runtime,
            turn_config=turn_config,
        )

    def _run_core_task(
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
        if turn_id:
            self._event_turn_ids[run_id] = turn_id
        paths = self.run_store.load_run_paths(run_id)
        board = ArtifactBoard(paths.artifact_board)
        board.bind_thread(thread_id)
        persisted = read_json(paths.root / "main_agent_state.json")
        if resume and isinstance(persisted, dict):
            state = MainAgentState.model_validate(persisted).model_copy(
                update={
                    "status": "running",
                    "error": None,
                    "clarification_question": None,
                    "missing_fields": [],
                    "updated_at": utc_now(),
                }
            )
        else:
            mode = decision.recommended_mode
            state = MainAgentState(
                run_id=run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                status="running",
                mode=mode,
                intent=decision.intent,
                request_kind=decision.request_kind,
                tool_loop_limit=self._tool_loop_limit(),
            )
        mode = state.mode
        control_path = paths.root / "execution_control.json"
        if resume or not control_path.exists():
            write_json(
                control_path,
                {"pause_requested": False, "cancel_requested": False, "updated_at": utc_now().isoformat()},
            )
        self._persist_state(paths.root, state)
        if emit_started:
            self._emit(
                paths.root,
                event_callback,
                "main_agent_resumed" if resume else "main_agent_started",
                run_id=run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                intent=decision.intent,
                mode=mode,
            )

        cancellation = Event()
        context = CoreToolContext(
            run_id=run_id,
            thread_id=thread_id,
            request_id=request.request_id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            run_root=paths.root,
            actor="main_agent",
            turn_id=turn_id,
            artifact_board=board,
            memory_store=self.memory_store,
            settings=self.settings,
            cancellation=cancellation,
            event_callback=event_callback,
            hook_runtime=hook_runtime,
            allowed_tools=frozenset(
                item.name
                for item in self.tools.definitions()
                if item.name.startswith("core.")
            ),
        )
        tool_results = []
        try:
            control_status = self._control_status(paths.root)
            if control_status is not None:
                return self._control_result(
                    root=paths.root,
                    state=state,
                    status=control_status,
                    request=request,
                    decision=decision,
                    tool_results=tool_results,
                    event_callback=event_callback,
                )
            message, state = self._run_agent_loop(
                request=request,
                decision=decision,
                context=context,
                tool_results=tool_results,
                state=state,
                root=paths.root,
                event_callback=event_callback,
                hook_runtime=hook_runtime,
                follow_up_runtime=follow_up_runtime,
                turn_config=turn_config,
            )
            control_status = self._control_status(paths.root)
            if control_status is not None:
                return self._control_result(
                    root=paths.root,
                    state=state,
                    status=control_status,
                    request=request,
                    decision=decision,
                    tool_results=tool_results,
                    event_callback=event_callback,
                )
            if state.status in {"gated", "failed", "cancelled", "timed_out", "waiting_approval"}:
                return MainAgentRunResult(
                    run_id=run_id,
                    thread_id=thread_id,
                    request_id=request.request_id,
                    status=state.status,
                    mode=state.mode,
                    intent=decision.intent,
                    message=message,
                    artifact_ids=list(state.artifact_ids),
                    tool_calls=tool_results,
                    continuation=state.continuation,
                    state=state,
                )
            if state.clarification_question:
                composed = self.tools.invoke(
                    tool_name="core.message.compose",
                    arguments={"message": message},
                    context=context,
                )
                tool_results.append(composed)
                if composed.status == "completed":
                    message = str(composed.output.get("message") or message)
                artifact = self._assistant_artifact(
                    request=request,
                    decision=decision,
                    run_id=run_id,
                    thread_id=thread_id,
                    message=message,
                    tool_results=tool_results,
                )
                artifact = board.add(artifact)
                state = state.model_copy(
                    update={
                        "assistant_message": message,
                        "artifact_ids": [artifact.artifact_id],
                        "tool_call_ids": [item.call_id for item in tool_results],
                        "updated_at": utc_now(),
                    }
                )
                self._persist_state(paths.root, state)
                self._emit(
                    paths.root,
                    event_callback,
                    "assistant_message_ready",
                    run_id=run_id,
                    thread_id=thread_id,
                    request_id=request.request_id,
                    mode=state.mode,
                    delivery_status="paused",
                    message=message,
                    artifact_ids=[artifact.artifact_id],
                )
                self._emit(
                    paths.root,
                    event_callback,
                    "main_agent_clarification_requested",
                    run_id=run_id,
                    thread_id=thread_id,
                    request_id=request.request_id,
                    question=state.clarification_question,
                    missing_fields=list(state.missing_fields),
                )
                return MainAgentRunResult(
                    run_id=run_id,
                    thread_id=thread_id,
                    request_id=request.request_id,
                    status="paused",
                    mode=state.mode,
                    intent=decision.intent,
                    message=message,
                    clarification_question=state.clarification_question,
                    missing_fields=list(state.missing_fields),
                    artifact_ids=[artifact.artifact_id],
                    tool_calls=tool_results,
                    state=state,
                )
            composed = self.tools.invoke(
                tool_name="core.message.compose",
                arguments={"message": message},
                context=context,
            )
            tool_results.append(composed)
            if composed.status != "completed":
                raise RuntimeError(composed.error or "core.message.compose failed")
            message = str(composed.output.get("message") or message)
            artifact = self._assistant_artifact(
                request=request,
                decision=decision,
                run_id=run_id,
                thread_id=thread_id,
                message=message,
                tool_results=tool_results,
            )
            artifact = board.add(artifact)
            state = state.model_copy(
                update={
                    "status": "completed",
                    "assistant_message": message,
                    "artifact_ids": [artifact.artifact_id],
                    "tool_call_ids": [item.call_id for item in tool_results],
                    "updated_at": utc_now(),
                }
            )
            self._persist_state(paths.root, state)
            self._emit(
                paths.root,
                event_callback,
                "assistant_message_ready",
                run_id=run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                intent=decision.intent,
                mode=state.mode,
                delivery_status="completed",
                message=message,
                artifact_ids=[artifact.artifact_id],
            )
            self._emit(
                paths.root,
                event_callback,
                "main_agent_completed",
                run_id=run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                intent=decision.intent,
                mode=state.mode,
                artifact_ids=[artifact.artifact_id],
            )
            return MainAgentRunResult(
                run_id=run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                status="completed",
                mode=state.mode,
                intent=decision.intent,
                message=message,
                artifact_ids=[artifact.artifact_id],
                tool_calls=tool_results,
                state=state,
            )

        except Exception as exc:
            state = state.model_copy(
                update={
                    "status": "failed",
                    "error": f"{exc.__class__.__name__}: {exc}",
                    "tool_call_ids": [item.call_id for item in tool_results],
                    "updated_at": utc_now(),
                }
            )
            self._persist_state(paths.root, state)
            self._emit(
                paths.root,
                event_callback,
                "main_agent_failed",
                run_id=run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                intent=decision.intent,
                mode=state.mode,
                error=state.error,
            )
            return MainAgentRunResult(
                run_id=run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                status="failed",
                mode=state.mode,
                intent=decision.intent,
                message="Agent OS Core Agent Loop failed to complete the request.",
                artifact_ids=[],
                tool_calls=tool_results,
                state=state,
            )

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

    @staticmethod
    def _control_status(root: Path) -> str | None:
        control = read_json(root / "execution_control.json") or {}
        if control.get("cancel_requested"):
            return "cancelled"
        if control.get("pause_requested"):
            return "paused"
        return None

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

    def _run_agent_loop(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        context: CoreToolContext,
        tool_results: list,
        state: MainAgentState,
        root: Path,
        event_callback: Callable[[dict[str, Any]], None] | None,
        hook_runtime=None,
        follow_up_runtime: FollowUpRuntime | None = None,
        turn_config: TurnConfig | None = None,
    ) -> tuple[str, MainAgentState]:
        """Run a bounded provider-native Tool loop, with a deterministic fallback.

        Providers that do not expose ``complete_with_tools`` still use the
        existing deterministic/Core response path. The persisted transcript
        makes a pause or retry resume the same Main Agent context instead of
        silently starting a second loop.
        """
        provider = self.llm_provider
        native_turn = getattr(provider, "complete_with_tools", None) if provider is not None else None
        available = bool(provider is not None and getattr(provider, "available", False))
        if (
            provider is not None
            and provider.__class__.__name__ == "OpenAIProvider"
            and self._has_deterministic_core_route(request.user_query)
        ):
            self._emit_loop_progress(
                root,
                event_callback,
                state=state,
                turn=max(1, state.turn_count + 1),
                phase="core_route",
                current_action="Main Agent entered the deterministic Core path",
                next_action="Execute the Core path and collect its result",
            )
            message = self._answer(
                request=request,
                decision=decision,
                context=context,
                tool_results=tool_results,
            )
            self._emit_loop_progress(
                root,
                event_callback,
                state=state,
                turn=max(1, state.turn_count + 1),
                phase="core_tool_result",
                current_action=(
                    "Core path completed"
                    + (
                        f" with Tool(s): {', '.join(item.tool_name for item in tool_results)}"
                        if tool_results
                        else " without Tool calls"
                    )
                ),
                next_action="Persist the assistant message and finish the Run",
                status="completed",
            )
            state, should_continue = self._apply_stop_hook_candidate(
                message=message,
                state=state,
                root=root,
                hook_runtime=hook_runtime,
                follow_up_runtime=follow_up_runtime,
                turn_config=turn_config,
                event_callback=event_callback,
            )
            if should_continue:
                state = state.model_copy(
                    update={
                        "status": "gated",
                        "error": "StopHook requested continuation but no provider loop is available.",
                    }
                )
                self._persist_state(root, state)
            return message, state
        if not callable(native_turn) or not available:
            self._emit_loop_progress(
                root,
                event_callback,
                state=state,
                turn=max(1, state.turn_count + 1),
                phase="core_route",
                current_action="Main Agent entered the deterministic Core path",
                next_action="Execute the Core path and collect its result",
            )
            message = self._answer(
                request=request,
                decision=decision,
                context=context,
                tool_results=tool_results,
            )
            self._emit_loop_progress(
                root,
                event_callback,
                state=state,
                turn=max(1, state.turn_count + 1),
                phase="core_tool_result",
                current_action=(
                    "Core path completed"
                    + (
                        f" with Tool(s): {', '.join(item.tool_name for item in tool_results)}"
                        if tool_results
                        else " without Tool calls"
                    )
                ),
                next_action="Persist the assistant message and finish the Run",
                status="completed",
            )
            state, should_continue = self._apply_stop_hook_candidate(
                message=message,
                state=state,
                root=root,
                hook_runtime=hook_runtime,
                follow_up_runtime=follow_up_runtime,
                turn_config=turn_config,
                event_callback=event_callback,
            )
            if should_continue:
                state = state.model_copy(
                    update={
                        "status": "gated",
                        "error": "StopHook requested continuation but no provider loop is available.",
                    }
                )
                self._persist_state(root, state)
            return message, state

        transcript = list(state.transcript)
        if not transcript:
            transcript.append({"role": "user", "content": request.user_query})
        limit = state.tool_loop_limit or self._tool_loop_limit()
        for turn_index in range(state.turn_count, limit):
            if follow_up_runtime is not None:
                pending_inputs = follow_up_runtime.deliver_current()
                if pending_inputs:
                    for item in pending_inputs:
                        transcript.append(
                            {
                                "role": "user",
                                "content": item.text,
                                "source": item.source,
                                "hook_originated": item.source == "hook",
                                "operation_id": item.operation_id,
                            }
                        )
                    state = state.model_copy(
                        update={
                            "follow_up_count": follow_up_runtime.follow_up_count,
                            "transcript": self._trim_transcript(transcript),
                            "updated_at": utc_now(),
                        }
                    )
                    self._persist_state(root, state)
            control_status = self._control_status(root)
            if control_status is not None:
                state = state.model_copy(
                    update={
                        "status": control_status,
                        "transcript": self._trim_transcript(transcript),
                        "turn_count": turn_index,
                        "updated_at": utc_now(),
                    }
                )
                self._persist_state(root, state)
                return state.assistant_message or (
                    "Agent OS Core Agent Loop paused."
                    if control_status == "paused"
                    else "Agent OS Core Agent Loop cancelled."
                ), state

            state = state.model_copy(
                update={
                    "turn_count": turn_index + 1,
                    "transcript": self._trim_transcript(transcript),
                    "updated_at": utc_now(),
                }
            )
            self._persist_state(root, state)
            self._emit(
                root,
                event_callback,
                "main_agent_turn_started",
                run_id=state.run_id,
                thread_id=state.thread_id,
                request_id=state.request_id,
                mode=state.mode,
                turn=turn_index + 1,
                message=f"Main Agent started loop {turn_index + 1}.",
            )
            self._emit_loop_progress(
                root,
                event_callback,
                state=state,
                turn=turn_index + 1,
                phase="model_turn",
                current_action=f"Main Agent model call {turn_index + 1} started",
                next_action="Wait for the model response",
            )
            operation_id = f"model:{state.run_id}:{turn_index + 1}"
            self._emit(
                root,
                event_callback,
                "model_call_started",
                run_id=state.run_id,
                thread_id=state.thread_id,
                request_id=state.request_id,
                turn_id=self._event_turn_ids.get(state.run_id),
                mode=state.mode,
                turn=turn_index + 1,
                operation_id=operation_id,
            )
            try:
                response = self._provider_tool_turn(
                    provider=provider,
                    request=request,
                    decision=decision,
                    transcript=transcript,
                    native_turn=native_turn,
                    definitions=[
                        item
                        for item in self.tools.definitions()
                        if item.name.startswith("core.")
                    ],
                )
                self._emit(
                    root,
                    event_callback,
                    "model_call_completed",
                    run_id=state.run_id,
                    thread_id=state.thread_id,
                    request_id=state.request_id,
                    turn_id=self._event_turn_ids.get(state.run_id),
                    mode=state.mode,
                    turn=turn_index + 1,
                    operation_id=operation_id,
                    response_kind=response.kind,
                )
            except Exception as exc:
                self._emit(
                    root,
                    event_callback,
                    "main_agent_model_turn_failed",
                    run_id=state.run_id,
                    thread_id=state.thread_id,
                    request_id=state.request_id,
                    mode=state.mode,
                    turn=turn_index + 1,
                    error=f"{exc.__class__.__name__}: {exc}",
                    operation_id=operation_id,
                )
                return self._answer(
                    request=request,
                    decision=decision,
                    context=context,
                    tool_results=tool_results,
                ), state
            if response.kind == "clarification":
                question = str(response.question or response.message or "").strip()
                if not question:
                    question = "Please clarify the request before I continue."
                current_action, next_action = self._response_progress(
                    response,
                    fallback_current=f"Model requested clarification: {question[:240]}",
                    fallback_next="Wait for the user response before resuming this Run",
                )
                self._emit_loop_progress(
                    root,
                    event_callback,
                    state=state,
                    turn=turn_index + 1,
                    phase="clarification",
                    current_action=current_action,
                    next_action=next_action,
                    status="paused",
                )
                self._emit(
                    root,
                    event_callback,
                    "main_agent_turn_completed",
                    run_id=state.run_id,
                    thread_id=state.thread_id,
                    request_id=state.request_id,
                    mode=state.mode,
                    turn=turn_index + 1,
                    message=question,
                    status="clarification",
                )
                transcript.append({"role": "assistant", "content": question})
                state = state.model_copy(
                    update={
                        "status": "paused",
                        "clarification_question": question,
                        "missing_fields": list(response.missing_fields),
                        "assistant_message": question,
                        "transcript": self._trim_transcript(transcript),
                        "updated_at": utc_now(),
                    }
                )
                self._persist_state(root, state)
                return question, state
            if response.kind != "tool_calls" or not response.tool_calls:
                message = response.message or response.question
                if message:
                    current_action, next_action = self._response_progress(
                        response,
                        fallback_current=f"Model returned a final answer: {str(message)[:240]}",
                        fallback_next="Persist the assistant message and finish the Run",
                    )
                    self._emit_loop_progress(
                        root,
                        event_callback,
                        state=state,
                        turn=turn_index + 1,
                        phase="answer",
                        current_action=current_action,
                        next_action=next_action,
                        status="completed",
                    )
                    self._emit(
                        root,
                        event_callback,
                        "main_agent_turn_completed",
                        run_id=state.run_id,
                        thread_id=state.thread_id,
                        request_id=state.request_id,
                        mode=state.mode,
                        turn=turn_index + 1,
                        message=message,
                        status="answer",
                    )
                    transcript.append({"role": "assistant", "content": message})
                    state = state.model_copy(
                        update={
                            "assistant_message": message,
                            "stop_hook_active": False,
                            "transcript": self._trim_transcript(transcript),
                            "updated_at": utc_now(),
                        }
                    )
                    self._persist_state(root, state)
                    state, should_continue = self._apply_stop_hook_candidate(
                        message=message,
                        state=state,
                        root=root,
                        hook_runtime=hook_runtime,
                        follow_up_runtime=follow_up_runtime,
                        turn_config=turn_config,
                        event_callback=event_callback,
                    )
                    if should_continue:
                        continue
                    return message, state
                break

            self._emit(
                root,
                event_callback,
                "main_agent_tool_calls_requested",
                run_id=state.run_id,
                thread_id=state.thread_id,
                request_id=state.request_id,
                mode=state.mode,
                turn=turn_index + 1,
                tool_names=[item.tool_name for item in response.tool_calls],
                tool_call_ids=[item.call_id for item in response.tool_calls],
                message=(
                    f"Main Agent loop {turn_index + 1} selected "
                    f"{len(response.tool_calls)} Tool(s)."
                ),
            )
            current_action, next_action = self._response_progress(
                response,
                fallback_current=(
                    "Model requested Tool(s): "
                    + ", ".join(item.tool_name for item in response.tool_calls)
                ),
                fallback_next=(
                    "Execute Tool(s): "
                    + ", ".join(item.tool_name for item in response.tool_calls)
                ),
            )
            self._emit_loop_progress(
                root,
                event_callback,
                state=state,
                turn=turn_index + 1,
                phase="tool_dispatch",
                current_action=current_action,
                next_action=next_action,
            )

            transcript.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "tool_name": item.tool_name,
                            "arguments": item.arguments,
                            "call_id": item.call_id,
                        }
                        for item in response.tool_calls
                    ],
                }
            )
            for requested in response.tool_calls:
                result = self.tools.invoke(
                    tool_name=requested.tool_name,
                    arguments=requested.arguments,
                    context=context,
                )
                tool_results.append(result)
                transcript.append(
                    {
                        "role": "tool",
                        "tool_name": result.tool_name,
                        "call_id": result.call_id,
                        "status": result.status,
                        "output": self._trim_tool_output(result.output),
                        "error": result.error,
                    }
                )
                state = state.model_copy(
                    update={
                        "tool_call_ids": [item.call_id for item in tool_results],
                        "transcript": self._trim_transcript(transcript),
                        "updated_at": utc_now(),
                    }
                )
                self._persist_state(root, state)
                self._emit(
                    root,
                    event_callback,
                    "main_agent_tool_loop_result",
                    run_id=state.run_id,
                    thread_id=state.thread_id,
                    request_id=state.request_id,
                    mode=state.mode,
                    turn=turn_index + 1,
                    tool_name=result.tool_name,
                    call_id=result.call_id,
                    status=result.status,
                    output_summary=self._tool_output_summary(result),
                    message=(
                        f"Main Agent loop {turn_index + 1} received "
                        f"the result from Tool {result.tool_name}."
                    ),
                )
                self._emit_loop_progress(
                    root,
                    event_callback,
                    state=state,
                    turn=turn_index + 1,
                    phase="tool_result",
                    current_action=(
                        f"Tool {result.tool_name} returned {result.status}"
                        + (
                            f": {self._tool_output_summary(result)[:240]}"
                            if result.status == "completed"
                            else f": {result.error or result.status}"
                        )
                    ),
                    next_action=f"Write the Tool {result.tool_name} result into the next Main Agent turn",
                    status=result.status,
                )

            if follow_up_runtime is not None and follow_up_runtime.config.allow_model_follow_up:
                try:
                    follow_up_runtime.record_model_follow_up()
                    state = state.model_copy(
                        update={
                            "follow_up_count": follow_up_runtime.follow_up_count,
                            "updated_at": utc_now(),
                        }
                    )
                    self._persist_state(root, state)
                except RuntimeError as exc:
                    state = state.model_copy(
                        update={
                            "status": "gated",
                            "error": str(exc),
                            "updated_at": utc_now(),
                        }
                    )
                    self._persist_state(root, state)
                    return "Agent OS follow-up limit exceeded.", state

        message = (
            "Agent OS Core Agent Loop reached its Tool-loop limit before producing a final answer."
        )
        self._emit_loop_progress(
            root,
            event_callback,
            state=state,
            turn=state.turn_count,
            phase="loop_limit",
            current_action=f"Tool loop limit reached after {state.turn_count} turn(s)",
            next_action="Return the bounded response and finish the Run",
            status="bounded",
        )
        state = state.model_copy(
            update={
                "assistant_message": message,
                "transcript": self._trim_transcript(transcript),
                "updated_at": utc_now(),
            }
        )
        self._persist_state(root, state)
        return message, state

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
    ) -> tuple[MainAgentState, bool]:
        """Run the only execution-time stop decision shared by Core and Task mode."""
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
            target="stop",
            cwd=str(root),
            transcript_ref=str(root / "task_state.json"),
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
        event_payload.setdefault("run_id", "unknown")
        event_payload.setdefault("thread_id", "unknown")
        event_payload.setdefault("request_id", "unknown")
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
