from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

from research_system.runtime.core.approvals import HumanApprovalStore
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.tool_registry import CoreToolContext
from research_system.runtime.presentation.display import localize_runtime_error
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.runtime.turn_context import TurnContext
from research_system.schemas.core_runtime import (
    AgentTurnResponse,
    CoreToolResult,
    MainAgentRunResult,
    MainAgentState,
)
from research_system.schemas.turn_runtime import TaskOutcome


@dataclass
class _CoreLoopSession:
    state: Any
    transcript: list[dict[str, Any]]
    tool_results: list[Any]
    context: CoreToolContext
    board: ArtifactBoard


class CoreLoopExecutor:
    """Own the serial provider/tool loop for a RegularTask.

    ``MainAgent`` supplies routing, provider and domain helpers.  It no longer
    owns the loop lifecycle: every call to ``_step`` advances exactly one
    provider turn or one deterministic completion, then persists a checkpoint.
    """

    def __init__(self, main_agent: Any) -> None:
        self.main_agent = main_agent

    def execute(self, context: TurnContext) -> TaskOutcome:
        if not self._supports_incremental_main_agent():
            raise TypeError(
                "TurnRunner requires MainAgent's incremental Core runtime contract"
            )
        session = context.core_session
        if session is None:
            session = self._prepare(context)
            context.core_session = session
        if context.deadline_at is not None and monotonic() >= context.deadline_at:
            return self._timed_out(context, session)
        if session.state.status in {
            "completed",
            "completed_partial",
            "gated",
            "failed",
            "cancelled",
        }:
            return self._result(
                context,
                session,
                session.state.status,
                session.state.assistant_message
                or session.state.error
                or "当前 Turn 已进入终态。",
            )
        try:
            # RegularExecutor owns the only loop.  One call here is exactly
            # one provider/tool safe point, so control, hooks and Graph share
            # the same continuation boundary.
            return self._step(context, session)
        except Exception as exc:
            return self._failed(context, session, exc)

    def _supports_incremental_main_agent(self) -> bool:
        return all(
            callable(getattr(self.main_agent, name, None))
            for name in ("_provider_tool_turn", "_trim_transcript", "_assistant_artifact")
        ) and hasattr(self.main_agent, "tools")

    def _prepare(self, context: TurnContext) -> _CoreLoopSession:
        root = context.root
        board = ArtifactBoard(root / "artifact_board.jsonl")
        board.bind_thread(context.thread_id)
        persisted = read_json(root / "main_agent_state.json")
        if context.recovery_continuation and isinstance(persisted, dict):
            persisted_state = MainAgentState.model_validate(persisted)
            state = persisted_state.model_copy(
                update={
                    "status": "running",
                    "error": None,
                    "clarification_question": None,
                    "missing_fields": [],
                    "updated_at": _utc_now(),
                }
            )
        elif context.resume and isinstance(persisted, dict):
            persisted_state = MainAgentState.model_validate(persisted)
            if persisted_state.status in {
                "completed",
                "completed_partial",
                "gated",
                "failed",
                "cancelled",
            } and (persisted_state.artifact_ids or persisted_state.error):
                state = persisted_state
            else:
                state = persisted_state.model_copy(
                    update={
                        "status": "running",
                        "error": None,
                        "clarification_question": None,
                        "missing_fields": [],
                        "updated_at": _utc_now(),
                    }
                )
        elif isinstance(persisted, dict):
            state = MainAgentState.model_validate(persisted).model_copy(
                update={
                    "status": "running",
                    "mode": context.decision.recommended_mode,
                    "intent": context.decision.intent,
                    "request_kind": context.decision.request_kind,
                    "error": None,
                    "updated_at": _utc_now(),
                }
            )
        else:
            state = MainAgentState(
                run_id=context.run_id,
                thread_id=context.thread_id,
                request_id=context.request.request_id,
                status="running",
                mode=context.decision.recommended_mode,
                intent=context.decision.intent,
                request_kind=context.decision.request_kind,
                tool_loop_limit=self.main_agent._tool_loop_limit(),
            )
        control_path = root / "execution_control.json"
        if not control_path.exists():
            write_json(
                control_path,
                {
                    "pause_requested": False,
                    "cancel_requested": False,
                    "updated_at": _utc_now().isoformat(),
                },
            )
        self.main_agent._event_turn_ids[context.run_id] = context.turn.turn_id
        self.main_agent._persist_state(root, state)
        if context.event_callback is not None:
            self._emit(
                context,
                "main_agent_resumed" if context.resume else "main_agent_started",
                intent=context.decision.intent,
                mode=state.mode,
            )
        cancellation = Event()
        approval_store = context.approval_store or HumanApprovalStore(root / "approvals.jsonl")
        if context.role_contract is not None and context.role_contract.requires_approval:
            context.role_approval_id = self._role_approval_id(context)
            existing_approval = approval_store.get(context.role_approval_id)
            if existing_approval is None:
                approval_store.request(
                    run_id=context.run_id,
                    node_id=f"child_role:{context.child_role_id or context.role_contract.role_id}",
                    capability=context.role_contract.role_id,
                    reason=f"子角色 {context.role_contract.role_id} 需要人工审批。",
                    approval_id=context.role_approval_id,
                    user_id=context.request.user_id,
                    tenant_id=context.request.tenant_id,
                )
                existing_approval = approval_store.get(context.role_approval_id)
            if existing_approval is not None and existing_approval.status != "approved":
                state = state.model_copy(
                    update={
                        "status": "waiting_approval",
                        "pending_approval_ids": [context.role_approval_id],
                        "continuation": {
                            "source": "child_role_approval",
                            "approval_id": context.role_approval_id,
                            "role_id": context.role_contract.role_id,
                        },
                        "updated_at": _utc_now(),
                    }
                )
                self.main_agent._persist_state(root, state)
        actor = (
            f"subagent:{context.child_role_id}"
            if context.child_role_id
            else "main_agent"
        )
        if context.role_contract is None:
            allowed_tools = frozenset(
                item.name
                for item in self.main_agent.tools.definitions()
                if item.name.startswith("core.")
            )
            allowed_permissions = None
        else:
            allowed_tools = frozenset(
                item
                for item in context.role_contract.allowed_tools
                if item not in context.role_contract.denied_tools
            )
            allowed_permissions = frozenset(context.role_contract.allowed_permissions)
        core_context = CoreToolContext(
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            user_id=context.request.user_id,
            tenant_id=context.request.tenant_id,
            run_root=root,
            actor=actor,
            turn_id=context.turn.turn_id,
            artifact_board=board,
            memory_store=getattr(self.main_agent, "memory_store", None),
            settings=getattr(self.main_agent, "settings", None),
            cancellation=cancellation,
            event_callback=context.event_callback,
            allowed_tools=allowed_tools,
            allowed_permissions=allowed_permissions,
            idempotency_key=context.request.idempotency_key,
            hook_runtime=context.hook_runtime,
            followup_task_handler=context.followup_task_handler,
            approval_store=approval_store,
        )
        transcript = list(state.transcript)
        if not transcript:
            transcript.append({"role": "user", "content": context.request.user_query})
        tool_results = self._load_tool_results(root)
        return _CoreLoopSession(
            state=state,
            transcript=transcript,
            tool_results=tool_results,
            context=core_context,
            board=board,
        )

    def _step(self, context: TurnContext, session: _CoreLoopSession) -> TaskOutcome:
        root = context.root
        follow_up = context.follow_up_runtime
        if follow_up is not None:
            pending_inputs = follow_up.deliver_current()
            for item in pending_inputs:
                prompt_payload = {
                    "run_id": context.run_id,
                    "thread_id": context.thread_id,
                    "request_id": context.request.request_id,
                    "turn_id": context.turn.turn_id,
                    "source": item.source,
                    "kind": item.kind,
                    "text": item.text,
                    "operation_id": item.operation_id,
                }
                item_text = item.text
                if context.hook_runtime is not None:
                    allowed, rewritten = context.hook_runtime.run_event(
                        "UserPromptSubmit",
                        prompt_payload,
                    )
                    if not allowed:
                        reason = str(
                            rewritten.get("_hook_block_reason")
                            or "UserPromptSubmit hook blocked pending input."
                        )
                        continuation = {
                            "source": "user_prompt_submit_hook",
                            "operation_id": f"user-prompt-submit:{item.operation_id}",
                            "reason": reason,
                            "input_id": item.input_id,
                        }
                        session.state = session.state.model_copy(
                            update={
                                "status": "gated",
                                "error": reason,
                                "continuation": continuation,
                                "updated_at": _utc_now(),
                            }
                        )
                        self._persist(context, session)
                        self._emit(
                            context,
                            "user_prompt_submit_blocked",
                            reason=reason,
                            input_id=item.input_id,
                            operation_id=item.operation_id,
                        )
                        return self._result(context, session, "gated", reason)
                    if (
                        context.config.hook_config is not None
                        and context.config.hook_config.policy.allow_input_rewrite
                    ):
                        item_text = str(rewritten.get("text") or item.text)
                session.transcript.append(
                    {
                        "role": "user",
                        "content": item_text,
                        "source": item.source,
                        "hook_originated": item.source == "hook",
                        "operation_id": item.operation_id,
                    }
                )
            if pending_inputs:
                session.state = session.state.model_copy(
                    update={
                        "follow_up_count": follow_up.follow_up_count,
                        "transcript": self._trim(session.transcript),
                        "updated_at": _utc_now(),
                    }
                )
                self._persist(context, session)

        control_status = self._control_status(root)
        if control_status is not None:
            return self._control(context, session, control_status)

        role_approval = self._role_approval_outcome(context, session)
        if role_approval is not None:
            return role_approval

        compacted = self._maybe_compact(context, session)
        if compacted is not None:
            return compacted

        provider = self.main_agent.llm_provider
        native_turn = getattr(provider, "complete_with_tools", None) if provider is not None else None
        available = bool(provider is not None and getattr(provider, "available", False))
        deterministic = (
            provider is not None
            and provider.__class__.__name__ == "OpenAIProvider"
            and self.main_agent._has_deterministic_core_route(context.request.user_query)
        )
        if deterministic or not callable(native_turn) or not available:
            self._emit_progress(
                context,
                phase="core_route",
                turn=max(1, session.state.turn_count + 1),
                current_action="RegularExecutor entered the deterministic Core path",
                next_action="Execute the Core path and collect its result",
            )
            message = self.main_agent._answer(
                request=context.request,
                decision=context.decision,
                context=session.context,
                tool_results=session.tool_results,
            )
            return self._complete_answer(context, session, message)

        provider_checkpoint = read_json(context.root / "provider_checkpoint.json") or {}
        checkpoint_status = str(provider_checkpoint.get("status") or "")
        recoverable_checkpoint = checkpoint_status in {"running", "response_ready"}
        limit = session.state.tool_loop_limit or self.main_agent._tool_loop_limit()
        if context.role_contract is not None:
            limit = min(limit, context.role_contract.max_model_turns)
        if session.state.turn_count >= limit and not recoverable_checkpoint:
            message = "Agent OS Core Agent Loop 已达到 Tool 循环上限，未能生成完整答复。"
            return self._complete_answer(context, session, message, status="completed_partial")

        recovered_response = None
        if provider_checkpoint.get("status") == "response_ready" and isinstance(
            provider_checkpoint.get("response"), dict
        ):
            recovered_response = AgentTurnResponse.model_validate(provider_checkpoint["response"])
            turn_index = int(provider_checkpoint.get("turn_index") or session.state.turn_count or 1)
        elif provider_checkpoint.get("status") == "running":
            turn_index = int(provider_checkpoint.get("turn_index") or session.state.turn_count or 1)
        else:
            turn_index = session.state.turn_count + 1
        session.state = session.state.model_copy(
            update={
                "turn_count": turn_index,
                "transcript": self._trim(session.transcript),
                "updated_at": _utc_now(),
            }
        )
        self._persist(context, session)
        self._emit(context, "main_agent_turn_started", turn=turn_index, mode=session.state.mode)
        self._emit_progress(
            context,
            phase="model_turn",
            turn=turn_index,
            current_action=f"RegularExecutor model call {turn_index} started",
            next_action="Wait for the model response",
        )
        operation_id = f"model:{context.run_id}:{turn_index}"
        if recovered_response is not None:
            response = recovered_response
            self._emit(
                context,
                "model_call_recovered",
                turn=turn_index,
                mode=session.state.mode,
                operation_id=operation_id,
                response_kind=response.kind,
            )
        else:
            self._write_provider_checkpoint(
                context,
                status="running",
                turn_index=turn_index,
                operation_id=operation_id,
                transcript=session.transcript,
            )
            self._emit(context, "model_call_started", turn=turn_index, mode=session.state.mode, operation_id=operation_id)
            try:
                response = self.main_agent._provider_tool_turn(
                    provider=provider,
                    request=context.request,
                    decision=context.decision,
                    transcript=session.transcript,
                    native_turn=native_turn,
                    definitions=[
                        item
                        for item in self.main_agent.tools.definitions()
                        if item.name.startswith("core.")
                    ],
                )
                self._write_provider_checkpoint(
                    context,
                    status="response_ready",
                    turn_index=turn_index,
                    operation_id=operation_id,
                    transcript=session.transcript,
                    response=response,
                )
                self._emit(
                    context,
                    "model_call_completed",
                    turn=turn_index,
                    mode=session.state.mode,
                    operation_id=operation_id,
                    response_kind=response.kind,
                )
            except Exception as exc:
                self._write_provider_checkpoint(
                    context,
                    status="failed",
                    turn_index=turn_index,
                    operation_id=operation_id,
                    transcript=session.transcript,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
                self._emit(
                    context,
                    "main_agent_model_turn_failed",
                    turn=turn_index,
                    mode=session.state.mode,
                    operation_id=operation_id,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
                message = self.main_agent._answer(
                    request=context.request,
                    decision=context.decision,
                    context=session.context,
                    tool_results=session.tool_results,
                )
                return self._complete_answer(context, session, message)

        if response.kind == "clarification":
            question = str(response.question or response.message or "").strip()
            question = question or "请补充请求信息后再继续。"
            session.transcript.append({"role": "assistant", "content": question})
            composed = self._invoke_tool(
                context,
                session,
                "core.message.compose",
                {"message": question},
                operation_id=f"compose:{operation_id}",
            )
            self._record_tool_result(context, session, composed)
            if composed.status == "completed":
                question = str(composed.output.get("message") or question)
            artifact = self.main_agent._assistant_artifact(
                request=context.request,
                decision=context.decision,
                run_id=context.run_id,
                thread_id=context.thread_id,
                message=question,
                tool_results=session.tool_results,
            )
            artifact = session.board.add(artifact)
            session.state = session.state.model_copy(
                update={
                    "status": "paused",
                    "clarification_question": question,
                    "missing_fields": list(response.missing_fields),
                    "assistant_message": question,
                    "artifact_ids": [artifact.artifact_id],
                    "tool_call_ids": [item.call_id for item in session.tool_results],
                    "transcript": self._trim(session.transcript),
                    "updated_at": _utc_now(),
                }
            )
            self._persist(context, session)
            self._emit(
                context,
                "assistant_message_ready",
                mode=session.state.mode,
                delivery_status="paused",
                message=question,
                artifact_ids=[artifact.artifact_id],
            )
            self._emit(
                context,
                "main_agent_clarification_requested",
                question=question,
                missing_fields=list(response.missing_fields),
            )
            self._emit_progress(
                context,
                phase="clarification",
                turn=turn_index,
                current_action=f"模型请求补充信息：{question[:240]}",
                next_action="等待用户回复后恢复当前 Turn",
                status="paused",
            )
            self._write_provider_checkpoint(
                context,
                status="handled",
                turn_index=turn_index,
                operation_id=operation_id,
                transcript=session.transcript,
            )
            return self._result(context, session, "paused", question)

        if response.kind != "tool_calls" or not response.tool_calls:
            message = response.message or response.question
            if message:
                session.transcript.append({"role": "assistant", "content": message})
                session.state = session.state.model_copy(
                    update={
                        "assistant_message": message,
                        "stop_hook_active": False,
                        "transcript": self._trim(session.transcript),
                        "updated_at": _utc_now(),
                    }
                )
            pending_inputs = follow_up.current_inputs() if follow_up is not None else []
            model_needs_follow_up = bool(
                response.needs_follow_up or response.model_needs_follow_up
            )
            if pending_inputs or model_needs_follow_up:
                if model_needs_follow_up and follow_up is not None:
                    follow_up.record_model_follow_up()
                    session.state = session.state.model_copy(
                        update={"follow_up_count": follow_up.follow_up_count}
                    )
                session.state = session.state.model_copy(
                    update={
                        "status": "running",
                        "continuation": {
                            "source": "model_follow_up"
                            if model_needs_follow_up
                            else "pending_input",
                            "operation_id": f"model-follow-up:{context.run_id}:{session.state.turn_count}",
                        },
                    }
                )
                self._persist(context, session)
                self._emit(
                    context,
                    "main_agent_follow_up_requested",
                    turn=session.state.turn_count,
                    model_needs_follow_up=model_needs_follow_up,
                    pending_input_ids=[item.input_id for item in pending_inputs],
                )
                self._write_provider_checkpoint(
                    context,
                    status="handled",
                    turn_index=session.state.turn_count,
                    operation_id=operation_id,
                    transcript=session.transcript,
                )
                return TaskOutcome(
                    execution_mode=context.config.execution_mode,
                    status="running",
                    result=None,
                    continuation=session.state.continuation,
                )
            session.state, should_continue = self.main_agent._apply_stop_hook_candidate(
                message=message,
                state=session.state,
                root=root,
                hook_runtime=context.hook_runtime,
                follow_up_runtime=follow_up,
                turn_config=context.config,
                event_callback=context.event_callback,
                stop_target=("subagent_stop" if context.parent_turn_id else "stop"),
                parent_turn_id=context.parent_turn_id,
                agent_id=context.child_role_id,
                agent_type=context.child_role_id,
                agent_transcript_ref=(
                    str(root / "task_state.json") if context.parent_turn_id else None
                ),
            )
            self._persist(context, session)
            if should_continue:
                session.transcript = list(session.state.transcript)
                return TaskOutcome(
                    execution_mode=context.config.execution_mode,
                    status="running",
                    result=None,
                    continuation=session.state.continuation,
                )
            if session.state.status == "gated":
                return self._result(context, session, "gated", message)
            return self._complete_answer(context, session, message)

        current_action, next_action = self.main_agent._response_progress(
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
        self._emit_progress(
            context,
            phase="tool_dispatch",
            turn=turn_index,
            current_action=current_action,
            next_action=next_action,
        )
        self._emit(
            context,
            "main_agent_tool_calls_requested",
            turn=turn_index,
            mode=session.state.mode,
            tool_names=[item.tool_name for item in response.tool_calls],
            tool_call_ids=[item.call_id for item in response.tool_calls],
        )
        session.transcript.append(
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
            approval_id = requested.approval_id
            result = self._invoke_tool(
                context,
                session,
                requested.tool_name,
                requested.arguments,
                operation_id=requested.call_id,
                approval_id=approval_id,
            )
            approval_id = str(result.metadata.get("approval_id") or approval_id or "") or None
            self._record_tool_result(context, session, result)
            session.transcript.append(
                {
                    "role": "tool",
                    "tool_name": result.tool_name,
                    "call_id": result.call_id,
                    "status": result.status,
                    "output": self.main_agent._trim_tool_output(result.output),
                    "error": result.error,
                }
            )
            session.state = session.state.model_copy(
                update={
                    "tool_call_ids": [item.call_id for item in session.tool_results],
                    "pending_approval_ids": (
                        [str(result.metadata.get("approval_id"))]
                        if result.status == "waiting_approval"
                        and result.metadata.get("approval_id")
                        else [
                            item
                            for item in session.state.pending_approval_ids
                            if item != result.metadata.get("approval_id")
                        ]
                    ),
                    "transcript": self._trim(session.transcript),
                    "updated_at": _utc_now(),
                }
            )
            self._persist(context, session)
            self._emit(
                context,
                "main_agent_tool_loop_result",
                turn=turn_index,
                mode=session.state.mode,
                tool_name=result.tool_name,
                call_id=result.call_id,
                status=result.status,
                output_summary=self.main_agent._tool_output_summary(result),
            )
            if result.status == "rejected" and approval_id:
                approval = (
                    session.context.approval_store.get(approval_id)
                    if session.context.approval_store is not None
                    else None
                )
                if approval is not None and approval.status in {
                    "rejected",
                    "revoked",
                    "expired",
                }:
                    session.state = session.state.model_copy(
                        update={
                            "status": "gated",
                            "pending_approval_ids": [],
                            "continuation": {
                                "source": "tool_approval",
                                "approval_id": approval_id,
                                "tool_name": result.tool_name,
                                "decision": approval.status,
                            },
                            "error": result.error,
                            "updated_at": _utc_now(),
                        }
                    )
                    self._persist(context, session)
                    self._emit(
                        context,
                        "main_agent_tool_approval_rejected",
                        tool_name=result.tool_name,
                        approval_id=approval_id,
                        decision=approval.status,
                    )
                    return self._result(
                        context,
                        session,
                        "gated",
                        result.error or "Tool 审批已被拒绝。",
                    )
            if result.status == "waiting_approval":
                approval_id = str(result.metadata.get("approval_id") or "")
                session.state = session.state.model_copy(
                    update={
                        "status": "waiting_approval",
                        "continuation": {
                            "source": "tool_approval",
                            "approval_id": approval_id,
                            "tool_name": result.tool_name,
                            "operation_id": requested.call_id,
                        },
                        "pending_approval_ids": [approval_id] if approval_id else [],
                        "updated_at": _utc_now(),
                    }
                )
                self._persist(context, session)
                self._emit(
                    context,
                    "main_agent_waiting_approval",
                    tool_name=result.tool_name,
                    approval_id=approval_id,
                    operation_id=requested.call_id,
                )
                return self._result(context, session, "waiting_approval", result.error or "当前 Tool 调用需要审批。")
            self._emit_progress(
                context,
                phase="tool_result",
                turn=turn_index,
                current_action=(
                    f"Tool {result.tool_name} returned {result.status}"
                    + (
                        f": {self.main_agent._tool_output_summary(result)[:240]}"
                        if result.status == "completed"
                        else f"：{localize_runtime_error(result.error, fallback='Tool 执行失败。')}"
                    )
                ),
                next_action=f"将 Tool {result.tool_name} 的结果写入 Main Agent 下一轮",
                status=result.status,
            )
        if follow_up is not None:
            try:
                follow_up.record_model_follow_up()
                session.state = session.state.model_copy(
                    update={
                        "follow_up_count": follow_up.follow_up_count,
                        "updated_at": _utc_now(),
                    }
                )
                self._persist(context, session)
            except RuntimeError as exc:
                session.state = session.state.model_copy(
                    update={"status": "gated", "error": str(exc), "updated_at": _utc_now()}
                )
                self._persist(context, session)
                return self._result(context, session, "gated", "Agent OS follow-up 次数已达到上限。")
        self._write_provider_checkpoint(
            context,
            status="handled",
            turn_index=turn_index,
            operation_id=operation_id,
            transcript=session.transcript,
        )
        return TaskOutcome(
            execution_mode=context.config.execution_mode,
            status="running",
            result=None,
        )

    def _complete_answer(
        self,
        context: TurnContext,
        session: _CoreLoopSession,
        message: str,
        *,
        status: str = "completed",
    ) -> TaskOutcome:
        compose_allowed = (
            session.context.allowed_tools is None
            or "core.message.compose" in session.context.allowed_tools
        )
        if compose_allowed:
            composed = self._invoke_tool(
                context,
                session,
                "core.message.compose",
                {"message": message},
                operation_id=f"compose:{session.state.turn_count}",
            )
            self._record_tool_result(context, session, composed)
            if composed.status != "completed":
                return self._failed(context, session, RuntimeError(composed.error or "core.message.compose failed"))
            message = str(composed.output.get("message") or message)
        artifact = self.main_agent._assistant_artifact(
            request=context.request,
            decision=context.decision,
            run_id=context.run_id,
            thread_id=context.thread_id,
            message=message,
            tool_results=session.tool_results,
        )
        artifact = session.board.add(artifact)
        if not session.transcript or session.transcript[-1] != {"role": "assistant", "content": message}:
            session.transcript.append({"role": "assistant", "content": message})
        session.state = session.state.model_copy(
            update={
                "status": status,
                "assistant_message": message,
                "artifact_ids": [artifact.artifact_id],
                "tool_call_ids": [item.call_id for item in session.tool_results],
                "transcript": self._trim(session.transcript),
                "updated_at": _utc_now(),
            }
        )
        self._persist(context, session)
        self._write_provider_checkpoint(
            context,
            status="handled",
            turn_index=session.state.turn_count,
            operation_id=f"model:{context.run_id}:{session.state.turn_count}",
            transcript=session.transcript,
        )
        self._emit(
            context,
            "assistant_message_ready",
            mode=session.state.mode,
            delivery_status="completed" if status == "completed" else "completed_partial",
            message=message,
            artifact_ids=[artifact.artifact_id],
        )
        self._emit(context, "main_agent_completed", mode=session.state.mode, artifact_ids=[artifact.artifact_id])
        return self._result(context, session, status, message)

    def _result(self, context: TurnContext, session: _CoreLoopSession, status: str, message: str) -> TaskOutcome:
        result = MainAgentRunResult(
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            status=status,
            mode=session.state.mode,
            intent=context.decision.intent,
            message=message,
            clarification_question=session.state.clarification_question,
            missing_fields=list(session.state.missing_fields),
            artifact_ids=list(session.state.artifact_ids),
            continuation=session.state.continuation,
            pending_approval_ids=list(session.state.pending_approval_ids),
            tool_calls=list(session.tool_results),
            state=session.state,
        )
        return TaskOutcome(
            execution_mode=context.config.execution_mode,
            status=status,
            result=result,
            artifact_ids=list(result.artifact_ids),
            pending_approval_ids=list(result.pending_approval_ids),
            continuation=result.continuation,
            usage=self._usage(session),
        )

    @staticmethod
    def _usage(session: _CoreLoopSession) -> dict[str, Any]:
        return {
            "model_turns": int(session.state.turn_count),
            "tool_calls": len(session.tool_results),
        }

    @staticmethod
    def _role_approval_id(context: TurnContext) -> str:
        identity = (
            f"{context.run_id}:{context.parent_turn_id or context.turn.turn_id}:"
            f"{context.child_role_id or context.role_contract.role_id}"
        )
        return "approval_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    def _role_approval_outcome(
        self,
        context: TurnContext,
        session: _CoreLoopSession,
    ) -> TaskOutcome | None:
        approval_id = context.role_approval_id
        if not approval_id or session.context.approval_store is None:
            return None
        approval = session.context.approval_store.get(approval_id)
        if approval is None or approval.status == "pending":
            session.state = session.state.model_copy(
                update={
                    "status": "waiting_approval",
                    "pending_approval_ids": [approval_id],
                    "updated_at": _utc_now(),
                }
            )
            self._persist(context, session)
            self._emit(context, "child_role_waiting_approval", approval_id=approval_id)
            return self._result(context, session, "waiting_approval", "子角色正在等待人工审批。")
        if approval.status != "approved":
            session.state = session.state.model_copy(
                update={
                    "status": "gated",
                    "pending_approval_ids": [],
                    "error": f"子角色审批状态为 {approval.status}。",
                    "continuation": {
                        "source": "child_role_approval",
                        "approval_id": approval_id,
                        "decision": approval.status,
                    },
                    "updated_at": _utc_now(),
                }
            )
            self._persist(context, session)
            return self._result(context, session, "gated", session.state.error or "子角色审批已被拒绝。")
        if approval_id in session.state.pending_approval_ids:
            session.state = session.state.model_copy(
                update={
                    "pending_approval_ids": [],
                    "continuation": None,
                    "updated_at": _utc_now(),
                }
            )
            self._persist(context, session)
        return None

    def _control(self, context: TurnContext, session: _CoreLoopSession, status: str) -> TaskOutcome:
        result = self.main_agent._control_result(
            root=context.root,
            state=session.state,
            status=status,
            request=context.request,
            decision=context.decision,
            tool_results=session.tool_results,
            event_callback=context.event_callback,
        )
        session.state = result.state
        return TaskOutcome(
            execution_mode=context.config.execution_mode,
            status=status,
            result=result,
            artifact_ids=list(result.artifact_ids),
        )

    def _failed(self, context: TurnContext, session: _CoreLoopSession, exc: Exception) -> TaskOutcome:
        session.state = session.state.model_copy(
            update={
                "status": "failed",
                "error": f"{exc.__class__.__name__}: {exc}",
                "tool_call_ids": [item.call_id for item in session.tool_results],
                "updated_at": _utc_now(),
            }
        )
        self._persist(context, session)
        self._emit(context, "main_agent_failed", mode=session.state.mode, error=session.state.error)
        return self._result(
            context,
            session,
            "failed",
            "Agent OS Core Agent Loop 未能完成请求。",
        )

    def _timed_out(self, context: TurnContext, session: _CoreLoopSession) -> TaskOutcome:
        message = "Turn 在 Core 安全点超过了配置的超时时间。"
        session.state = session.state.model_copy(
            update={
                "status": "timed_out",
                "error": message,
                "updated_at": _utc_now(),
            }
        )
        self._persist(context, session)
        self._emit(context, "main_agent_timed_out", mode=session.state.mode, error=message)
        return self._result(context, session, "timed_out", message)

    def _maybe_compact(
        self,
        context: TurnContext,
        session: _CoreLoopSession,
    ) -> TaskOutcome | None:
        compact_config = context.config.compact_config
        if (
            compact_config is None
            or not compact_config.enabled
            or compact_config.context_threshold <= 0
            or context.follow_up_runtime is None
        ):
            return None
        keep = max(8, int(compact_config.context_threshold))
        if len(session.transcript) <= keep:
            return None
        from research_system.runtime.turn_runner import CompactExecutor

        compact_executor = CompactExecutor()
        compacted, removed, error = compact_executor.compact_transcript(
            context,
            transcript=session.transcript,
            message=session.state.assistant_message,
        )
        if error:
            if context.follow_up_runtime is not None:
                context.follow_up_runtime.state = context.follow_up_runtime.state.model_copy(
                    update={
                        "continuation": {
                            "source": "compact_hook",
                            "reason": error,
                        }
                    }
                )
                context.follow_up_runtime.persist()
            session.state = session.state.model_copy(
                update={
                    "status": "gated",
                    "error": error,
                    "continuation": {
                        "source": "compact_hook",
                        "reason": error,
                    },
                    "updated_at": _utc_now(),
                }
            )
            self._persist(context, session)
            return self._result(context, session, "gated", error)
        session.transcript = compacted
        session.state = session.state.model_copy(
            update={
                "status": "running",
                "transcript": self._trim(session.transcript),
                "continuation": {
                    "source": "compact",
                    "removed_messages": removed,
                    "retained_messages": len(compacted),
                },
                "updated_at": _utc_now(),
            }
        )
        self._persist(context, session)
        self._emit(
            context,
            "main_agent_context_compacted",
            removed_messages=removed,
            retained_messages=len(compacted),
        )
        return TaskOutcome(
            execution_mode=context.config.execution_mode,
            status="running",
            result=None,
            continuation=session.state.continuation,
        )

    def _persist(self, context: TurnContext, session: _CoreLoopSession) -> None:
        session.state = session.state.model_copy(
            update={"transcript": self._trim(session.transcript), "updated_at": _utc_now()}
        )
        self.main_agent._persist_state(context.root, session.state)
        if context.follow_up_runtime is not None:
            context.follow_up_runtime.update_transcript(
                session.state.transcript,
                message=session.state.assistant_message,
            )

    def _record_tool_result(
        self,
        context: TurnContext,
        session: _CoreLoopSession,
        result: CoreToolResult,
    ) -> None:
        session.tool_results.append(result)
        self._persist_tool_results(context, session)

    @staticmethod
    def _persist_tool_results(context: TurnContext, session: _CoreLoopSession) -> None:
        write_json(
            context.root / "core_tool_results.json",
            [item.model_dump(mode="json") for item in session.tool_results],
        )

    @staticmethod
    def _load_tool_results(root: Path) -> list[CoreToolResult]:
        payload = read_json(root / "core_tool_results.json")
        if not isinstance(payload, list):
            return []
        results: list[CoreToolResult] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                results.append(CoreToolResult.model_validate(item))
            except Exception:
                continue
        return results

    def _write_provider_checkpoint(
        self,
        context: TurnContext,
        *,
        status: str,
        turn_index: int,
        operation_id: str,
        transcript: list[dict[str, Any]],
        response: AgentTurnResponse | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "status": status,
            "turn_index": turn_index,
            "operation_id": operation_id,
            "transcript": self._trim(transcript),
            "updated_at": _utc_now().isoformat(),
        }
        if response is not None:
            payload["response"] = response.model_dump(mode="json")
        if error is not None:
            payload["error"] = error
        write_json(context.root / "provider_checkpoint.json", payload)

    def _invoke_tool(
        self,
        context: TurnContext,
        session: _CoreLoopSession,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        operation_id: str,
        approval_id: str | None = None,
    ):
        tool_arguments = dict(arguments)
        approval_id = approval_id or str(tool_arguments.pop("approval_id", "") or "") or None
        if approval_id is None:
            for prior in reversed(session.tool_results):
                if (
                    (
                        prior.call_id == operation_id
                        or prior.metadata.get("operation_id") == operation_id
                    )
                    and prior.tool_name == tool_name
                    and prior.status == "waiting_approval"
                ):
                    approval_id = str(prior.metadata.get("approval_id") or "") or None
                    break
        governed_context = replace(
            session.context,
            idempotency_key=f"{context.run_id}:{operation_id}:{tool_name}",
            approval_id=approval_id,
            authorize_approval=(
                lambda candidate: session.context.approval_store.validate(
                    approval_id=candidate,
                    run_id=context.run_id,
                    node_id=f"core_tool:{tool_name}",
                    capability=tool_name,
                    user_id=context.request.user_id,
                    tenant_id=context.request.tenant_id,
                )
                if session.context.approval_store is not None
                else False
            ),
        )
        result = self.main_agent.tools.invoke(
            tool_name=tool_name,
            arguments=tool_arguments,
            context=governed_context,
            node_id=f"core_tool:{tool_name}",
        )
        result = result.model_copy(
            update={
                "metadata": {
                    **dict(result.metadata),
                    **({"approval_id": approval_id} if approval_id else {}),
                    **({"operation_id": operation_id} if result.status == "waiting_approval" else {}),
                }
            }
        )
        return result

    def _emit(self, context: TurnContext, event_type: str, **payload: Any) -> None:
        self.main_agent._emit(
            context.root,
            context.event_callback,
            event_type,
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            task_type=context.config.task_type,
            execution_mode=context.config.execution_mode,
            **{
                key: value
                for key, value in payload.items()
                if key not in {"run_id", "thread_id", "request_id", "task_type", "execution_mode"}
            },
        )

    def _emit_progress(self, context: TurnContext, **payload: Any) -> None:
        self.main_agent._emit_loop_progress(
            context.root,
            context.event_callback,
            state=self._state_for_progress(context),
            **payload,
        )

    def _state_for_progress(self, context: TurnContext):
        payload = read_json(context.root / "main_agent_state.json")
        return MainAgentState.model_validate(payload)

    def _trim(self, transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.main_agent._trim_transcript(transcript)

    @staticmethod
    def _control_status(root: Path) -> str | None:
        control = read_json(root / "execution_control.json") or {}
        if control.get("cancel_requested"):
            return "cancelled"
        if control.get("pause_requested"):
            return "paused"
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["CoreLoopExecutor"]
