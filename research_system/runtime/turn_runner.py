from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from time import monotonic
from pathlib import Path
from threading import RLock, get_ident
from typing import Any, Callable, Protocol

from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.core.approvals import HumanApprovalStore
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.core.tool_registry import CoreToolContext, build_core_tool_registry
from research_system.runtime.event_store import append_canonical_event
from research_system.runtime.follow_up import FollowUpRuntime, mailbox_lease
from research_system.runtime.hooks import HookRuntime, LifecycleInput, StopHookInput
from research_system.runtime.roles import ChildTurnRuntime, RoleRegistry
from research_system.runtime.storage.jsonl import append_jsonl, read_json, read_jsonl, write_json
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.runtime.turn_context import TurnContext, TurnStepExecutor, TurnTask
from research_system.schemas.core_runtime import (
    MainAgentRunResult,
    MainAgentState,
    RequestIntakeDecision,
)
from research_system.schemas.artifacts import ArtifactDraft, ArtifactProducer, ArtifactRecord
from research_system.schemas.subagents import RoleContract
from research_system.schemas.events import EventEnvelope
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.execution_runtime import (
    ExecutionRunResult,
    ExecutionRunSnapshot,
    GraphStepOutcome,
)
from research_system.schemas.protocol import ProgressState, RequestEnvelope, ResponseEnvelope
from research_system.schemas.state_machine import RUN_TERMINAL_STATUSES, transition_status
from research_system.schemas.turn_runtime import (
    FollowUpConfig,
    GraphExecutorConfig,
    HookConfig,
    TaskSelection,
    TaskOutcome,
    TurnConfig,
    TurnExecutionMode,
    TurnTaskType,
    ChildTurnSnapshot,
    FollowupTaskRequest,
    FollowupTaskResult,
    TurnSnapshot,
)


# TurnContext and the Core provider/tool loop live in focused runtime modules.
# They remain re-exported here for the existing public import path.
from research_system.runtime.core.core_loop import CoreLoopExecutor


class GraphRunnerAdapter(Protocol):
    def step(self, context: TurnContext) -> GraphStepOutcome | ExecutionRunResult:
        ...


class GraphExecutor:
    """Optional Graph step executor mounted inside RegularExecutor."""

    def __init__(
        self,
        adapter: GraphRunnerAdapter,
        child_projector: Callable[[TurnContext, ExecutionRunResult], list[str]] | None = None,
    ) -> None:
        self.adapter = adapter
        self.child_projector = child_projector

    def execute(self, context: TurnContext) -> TaskOutcome:
        delivered_nodes = self._inject_running_child_followups(context)
        step = self.adapter.step(context)
        graph_step = step if isinstance(step, GraphStepOutcome) else None
        result = graph_step.result if graph_step is not None else step
        if delivered_nodes:
            self._clear_injected_followups(context, result, delivered_nodes)
        snapshot = result.snapshot
        child_turn_ids = (
            self.child_projector(context, result)
            if self.child_projector is not None
            else []
        )
        outcome = TaskOutcome(
            execution_mode="graph",
            status="running" if graph_step is not None and graph_step.should_continue else snapshot.status,
            result=result,
            graph_state_ref=str(context.root / "execution_snapshot.json"),
            artifact_ids=list(result.artifact_ids),
            final_artifact_ids=list(result.final_artifact_ids),
            pending_approval_ids=list(result.snapshot.pending_approval_ids),
            child_turn_ids=child_turn_ids,
        )
        return self._apply_subagent_stop_hooks(context, outcome)

    @staticmethod
    def _inject_running_child_followups(context: TurnContext) -> dict[str, list[dict[str, Any]]]:
        """Deliver running-child mailbox items at a Graph safe point."""
        runtime = context.follow_up_runtime
        if runtime is None:
            return {}
        payload = read_json(context.root / "execution_snapshot.json")
        if not isinstance(payload, dict):
            return {}
        snapshot = ExecutionRunSnapshot.model_validate(payload)
        child_by_id = {item.child_turn_id: item for item in context.turn.child_turns}
        nodes_by_id = {node.node_id: node for node in snapshot.graph.nodes}
        delivered: dict[str, list[dict[str, Any]]] = {}
        for child_id, child in child_by_id.items():
            if child.status != "running" or not child.parent_node_id:
                continue
            node = nodes_by_id.get(child.parent_node_id)
            if node is None:
                continue
            items = runtime.deliver_for_turn(child_id)
            if not items:
                continue
            rows = [
                {
                    "input_id": item.input_id,
                    "operation_id": item.operation_id,
                    "source": item.source,
                    "kind": item.kind,
                    "text": item.text,
                    "target_turn_id": child_id,
                }
                for item in items
            ]
            node.metadata = {
                **dict(node.metadata or {}),
                "pending_followups": [
                    *list((node.metadata or {}).get("pending_followups") or []),
                    *rows,
                ],
            }
            delivered[node.node_id] = rows
            RegularExecutor._emit_runtime_event(
                context,
                "graph_child_followup_injected",
                node_id=node.node_id,
                child_turn_id=child_id,
                input_ids=[item["input_id"] for item in rows],
            )
        if delivered:
            write_json(context.root / "execution_snapshot.json", snapshot.model_dump(mode="json"))
        return delivered

    @staticmethod
    def _clear_injected_followups(
        context: TurnContext,
        result: ExecutionRunResult,
        delivered_nodes: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Clear delivered metadata after GraphRunner persisted the safe point."""
        for node_id in delivered_nodes:
            node = next((item for item in result.snapshot.graph.nodes if item.node_id == node_id), None)
            if node is not None:
                node.metadata = {
                    key: value
                    for key, value in dict(node.metadata or {}).items()
                    if key != "pending_followups"
                }
        write_json(context.root / "execution_snapshot.json", result.snapshot.model_dump(mode="json"))

    @staticmethod
    def _child_transcript_ref(context: TurnContext, child: ChildTurnSnapshot) -> str:
        """Create the durable transcript boundary owned by one Graph ChildTurn."""
        child_root = ChildTurnRuntime(context.root).storage_root(child.child_turn_id)
        child_root.mkdir(parents=True, exist_ok=True)
        transcript_path = child_root / "task_state.json"
        if not transcript_path.exists():
            write_json(
                transcript_path,
                {
                    "task_type": "regular",
                    "execution_mode": "graph",
                    "transcript": [],
                    "graph_state_ref": str(context.root / "execution_snapshot.json"),
                },
            )
        return str(transcript_path)

    @staticmethod
    def _apply_subagent_stop_hooks(context: TurnContext, outcome: TaskOutcome) -> TaskOutcome:
        runtime = context.hook_runtime
        follow_up = context.follow_up_runtime
        snapshot = getattr(outcome.result, "snapshot", None)
        if runtime is None or follow_up is None or snapshot is None:
            return outcome
        if not context.config.hook_config or not context.config.hook_config.enabled:
            return outcome
        processed = set(follow_up.state.subagent_stop_node_ids)
        child_by_node = {
            item.parent_node_id: item
            for item in context.turn.child_turns
            if item.parent_node_id
        }
        for node_id, node_state in snapshot.node_states.items():
            if node_id in processed or node_state.status not in {
                "completed",
                "failed",
                "cancelled",
                "timed_out",
                "gated",
            }:
                continue
            node = next((item for item in snapshot.graph.nodes if item.node_id == node_id), None)
            child = child_by_node.get(node_id)
            if child is None:
                # SubagentStop is a ChildTurn lifecycle hook. A stale or
                # externally supplied Graph node must not be evaluated with
                # the Root Turn identity; Root completion is handled by Stop.
                continue
            hook_input = StopHookInput(
                session_id=context.thread_id,
                run_id=context.run_id,
                thread_id=child.child_thread_id,
                turn_id=child.child_turn_id,
                parent_turn_id=context.turn.turn_id,
                task_type=context.config.task_type,
                target="subagent_stop",
                agent_id=node_id,
                agent_type=str(getattr(node, "executor", None) or getattr(node, "capability", None) or node_id),
                agent_transcript_ref=GraphExecutor._child_transcript_ref(context, child),
                last_assistant_message=None,
            )
            decision = runtime.run_stop(
                hook_input,
                policy=context.config.hook_config.policy,
            )
            processed.add(node_id)
            follow_up.state = follow_up.state.model_copy(
                update={"subagent_stop_node_ids": sorted(processed)}
            )
            follow_up.persist()
            RegularExecutor._emit_runtime_event(
                context,
                "subagent_stop_evaluated",
                node_id=node_id,
                child_turn_id=hook_input.turn_id,
                should_block=decision.should_block,
                reason=decision.block_reason or decision.stop_reason,
            )
            if decision.should_block:
                reason = decision.block_reason or "SubagentStop requested continuation."
                continuation = {
                    "source": "subagent_stop",
                    "node_id": node_id,
                    "child_turn_id": hook_input.turn_id,
                    "reason": reason,
                    "resume_task_type": "review",
                    "fragments": [
                        item.model_dump(mode="json")
                        for item in decision.continuation_fragments
                    ],
                }
                if child is not None:
                    follow_up.enqueue(
                        source="hook",
                        kind="hook_prompt",
                        text=reason,
                        target_turn_id=child.child_turn_id,
                        operation_id=f"subagent-stop:{context.turn.turn_id}:{node_id}",
                    )
                return outcome.model_copy(
                    update={
                        "status": "gated",
                        "continuation": continuation,
                        "reason_codes": [*outcome.reason_codes, "subagent_stop_blocked"],
                    }
                )
        return outcome


class RegularExecutor:
    """The single RegularTask loop that selects an internal step executor."""

    def __init__(
        self,
        *,
        core_executor: TurnStepExecutor,
        graph_executor: TurnStepExecutor,
    ) -> None:
        self.core_executor = core_executor
        self.graph_executor = graph_executor

    def execute(self, context: TurnContext) -> TaskOutcome:
        if (
            context.deadline_at is not None
            and monotonic() >= context.deadline_at
            and context.last_result is not None
        ):
            return TaskOutcome(
                execution_mode=context.config.execution_mode,
                status="timed_out",
                result=context.last_result,
                error="Turn exceeded its configured timeout at a safe point.",
            )
        executor = (
            self.graph_executor
            if context.config.execution_mode == "graph"
            else self.core_executor
        )
        # The executor is a step provider.  RegularTask owns the only serial
        # loop and keeps the root Turn alive while a mode reports progress.
        while True:
            outcome = executor.execute(context)
            context.last_result = outcome.result
            if outcome.status != "running":
                break
            context.resume = True
        if context.config.execution_mode == "graph" and outcome.status in {
            "completed",
            "completed_partial",
            "gated",
        }:
            outcome = self._apply_graph_stop_hook(context, outcome)
        return outcome

    @staticmethod
    def _apply_graph_stop_hook(context: TurnContext, outcome: TaskOutcome) -> TaskOutcome:
        runtime = context.hook_runtime
        if runtime is None or not context.config.hook_config or not context.config.hook_config.enabled:
            return outcome
        result = outcome.result
        snapshot = getattr(result, "snapshot", None)
        message = None
        if snapshot is not None:
            message = str(getattr(snapshot, "clarification_question", None) or "") or None
        hook_input = StopHookInput(
            session_id=context.thread_id,
            run_id=context.run_id,
            thread_id=context.thread_id,
            turn_id=context.turn.turn_id,
            task_type=context.config.task_type,
            target="stop",
            stop_hook_active=bool(context.follow_up_runtime and context.follow_up_runtime.stop_hook_active),
            last_assistant_message=message,
        )
        hook_outcome = runtime.run_stop(
            hook_input,
            policy=context.config.hook_config.policy,
        )
        if not hook_outcome.should_block:
            return outcome
        reason = hook_outcome.block_reason or "Graph completion requires continuation."
        continuation = {
            "source": "stop_hook",
            "target": hook_input.target,
            "reason": reason,
            "resume_task_type": "review",
            "fragments": [item.model_dump(mode="json") for item in hook_outcome.continuation_fragments],
        }
        iteration = (context.follow_up_runtime.state.hook_iteration if context.follow_up_runtime else 0) + 1
        max_iterations = context.config.hook_config.policy.max_stop_continuations
        if iteration > max_iterations:
            continuation.pop("resume_task_type", None)
            RegularExecutor._emit_runtime_event(
                context,
                "stop_hook_limit_exceeded",
                hook_iteration=iteration,
                max_stop_continuations=max_iterations,
                reason=reason,
            )
            return outcome.model_copy(
                update={
                    "status": "gated",
                    "continuation": continuation,
                    "reason_codes": [*outcome.reason_codes, "stop_hook_limit_exceeded"],
                }
            )
        if context.follow_up_runtime is not None:
            context.follow_up_runtime.set_stop_hook_state(
                active=True,
                iteration=iteration,
                continuation=continuation,
            )
            context.follow_up_runtime.enqueue(
                source="hook",
                kind="hook_prompt",
                text=reason,
                operation_id=f"stop-hook:{context.turn.turn_id}:{iteration}",
            )
        RegularExecutor._emit_runtime_event(
            context,
            "stop_hook_blocked",
            reason=reason,
            continuation=continuation,
        )
        return outcome.model_copy(
            update={
                "status": "paused",
                "continuation": continuation,
                "reason_codes": [*outcome.reason_codes, "stop_hook_blocked"],
            }
        )

    @staticmethod
    def _emit_runtime_event(context: TurnContext, event_type: str, **payload: Any) -> None:
        """Persist an executor-owned event when no MainAgent emitter is involved."""
        root = context.root
        event = EventEnvelope(
            event_type=event_type,
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            user_id=context.request.user_id,
            tenant_id=context.request.tenant_id,
            turn_id=context.turn.turn_id,
            task_type=context.config.task_type,
            execution_mode=context.config.execution_mode,
            sequence=0,
            actor="regular_executor",
            payload=payload,
        ).model_dump(mode="json")
        event = append_canonical_event(root / "execution_events.jsonl", event)
        if context.event_callback is not None:
            context.event_callback(event)


class RegularTask:
    """Root Turn task; Graph is a configured executor, not a second Task."""

    task_type = "regular"

    def __init__(self, executor: RegularExecutor) -> None:
        self.executor = executor

    def start_or_resume(self, context: TurnContext) -> TaskOutcome:
        return self.executor.execute(context)

    def start(self, context: TurnContext) -> TaskOutcome:
        context.resume = False
        return self.executor.execute(context)

    def resume(self, context: TurnContext) -> TaskOutcome:
        context.resume = True
        return self.executor.execute(context)

    def cancel(self, context: TurnContext, reason: str) -> TaskOutcome:
        control = read_json(context.root / "execution_control.json") or {}
        write_json(
            context.root / "execution_control.json",
            {
                **control,
                "pause_requested": False,
                "cancel_requested": True,
                "cancel_reason": reason,
                "updated_at": _utc_now().isoformat(),
            },
        )
        context.resume = True
        return self.executor.execute(context)


class ReviewExecutor:
    """Review the current Turn's active artifacts in the same lifecycle."""

    def execute(self, context: TurnContext) -> TaskOutcome:
        board = ArtifactBoard(context.root / "artifact_board.jsonl")
        board.bind_thread(context.thread_id)
        requested_ids = {
            str(item).strip()
            for item in list((context.request.constraints or {}).get("review_artifact_ids") or [])
            if str(item).strip()
        }
        artifacts = board.list_artifacts(thread_id=None, run_id=context.run_id, status="active")
        if requested_ids:
            artifacts = [item for item in artifacts if item.artifact_id in requested_ids]
        findings: list[dict[str, Any]] = []
        for artifact in artifacts:
            if not artifact.content:
                findings.append({"artifact_id": artifact.artifact_id, "finding": "empty_content"})
            if not artifact.source_refs:
                findings.append({"artifact_id": artifact.artifact_id, "finding": "missing_source_refs"})
        verdict = "needs_review" if findings else "pass"
        status = "gated" if findings else "completed"
        reviewed_ids = [item.artifact_id for item in artifacts]
        review_artifact = ArtifactRecord(
            artifact_type="review_result",
            schema_version="1",
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            user_id=context.request.user_id,
            tenant_id=context.request.tenant_id,
            producer=ArtifactProducer(
                executor="turn_runner",
                capability="turn.review",
                responsibility_node_id="review_task",
            ),
            content={
                "verdict": verdict,
                "reviewed_artifact_ids": reviewed_ids,
                "findings": findings,
                "summary": f"Reviewed {len(reviewed_ids)} active artifact(s).",
            },
            source_refs=[{"type": "artifact", "id": item} for item in reviewed_ids],
            parent_artifact_ids=reviewed_ids,
            confidence=1.0 if not findings else 0.5,
            risk_flags=[item["finding"] for item in findings],
        )
        review_artifact = board.add(review_artifact)
        continuation = (
            {
                "source": "review",
                "reason": "Review findings require a Gate decision.",
                "resume_task_type": "review",
                "review_artifact_id": review_artifact.artifact_id,
                "findings": findings,
            }
            if findings
            else None
        )
        if continuation and context.follow_up_runtime is not None:
            context.follow_up_runtime.state = context.follow_up_runtime.state.model_copy(
                update={"continuation": continuation}
            )
            context.follow_up_runtime.persist()
        state = _special_state(context, status=status, artifact_ids=[review_artifact.artifact_id])
        write_json(context.root / "main_agent_state.json", state.model_dump(mode="json"))
        _emit_special_event(
            context,
            "review_task_completed" if not findings else "review_task_gated",
            verdict=verdict,
            artifact_ids=reviewed_ids,
            findings=findings,
        )
        result = MainAgentRunResult(
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            status=status,
            mode=context.config.execution_mode,
            intent=context.decision.intent,
            message=f"Reviewed {len(reviewed_ids)} artifact(s): {verdict}.",
            artifact_ids=[review_artifact.artifact_id],
            continuation=continuation,
            state=state,
        )
        if context.config.execution_mode == "graph":
            snapshot_payload = read_json(context.root / "execution_snapshot.json")
            if isinstance(snapshot_payload, dict):
                snapshot = ExecutionRunSnapshot.model_validate(snapshot_payload)
                graph_result = ExecutionRunResult(
                    snapshot=snapshot,
                    artifact_ids=[review_artifact.artifact_id],
                    final_artifact_ids=[] if findings else [review_artifact.artifact_id],
                )
                return TaskOutcome(
                    execution_mode=context.config.execution_mode,
                    status=status,
                    result=graph_result,
                    artifact_ids=[review_artifact.artifact_id],
                    final_artifact_ids=[] if findings else [review_artifact.artifact_id],
                    continuation=continuation,
                    reason_codes=["review_required"] if findings else [],
                )
        return TaskOutcome(
            execution_mode=context.config.execution_mode,
            status=status,
            result=result,
            artifact_ids=[review_artifact.artifact_id],
            final_artifact_ids=[] if findings else [review_artifact.artifact_id],
            continuation=continuation,
            reason_codes=["review_required"] if findings else [],
        )


class CompactExecutor:
    """Compact transcript and mailbox state at a persisted Turn safe point."""

    def compact_transcript(
        self,
        context: TurnContext,
        *,
        transcript: list[dict[str, Any]],
        message: str | None,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """Compact one context without ending the surrounding Task.

        The removed messages are archived separately so compaction changes the
        provider context only; it never deletes the audit trail.
        """
        task_runtime = context.follow_up_runtime
        if task_runtime is None:
            raise RuntimeError("context compaction requires FollowUpRuntime")
        hook_runtime = context.hook_runtime
        hook_enabled = bool(
            hook_runtime is not None
            and context.config.hook_config is not None
            and context.config.hook_config.enabled
        )
        compact_payload = {
            "run_id": context.run_id,
            "thread_id": context.thread_id,
            "turn_id": context.turn.turn_id,
            "task_type": context.config.task_type,
            "execution_mode": context.config.execution_mode,
            "transcript_ref": str(context.root / "task_state.json"),
        }
        if hook_enabled:
            allowed, rewritten = hook_runtime.run_event("PreCompact", compact_payload)
            if not allowed:
                return transcript, 0, str(
                    rewritten.get("_hook_block_reason")
                    or "PreCompact hook blocked context compaction."
                )
        threshold = int(
            context.config.compact_config.context_threshold
            if context.config.compact_config is not None
            else 0
        )
        keep = max(8, threshold or 24)
        compacted = list(transcript[-keep:])
        removed = max(0, len(transcript) - len(compacted))
        if removed:
            append_jsonl(
                context.root / "compact_archive.jsonl",
                {
                    "run_id": context.run_id,
                    "turn_id": context.turn.turn_id,
                    "created_at": _utc_now().isoformat(),
                    "removed_messages": list(transcript[:removed]),
                    "retained_messages": len(compacted),
                },
            )
        task_runtime.update_transcript(compacted, message=message)
        if hook_enabled:
            allowed, rewritten = hook_runtime.run_event(
                "PostCompact",
                {
                    **compact_payload,
                    "removed_messages": removed,
                    "retained_messages": len(compacted),
                },
            )
            if not allowed:
                return compacted, removed, str(
                    rewritten.get("_hook_block_reason")
                    or "PostCompact hook blocked context compaction."
                )
        return compacted, removed, None

    def execute(self, context: TurnContext) -> TaskOutcome:
        task_runtime = context.follow_up_runtime
        if task_runtime is None:
            raise RuntimeError("CompactTask requires FollowUpRuntime")
        state = task_runtime.state
        transcript = list(state.transcript)
        compacted, removed, error = self.compact_transcript(
            context,
            transcript=transcript,
            message=state.last_assistant_message,
        )
        if error:
            return self._blocked_outcome(context, task_runtime, error)
        continuation = {
            "source": "compact",
            "operation_id": f"compact:{context.turn.turn_id}:{task_runtime.state.follow_up_count}",
            "resume_task_type": "regular",
            "execution_mode": context.config.execution_mode,
            "removed_messages": removed,
            "retained_messages": len(compacted),
        }
        task_runtime.state = task_runtime.state.model_copy(
            update={
                "task_type": "regular",
                "continuation": continuation,
                "compact_before_continue": True,
            }
        )
        task_runtime.persist()
        # Compaction is a checkpoint, not the end of the user's Turn. The
        # next resume restores this state and TaskFactory switches back to the
        # original RegularTask within the same TurnSnapshot.
        main_state = _special_state(context, status="paused")
        main_state = main_state.model_copy(update={"transcript": compacted})
        write_json(context.root / "main_agent_state.json", main_state.model_dump(mode="json"))
        _emit_special_event(
            context,
            "compact_task_completed",
            removed_messages=removed,
            retained_messages=len(compacted),
            continuation=continuation,
        )
        result = MainAgentRunResult(
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            status=main_state.status,
            mode=context.config.execution_mode,
            intent=context.decision.intent,
            message=f"Compacted Turn context: retained {len(compacted)} message(s); the original task is ready to resume.",
            state=main_state,
            continuation=continuation,
        )
        return TaskOutcome(
            execution_mode=context.config.execution_mode,
            status=main_state.status,
            result=result,
            continuation=continuation,
        )

    @staticmethod
    def _blocked_outcome(
        context: TurnContext,
        task_runtime: FollowUpRuntime,
        reason: str,
    ) -> TaskOutcome:
        continuation = {"source": "compact_hook", "reason": reason}
        main_state = _special_state(context, status="gated")
        main_state = main_state.model_copy(
            update={"error": reason, "continuation": continuation}
        )
        task_runtime.state = task_runtime.state.model_copy(
            update={"continuation": continuation}
        )
        task_runtime.persist()
        write_json(context.root / "main_agent_state.json", main_state.model_dump(mode="json"))
        _emit_special_event(context, "compact_task_blocked", reason=reason)
        result = MainAgentRunResult(
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            status="gated",
            mode=context.config.execution_mode,
            intent=context.decision.intent,
            message=reason,
            continuation=main_state.continuation,
            state=main_state,
        )
        return TaskOutcome(
            execution_mode=context.config.execution_mode,
            status="gated",
            result=result,
            continuation=main_state.continuation,
            reason_codes=["compact_hook_blocked"],
            error=reason,
        )


class ReviewTask(RegularTask):
    """Review Task with a concrete artifact review step."""

    task_type = "review"

    def __init__(self, executor: RegularExecutor, review_executor: ReviewExecutor) -> None:
        super().__init__(executor)
        self.review_executor = review_executor

    def start_or_resume(self, context: TurnContext) -> TaskOutcome:
        return self._execute_guarded(context) or self.review_executor.execute(context)

    def start(self, context: TurnContext) -> TaskOutcome:
        return self._execute_guarded(context) or self.review_executor.execute(context)

    def resume(self, context: TurnContext) -> TaskOutcome:
        return self._execute_guarded(context) or self.review_executor.execute(context)

    def cancel(self, context: TurnContext, reason: str) -> TaskOutcome:
        return _special_control_outcome(context, "cancelled", reason)

    @staticmethod
    def _execute_guarded(context: TurnContext) -> TaskOutcome | None:
        return _special_task_guard(context)


class CompactTask(RegularTask):
    """Compact Task with a concrete transcript checkpoint step."""

    task_type = "compact"

    def __init__(self, executor: RegularExecutor, compact_executor: CompactExecutor) -> None:
        super().__init__(executor)
        self.compact_executor = compact_executor

    def start_or_resume(self, context: TurnContext) -> TaskOutcome:
        return self._execute_guarded(context) or self.compact_executor.execute(context)

    def start(self, context: TurnContext) -> TaskOutcome:
        return self._execute_guarded(context) or self.compact_executor.execute(context)

    def resume(self, context: TurnContext) -> TaskOutcome:
        return self._execute_guarded(context) or self.compact_executor.execute(context)

    def cancel(self, context: TurnContext, reason: str) -> TaskOutcome:
        return _special_control_outcome(context, "cancelled", reason)

    @staticmethod
    def _execute_guarded(context: TurnContext) -> TaskOutcome | None:
        return _special_task_guard(context)


def _special_state(
    context: TurnContext,
    *,
    status: str,
    artifact_ids: list[str] | None = None,
) -> MainAgentState:
    payload = read_json(context.root / "main_agent_state.json")
    if isinstance(payload, dict):
        state = MainAgentState.model_validate(payload)
        return state.model_copy(
            update={
                "status": status,
                "mode": context.config.execution_mode,
                "intent": context.decision.intent,
                "request_kind": context.decision.request_kind,
                "artifact_ids": list(artifact_ids or state.artifact_ids),
                "updated_at": _utc_now(),
            }
        )
    return MainAgentState(
        run_id=context.run_id,
        thread_id=context.thread_id,
        request_id=context.request.request_id,
        status=status,
        mode=context.config.execution_mode,
        intent=context.decision.intent,
        request_kind=context.decision.request_kind,
        artifact_ids=list(artifact_ids or []),
    )


def _special_task_guard(context: TurnContext) -> TaskOutcome | None:
    """Apply root control and deadline checks before atomic special Tasks."""
    if context.deadline_at is not None and monotonic() >= context.deadline_at:
        return _special_control_outcome(
            context,
            "timed_out",
            "Turn exceeded its configured timeout before the Task safe point.",
        )
    control = read_json(context.root / "execution_control.json") or {}
    if isinstance(control, dict) and control.get("cancel_requested"):
        return _special_control_outcome(context, "cancelled", "Turn cancellation requested.")
    if isinstance(control, dict) and control.get("pause_requested"):
        return _special_control_outcome(context, "paused", "Turn pause requested.")
    return None


def _special_control_outcome(
    context: TurnContext,
    status: str,
    reason: str,
) -> TaskOutcome:
    """Return a normalized result when a special Task reaches a control point."""
    state = _special_state(context, status=status)
    if status in {"cancelled", "timed_out", "failed"}:
        state = state.model_copy(update={"error": reason})
    write_json(context.root / "main_agent_state.json", state.model_dump(mode="json"))
    if context.config.execution_mode == "graph":
        payload = read_json(context.root / "execution_snapshot.json")
        if isinstance(payload, dict):
            graph_result = ExecutionRunResult(
                snapshot=ExecutionRunSnapshot.model_validate(payload)
            )
        else:
            graph_result = ExecutionRunResult(
                snapshot=ExecutionRunSnapshot(
                    run_id=context.run_id,
                    thread_id=context.thread_id,
                    request_id=context.request.request_id,
                    graph=context.graph or ExecutionGraph(
                        domain_id=context.decision.domain_id or "unknown",
                        objective=context.request.user_query,
                    ),
                    status=status,
                )
            )
        result: Any = graph_result
        pending_approval_ids = list(graph_result.snapshot.pending_approval_ids)
    else:
        result = MainAgentRunResult(
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            status=status,
            mode=context.config.execution_mode,
            intent=context.decision.intent,
            message=reason,
            continuation={"source": "turn_control", "reason": reason},
            state=state,
        )
        pending_approval_ids = list(state.pending_approval_ids)
    _emit_special_event(context, f"{status}_task_controlled", reason=reason)
    return TaskOutcome(
        execution_mode=context.config.execution_mode,
        status=status,
        result=result,
        pending_approval_ids=pending_approval_ids,
        error=reason if status in {"cancelled", "timed_out", "failed"} else None,
    )


def _emit_special_event(context: TurnContext, event_type: str, **payload: Any) -> None:
    event = EventEnvelope(
        event_type=event_type,
        run_id=context.run_id,
        thread_id=context.thread_id,
        request_id=context.request.request_id,
        user_id=context.request.user_id,
        tenant_id=context.request.tenant_id,
        turn_id=context.turn.turn_id,
        task_type=context.config.task_type,
        execution_mode=context.config.execution_mode,
        sequence=0,
        actor="regular_executor",
        payload=payload,
    ).model_dump(mode="json")
    event = append_canonical_event(context.root / "execution_events.jsonl", event)
    if context.event_callback is not None:
        context.event_callback(event)


class TaskFactory:
    """Select a real Task type and configure RegularExecutor execution."""

    @staticmethod
    def select(
        *,
        decision: RequestIntakeDecision,
        execution_mode: TurnExecutionMode,
        requested: TurnConfig | None = None,
        request: RequestEnvelope | None = None,
    ) -> TurnConfig:
        if requested is None and request is not None:
            raw_task_type = str((request.constraints or {}).get("task_type") or "").strip()
            if raw_task_type in {"review", "compact"}:
                requested = TurnConfig(
                    task_type=raw_task_type,  # type: ignore[arg-type]
                    execution_mode=execution_mode,
                    selection=TaskSelection(
                        task_type=raw_task_type,  # type: ignore[arg-type]
                        execution_mode=execution_mode,
                        reason_codes=[f"{raw_task_type}_task_requested"],
                        requires_persistence=True,
                    ),
                )
        if requested is not None:
            if requested.execution_mode != execution_mode:
                requested = requested.model_copy(update={"execution_mode": execution_mode})
            if execution_mode == "graph" and requested.task_type != "regular":
                # Graph is the RegularTask executor mounted inside the root
                # Turn. Review/compact continuations are selected from the
                # persisted TaskState, not encoded as a second Graph task.
                requested = requested.model_copy(update={"task_type": "regular"})
            if execution_mode == "graph" and requested.graph_config is None:
                requested = requested.model_copy(
                    update={"graph_config": GraphExecutorConfig(graph_id=decision.domain_id)}
                )
            if execution_mode == "graph" and decision.request_kind != "domain_task":
                raise ValueError("graph execution requires a domain task decision")
            return requested.model_copy(
                update={
                    "selection": requested.selection.model_copy(
                        update={
                            "task_type": requested.task_type,
                            "execution_mode": execution_mode,
                        }
                    )
                }
            )
        if execution_mode == "graph" and decision.request_kind != "domain_task":
            raise ValueError("graph execution requires a domain task decision")
        selection = TaskSelection(
            task_type="regular",
            execution_mode=execution_mode,
            reason_codes=[
                "graph_configured" if execution_mode == "graph" else
                "persistent_task_requested" if execution_mode == "task" else
                "regular_task"
            ],
            required_capabilities=list(decision.candidate_capabilities),
            graph_required=execution_mode == "graph",
            requires_persistence=execution_mode in {"task", "graph"},
            requires_approval=execution_mode == "graph",
        )
        return TurnConfig(
            task_type="regular",
            execution_mode=execution_mode,
            graph_config=(
                GraphExecutorConfig(graph_id=decision.domain_id)
                if execution_mode == "graph"
                else None
            ),
            hook_config=HookConfig(),
            follow_up_config=FollowUpConfig(),
            selection=selection,
        )

    @staticmethod
    def build(
        config: TurnConfig,
        regular: RegularTask,
        *,
        review_executor: ReviewExecutor,
        compact_executor: CompactExecutor,
        active_task_type: TurnTaskType | None = None,
    ) -> TurnTask:
        task_type = active_task_type or config.task_type
        if task_type == "review":
            return ReviewTask(regular.executor, review_executor)
        if task_type == "compact":
            return CompactTask(regular.executor, compact_executor)
        return regular

    @staticmethod
    def restore(
        *,
        turn: TurnSnapshot,
        task_snapshot: Any,
        regular: RegularTask,
        review_executor: ReviewExecutor | None = None,
        compact_executor: CompactExecutor | None = None,
    ) -> TurnTask:
        if str(getattr(task_snapshot, "execution_mode", turn.execution_mode)) != turn.execution_mode:
            raise ValueError("TaskSnapshot execution mode does not match Turn")
        return TaskFactory.build(
            turn.config,
            regular,
            review_executor=review_executor or ReviewExecutor(),
            compact_executor=compact_executor or CompactExecutor(),
            active_task_type=str(getattr(task_snapshot, "task_type", "") or "") or None,  # type: ignore[arg-type]
        )


class TurnRunner:
    """Own the root Turn lifecycle around Core and Graph execution."""

    def __init__(
        self,
        *,
        run_store: RunStore,
        main_agent: Any,
        graph_adapter: GraphRunnerAdapter,
        core_executor: TurnStepExecutor | None = None,
        hook_runtime: HookRuntime | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.run_store = run_store
        self.main_agent = main_agent
        self._execution_lock_guard = RLock()
        self._execution_locks: dict[str, RLock] = {}
        self._running_runs: set[str] = set()
        self.hook_runtime = hook_runtime or HookRuntime()
        self.timeout_seconds = timeout_seconds
        self.task_factory = TaskFactory()
        self.review_executor = ReviewExecutor()
        self.compact_executor = CompactExecutor()
        self.role_registry = RoleRegistry()
        self.regular_task = RegularTask(
            RegularExecutor(
                core_executor=core_executor or CoreLoopExecutor(main_agent),
                graph_executor=GraphExecutor(
                    graph_adapter,
                    child_projector=self._project_graph_children,
                ),
            )
        )

    def _project_graph_children(
        self,
        context: TurnContext,
        result: ExecutionRunResult,
    ) -> list[str]:
        runtime = ChildTurnRuntime(context.root)
        children = runtime.project_graph(
            parent_turn_id=context.turn.turn_id,
            parent_run_id=context.run_id,
            snapshot=result.snapshot,
        )
        for child in children:
            if not any(item.role_id == child.role_id for item in self.role_registry.list()):
                self.role_registry.register(
                    RoleContract.model_validate(
                        child.role_contract
                        or {
                            "role_id": child.role_id,
                            "role_version": "graph",
                            "display_name": child.role_id,
                            "objective": child.objective or child.role_id,
                            "source": "pack",
                        }
                    )
                )
            self._project_child_artifacts(
                parent_root=context.root,
                parent_turn=context.turn,
                child=child,
                artifact_ids=list(child.artifact_ids),
                parent_request=context.request,
            )
        self._normalize_root_artifact_scope(
            parent_root=context.root,
            parent_turn=context.turn,
            parent_request=context.request,
            artifact_ids=[*list(result.artifact_ids), *list(result.final_artifact_ids)],
        )
        context.turn = context.turn.model_copy(
            update={"child_turns": children, "updated_at": _utc_now()}
        )
        self._persist_turn(context.turn)
        self._emit(
            context,
            "child_turns_projected",
            child_turn_ids=[item.child_turn_id for item in children],
            node_ids=[item.parent_node_id for item in children if item.parent_node_id],
        )
        return [item.child_turn_id for item in children]

    def _project_child_artifacts(
        self,
        *,
        parent_root: Path,
        parent_turn: TurnSnapshot,
        child: ChildTurnSnapshot,
        artifact_ids: list[str],
        parent_request: RequestEnvelope,
    ) -> None:
        """Consume ChildTurn artifacts into the parent Board at a safe point.

        A child Core Turn owns a namespaced Board.  Returning only its IDs is
        insufficient: the parent response validator cannot prove ownership or
        lineage from the child filesystem.  Projection keeps the stable ID,
        rewrites identity scope to the parent request, and records the child
        provenance in metadata.
        """
        requested_ids = {str(item).strip() for item in artifact_ids if str(item).strip()}
        if not requested_ids:
            return
        parent_board = ArtifactBoard(parent_root / "artifact_board.jsonl")
        parent_board.bind_thread(parent_turn.thread_id)
        existing = {
            item.artifact_id: item
            for item in parent_board.list_artifacts(
                thread_id=None,
                run_id=parent_turn.run_id,
                include_invalidated=True,
            )
        }
        child_root = ChildTurnRuntime(parent_root).storage_root(child.child_turn_id)
        child_board = ArtifactBoard(child_root / "artifact_board.jsonl")
        child_records = {
            item.artifact_id: item
            for item in child_board.list_artifacts(
                thread_id=None,
                run_id=parent_turn.run_id,
                include_invalidated=True,
            )
        }

        ordered: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(artifact_id: str) -> None:
            if artifact_id in visited:
                return
            if artifact_id in visiting:
                raise ValueError(f"cyclic ChildTurn artifact lineage: {artifact_id}")
            visiting.add(artifact_id)
            record = child_records.get(artifact_id)
            if record is None:
                if artifact_id not in existing:
                    raise ValueError(
                        f"ChildTurn artifact is missing from both child and parent Board: "
                        f"{child.child_turn_id}->{artifact_id}"
                    )
            else:
                for parent_id in record.parent_artifact_ids:
                    if parent_id in child_records:
                        visit(parent_id)
                    elif parent_id not in existing:
                        raise ValueError(
                            f"ChildTurn artifact lineage is not available in the parent Run: "
                            f"{artifact_id}->{parent_id}"
                        )
            visiting.remove(artifact_id)
            visited.add(artifact_id)
            ordered.append(artifact_id)

        for artifact_id in sorted(requested_ids):
            visit(artifact_id)

        projections: list[ArtifactRecord] = []
        for artifact_id in ordered:
            source = child_records.get(artifact_id)
            if source is None:
                current = existing.get(artifact_id)
                if current is None:
                    continue
                if (
                    current.thread_id != parent_turn.thread_id
                    and current.run_id == parent_turn.run_id
                    and current.request_id == parent_turn.request_id
                ):
                    projections.append(
                        current.model_copy(
                            update={
                                "thread_id": parent_turn.thread_id,
                                "user_id": parent_request.user_id,
                                "tenant_id": parent_request.tenant_id,
                                "metadata": {
                                    **dict(current.metadata),
                                    "projected_from_graph_child": child.child_turn_id,
                                    "source_thread_id": current.thread_id,
                                },
                            }
                        )
                    )
                else:
                    self._validate_parent_artifact_scope(
                        current, parent_turn=parent_turn, parent_request=parent_request
                    )
                continue
            if source.run_id != parent_turn.run_id:
                raise ValueError(f"ChildTurn artifact run scope mismatch: {artifact_id}")
            if source.thread_id != child.child_thread_id:
                raise ValueError(f"ChildTurn artifact thread scope mismatch: {artifact_id}")
            if artifact_id in existing:
                current = existing[artifact_id]
                if (
                    current.thread_id != parent_turn.thread_id
                    and current.run_id == parent_turn.run_id
                    and current.request_id == parent_turn.request_id
                ):
                    projections.append(
                        current.model_copy(
                            update={
                                "thread_id": parent_turn.thread_id,
                                "user_id": parent_request.user_id,
                                "tenant_id": parent_request.tenant_id,
                                "metadata": {
                                    **dict(current.metadata),
                                    "projected_from_graph_child": child.child_turn_id,
                                    "source_thread_id": current.thread_id,
                                },
                            }
                        )
                    )
                else:
                    self._validate_parent_artifact_scope(
                        current, parent_turn=parent_turn, parent_request=parent_request
                    )
                continue
            source_refs: list[dict[str, Any]] = []
            for source_ref in source.source_refs:
                ref = dict(source_ref)
                source_type = str(ref.get("type") or "").strip()
                source_id = str(ref.get("id") or "").strip()
                if source_type == "request":
                    if source_id not in {"", source.request_id, parent_turn.request_id}:
                        raise ValueError(f"ChildTurn artifact request lineage mismatch: {artifact_id}")
                    ref["id"] = parent_turn.request_id
                elif source_type == "artifact" and source_id not in child_records and source_id not in existing:
                    raise ValueError(
                        f"ChildTurn artifact source is outside the parent Run: {artifact_id}->{source_id}"
                    )
                source_refs.append(ref)
            projections.append(
                source.model_copy(
                    update={
                        "thread_id": parent_turn.thread_id,
                        "request_id": parent_turn.request_id,
                        "user_id": parent_request.user_id,
                        "tenant_id": parent_request.tenant_id,
                        "source_refs": source_refs,
                        "metadata": {
                            **dict(source.metadata),
                            "projected_from_child_turn": child.child_turn_id,
                            "child_thread_id": child.child_thread_id,
                            "source_artifact_id": source.artifact_id,
                        },
                    }
                )
            )
        if projections:
            parent_board.add_many(projections)
        for artifact_id in requested_ids:
            projected = parent_board.get(artifact_id)
            if projected is None:
                raise ValueError(f"ChildTurn artifact projection failed: {artifact_id}")
            self._validate_parent_artifact_scope(
                projected, parent_turn=parent_turn, parent_request=parent_request
            )

    def _normalize_root_artifact_scope(
        self,
        *,
        parent_root: Path,
        parent_turn: TurnSnapshot,
        parent_request: RequestEnvelope,
        artifact_ids: list[str],
    ) -> None:
        """Project Graph-owned records onto the canonical Root thread."""
        board = ArtifactBoard(parent_root / "artifact_board.jsonl")
        board.bind_thread(parent_turn.thread_id)
        records = {
            item.artifact_id: item
            for item in board.list_artifacts(
                thread_id=None,
                run_id=parent_turn.run_id,
                include_invalidated=True,
            )
        }
        ordered: list[str] = []
        visited: set[str] = set()

        def visit(artifact_id: str) -> None:
            if artifact_id in visited:
                return
            record = records.get(artifact_id)
            if record is None:
                return
            visited.add(artifact_id)
            for parent_id in record.parent_artifact_ids:
                visit(parent_id)
            ordered.append(artifact_id)

        for artifact_id in artifact_ids:
            visit(str(artifact_id).strip())
        projections: list[ArtifactRecord] = []
        for artifact_id in ordered:
            record = records[artifact_id]
            if record.run_id != parent_turn.run_id or record.request_id != parent_turn.request_id:
                continue
            if record.thread_id == parent_turn.thread_id:
                continue
            projections.append(
                record.model_copy(
                    update={
                        "thread_id": parent_turn.thread_id,
                        "user_id": parent_request.user_id,
                        "tenant_id": parent_request.tenant_id,
                        "metadata": {
                            **dict(record.metadata),
                            "projected_to_root_thread": parent_turn.thread_id,
                            "source_thread_id": record.thread_id,
                        },
                    }
                )
            )
        if projections:
            board.add_many(projections)

    @staticmethod
    def _validate_parent_artifact_scope(
        artifact: ArtifactRecord,
        *,
        parent_turn: TurnSnapshot,
        parent_request: RequestEnvelope,
    ) -> None:
        if artifact.run_id != parent_turn.run_id:
            raise ValueError(f"artifact does not belong to the parent Run: {artifact.artifact_id}")
        if artifact.request_id != parent_turn.request_id:
            raise ValueError(f"artifact request scope does not match the parent Turn: {artifact.artifact_id}")
        if artifact.thread_id != parent_turn.thread_id:
            raise ValueError(f"artifact thread scope does not match the parent Turn: {artifact.artifact_id}")
        if artifact.user_id not in (None, "") and artifact.user_id != parent_request.user_id:
            raise ValueError(f"artifact user scope does not match the parent Turn: {artifact.artifact_id}")
        if artifact.tenant_id not in (None, "") and artifact.tenant_id != parent_request.tenant_id:
            raise ValueError(f"artifact tenant scope does not match the parent Turn: {artifact.artifact_id}")

    def register_hook(self, event: str, callback: Callable[[Any], Any], **kwargs: Any) -> None:
        """Register a process-level hook template cloned into each Turn."""
        self.hook_runtime.register(event, callback, **kwargs)

    def resolve_config(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        execution_mode: TurnExecutionMode | None = None,
    ) -> TurnConfig:
        raw = (request.constraints or {}).get("turn_config")
        requested = None
        if isinstance(raw, TurnConfig):
            requested = raw
        elif isinstance(raw, dict):
            requested = TurnConfig.model_validate(raw)
        mode = execution_mode or (
            requested.execution_mode if requested is not None else decision.recommended_mode
        )
        config = self.task_factory.select(
            decision=decision,
            execution_mode=mode,
            requested=requested,
            request=request,
        )
        if config.timeout_seconds is None and self.timeout_seconds is not None:
            config = config.model_copy(update={"timeout_seconds": self.timeout_seconds})
        return config

    def execute_task(self, **kwargs: Any) -> TaskOutcome:
        """Serialize root execution per Run within and across processes."""
        run_id = str(kwargs["run_id"])
        with self._lock_for_run(run_id), self._execution_lease(run_id):
            return self._execute_task(**kwargs)

    def _lock_for_run(self, run_id: str) -> RLock:
        with self._execution_lock_guard:
            return self._execution_locks.setdefault(run_id, RLock())

    def _execute_task(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        run_id: str,
        thread_id: str,
        execution_mode: TurnExecutionMode,
        turn_config: TurnConfig | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        resume: bool = False,
        recover: bool = False,
        graph: Any | None = None,
        seed_artifacts: list[ArtifactDraft] | None = None,
        rerun_from: str | None = None,
    ) -> TaskOutcome:
        run_root = self.run_store.load_run_paths(run_id).root
        persisted_turn = self._read_persisted_turn(run_root)
        # A persisted Turn is the source of truth during recovery. Resolving
        # current defaults first would make a config-file change look like a
        # mutation of an already-running Turn.
        config = (
            persisted_turn.config
            if persisted_turn is not None
            else turn_config
            or self.resolve_config(
                request=request,
                decision=decision,
                execution_mode=execution_mode,
            )
        )
        turn, created = self._load_or_create_turn(
            request=request,
            run_id=run_id,
            thread_id=thread_id,
            config=config,
        )
        turn = self._ensure_turn_deadline(turn, config)
        config = turn.config
        recovery_continuation = False
        if recover and turn.status in {"timed_out", "failed", "cancelled"}:
            turn = self._create_recovery_continuation(
                turn,
                request=request,
                config=config,
            )
            recovery_continuation = True
            resume = True
        is_graph_rerun = execution_mode == "graph" and bool(rerun_from)
        if (
            turn.status in RUN_TERMINAL_STATUSES
            and turn.status not in {"gated", "completed_partial"}
            and not is_graph_rerun
        ):
            raise ValueError(f"Turn is already terminal and cannot be resumed: {turn.status}")
        recovered_running = turn.status == "running" and resume
        if turn.status == "running" and not recovered_running:
            raise ValueError(f"Turn is already running: {run_id}")
        if recovered_running:
            # A persisted running state can outlive its process. Explicit
            # recovery reopens it through the legal paused -> running path;
            # the caller must coordinate any live worker lease before doing so.
            turn = turn.model_copy(
                update={
                    "status": "paused",
                    "error": "recovered_after_worker_restart",
                    "updated_at": _utc_now(),
                }
            )
            self._persist_turn(turn)

        context = TurnContext(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id=thread_id,
            turn=turn,
            config=config,
            run_store=self.run_store,
            main_agent=self.main_agent,
            event_callback=None,
            resume=resume,
            graph=graph,
            seed_artifacts=list(seed_artifacts or []),
            rerun_from=rerun_from,
            recovery_continuation=recovery_continuation,
            worker_restart_recovery=recovered_running,
        )
        context.event_callback = self._event_observer(context, event_callback)
        context.followup_task_handler = lambda request: self._followup_task_internal(
            context.run_id,
            request,
        )
        context.deadline_at = self._monotonic_deadline(turn.deadline_at)
        context.hook_runtime = self.hook_runtime.clone(
            policy=(config.hook_config.policy if config.hook_config else None),
            emitter=lambda event_type, payload: self._emit(
                context,
                event_type,
                task_type=config.task_type,
                execution_mode=config.execution_mode,
                **{
                    key: value
                    for key, value in payload.items()
                    if key not in {"run_id", "thread_id", "turn_id"}
                },
            ),
        )
        context.follow_up_runtime = FollowUpRuntime(
            root=context.root,
            run_id=run_id,
            turn_id=turn.turn_id,
            config=config.follow_up_config,
            emitter=lambda event_type, payload: self._emit(
                context,
                event_type,
                task_type=config.task_type,
                execution_mode=config.execution_mode,
                **{
                    key: value
                    for key, value in payload.items()
                    if key not in {"run_id", "thread_id", "turn_id"}
                },
            ),
        )
        self._prepare_resume_control(context)
        persisted_task = context.follow_up_runtime.state
        compact_continuation = (
            resume
            and str((persisted_task.continuation or {}).get("source") or "") == "compact"
            and persisted_task.task_type == "regular"
        )
        review_continuation = (
            resume
            and str((persisted_task.continuation or {}).get("resume_task_type") or "")
            == "review"
        )
        active_task_type: TurnTaskType = (
            "regular" if compact_continuation else config.task_type
        )
        if review_continuation and not compact_continuation:
            active_task_type = "review"
        if compact_continuation:
            context.follow_up_runtime.state = persisted_task.model_copy(
                update={"execution_mode": config.execution_mode}
            )
            context.follow_up_runtime.persist()
            self._emit(
                context,
                "compact_continuation_resumed",
                task_type=active_task_type,
                execution_mode=config.execution_mode,
            )
        else:
            context.follow_up_runtime.bind_execution(
                task_type=active_task_type,
                execution_mode=config.execution_mode,
            )
        if created:
            self._emit(context, "turn_received", task_type=config.task_type)
            self._emit(
                context,
                "turn_ready",
                task_type=config.task_type,
                execution_mode=config.execution_mode,
                reason_codes=list(config.selection.reason_codes),
            )
        if recovered_running:
            self._emit(
                context,
                "turn_recovered_after_worker_restart",
                task_type=config.task_type,
            )
        if resume and not recovered_running:
            self._emit(
                context,
                "turn_resumed",
                task_type=config.task_type,
                execution_mode=config.execution_mode,
            )
        if is_graph_rerun and context.turn.status in RUN_TERMINAL_STATUSES:
            # Partial rerun is an explicit Graph operation. Preserve the
            # original terminal checkpoint, then reopen this same Turn through
            # the state machine before GraphRunner resumes from the node.
            self._set_status(context, "paused")
        elif context.turn.status in {"gated", "completed_partial"}:
            self._set_status(context, "paused")
        if context.turn.status not in {"paused", "waiting_approval"}:
            self._set_status(context, "ready")
        self._emit(
            context,
            "task_selected",
            task_type=config.task_type,
            execution_mode=execution_mode,
            reason_codes=list(config.selection.reason_codes),
        )
        self._set_status(context, "running")
        self._emit(
            context,
            "turn_started",
            task_type=config.task_type,
            execution_mode=execution_mode,
        )
        context.hook_runtime.run_lifecycle(
            LifecycleInput(
                event="TurnStart",
                run_id=run_id,
                thread_id=thread_id,
                turn_id=turn.turn_id,
                task_type=config.task_type,
                status="running",
            )
        )
        self._running_runs.add(run_id)
        try:
            if created:
                context.hook_runtime.run_lifecycle(
                    LifecycleInput(
                        event="SessionStart",
                        run_id=run_id,
                        thread_id=thread_id,
                        turn_id=turn.turn_id,
                        task_type=config.task_type,
                        status="running",
                    )
                )

            outcome: TaskOutcome | None = None
            if not resume:
                allowed, rewritten = context.hook_runtime.run_event(
                    "UserPromptSubmit",
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "request_id": request.request_id,
                        "turn_id": turn.turn_id,
                        "user_query": context.request.user_query,
                        "request": context.request.model_dump(mode="json"),
                    },
                )
                if allowed:
                    if config.hook_config and config.hook_config.policy.allow_input_rewrite:
                        rewritten_query = str(rewritten.get("user_query") or "").strip()
                        if rewritten_query and rewritten_query != context.request.user_query:
                            context.request = context.request.model_copy(
                                update={"user_query": rewritten_query}
                            )
                            self._emit(
                                context,
                                "user_prompt_rewritten",
                                source="hook",
                                operation_id=f"user-prompt-rewrite:{run_id}",
                            )
                else:
                    reason = str(
                        rewritten.get("_hook_block_reason")
                        or "UserPromptSubmit hook blocked the request."
                    )
                    outcome = self._input_hook_blocked_outcome(context, reason)

            if outcome is None:
                task = self.task_factory.build(
                    config,
                    self.regular_task,
                    review_executor=self.review_executor,
                    compact_executor=self.compact_executor,
                    active_task_type=active_task_type,
                )
                outcome = task.resume(context) if resume else task.start(context)
            if context.deadline_at is not None and monotonic() >= context.deadline_at:
                outcome = outcome.model_copy(
                    update={
                        "status": "timed_out",
                        "error": "Turn exceeded its configured timeout.",
                    }
                )
            self._finish_turn(context, outcome)
            if outcome.status in {"cancelled", "timed_out"}:
                context.hook_runtime.run_lifecycle(
                    LifecycleInput(
                        event="TurnAbort",
                        run_id=run_id,
                        thread_id=thread_id,
                        turn_id=context.turn.turn_id,
                        task_type=config.task_type,
                        status=outcome.status,
                        reason=outcome.error,
                    )
                )
            else:
                context.hook_runtime.run_lifecycle(
                    LifecycleInput(
                        event="TurnStop",
                        run_id=run_id,
                        thread_id=thread_id,
                        turn_id=context.turn.turn_id,
                        task_type=config.task_type,
                        status=outcome.status,
                    )
                )
            self._sync_event_cursor(context)
            self._emit(
                context,
                self._completion_event(outcome.status),
                status=outcome.status,
                task_type=config.task_type,
                execution_mode=execution_mode,
            )
            return outcome
        except Exception as exc:
            error = f"{exc.__class__.__name__}: {exc}"
            # A post-completion observer, lifecycle hook, or response-side
            # persistence failure must not replace the original exception with
            # an illegal terminal -> failed transition.  Preserve the durable
            # terminal result (and its committed artifacts) while recording
            # the post-processing error for diagnostics.
            failure_status = (
                context.turn.status
                if context.turn.status in RUN_TERMINAL_STATUSES
                else self._transition_status(context.turn.status, "failed")
            )
            context.turn = context.turn.model_copy(
                update={
                    "status": failure_status,
                    "error": error,
                }
            )
            self._persist_turn(context.turn)
            context.hook_runtime.run_lifecycle(
                LifecycleInput(
                    event="TurnError",
                    run_id=run_id,
                    thread_id=thread_id,
                    turn_id=context.turn.turn_id,
                    task_type=config.task_type,
                    status="failed",
                    reason=error,
                )
            )
            self._emit(
                context,
                "turn_failed",
                error=error,
            )
            raise
        finally:
            self._running_runs.discard(run_id)

    def run(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        run_id: str,
        thread_id: str,
        execution_mode: TurnExecutionMode,
        turn_config: TurnConfig | None = None,
        response_factory: Callable[[Any], ResponseEnvelope],
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        resume: bool = False,
        recover: bool = False,
    ) -> ResponseEnvelope:
        if not resume:
            persisted = read_json(self.run_store.load_run_paths(run_id).root / "response.json")
            if isinstance(persisted, dict):
                persisted_response = ResponseEnvelope.model_validate(persisted)
                # An intake clarification is an intermediate response. A
                # follow-up on the same run must continue into the Turn.
                if persisted_response.status != "needs_clarification":
                    return persisted_response
        outcome = self.execute_task(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id=thread_id,
            execution_mode=execution_mode,
            turn_config=turn_config,
            event_callback=event_callback,
            resume=resume,
            recover=recover,
        )
        return self.finalize(outcome=outcome, response_factory=response_factory)

    def start_or_resume(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        run_id: str,
        thread_id: str,
        execution_mode: TurnExecutionMode,
        response_factory: Callable[[Any], ResponseEnvelope],
        operation: str = "start",
        turn_config: TurnConfig | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ResponseEnvelope:
        if operation not in {"start", "resume"}:
            raise ValueError(f"unsupported Turn operation: {operation}")
        return self.run(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id=thread_id,
            execution_mode=execution_mode,
            turn_config=turn_config,
            response_factory=response_factory,
            event_callback=event_callback,
            resume=operation == "resume",
            recover=operation == "resume",
        )

    def resume(
        self,
        run_id: str,
        *,
        request: RequestEnvelope | None = None,
        decision: RequestIntakeDecision | None = None,
        response_factory: Callable[[Any], ResponseEnvelope] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ResponseEnvelope:
        """Resume a persisted Turn without creating a new root Run."""
        root = self.run_store.load_run_paths(run_id).root
        request = request or RequestEnvelope.model_validate(read_json(root / "request.json") or {})
        turn = TurnSnapshot.model_validate(read_json(root / "turn_state.json") or {})
        request_payload = read_json(root / "agent_os_request.json") or {}
        decision_payload = request_payload.get("request_intake") if isinstance(request_payload, dict) else None
        decision = decision or (
            RequestIntakeDecision.model_validate(decision_payload)
            if isinstance(decision_payload, dict)
            else RequestIntakeDecision(
                status="accepted",
                request_kind="domain_task" if turn.execution_mode == "graph" else "conversation",
                recommended_mode=turn.execution_mode,
                intent=request.intent,
            )
        )
        if response_factory is None:
            response_factory = lambda result: ResponseEnvelope(
                request_id=request.request_id,
                run_id=run_id,
                thread_id=turn.thread_id,
                mode=turn.execution_mode,
                task_type=turn.task_type,
                status=(getattr(result, "status", "completed")),
                message=str(getattr(result, "message", None) or f"Turn {getattr(result, 'status', 'completed')}"),
                progress=ProgressState(
                    current_phase="turn",
                    active_agent="main_agent",
                    latest_event="turn_resumed",
                    status_message="Turn resumed",
                    percent=100 if getattr(result, "status", "") in {"completed", "failed", "cancelled", "gated"} else 0,
                ),
            )
        return self.run(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id=turn.thread_id,
            execution_mode=turn.execution_mode,
            turn_config=turn.config,
            response_factory=response_factory,
            event_callback=event_callback,
            resume=True,
            recover=True,
        )

    def finalize(
        self,
        *,
        outcome: TaskOutcome,
        response_factory: Callable[[Any], ResponseEnvelope],
    ) -> ResponseEnvelope:
        """Create and persist the one response owned by the root Turn."""
        response = response_factory(outcome.result)
        turn_id = None
        thread_id = None
        task_type = None
        payload: dict[str, Any] | None = None
        if response.run_id:
            payload = read_json(self.run_store.load_run_paths(response.run_id).turn_state)
            if isinstance(payload, dict):
                turn_id = str(payload.get("turn_id") or "") or None
                thread_id = str(payload.get("thread_id") or "") or None
                task_type = str(payload.get("task_type") or "") or None
        self._validate_response_artifacts(response=response, outcome=outcome, turn_payload=payload)
        persisted_status = str((payload or {}).get("status") or "") if isinstance(payload, dict) else ""
        child_turn_ids = [
            str(item.get("child_turn_id"))
            for item in list((payload or {}).get("child_turns") or [])
            if isinstance(item, dict) and item.get("child_turn_id")
        ] if isinstance(payload, dict) else []
        response_status = response.status
        if persisted_status in {"timed_out", "cancelled", "failed", "gated"}:
            response_status = persisted_status  # type: ignore[assignment]
        response = response.model_copy(
            update={
                "status": response_status,
                "turn_id": response.turn_id or turn_id,
                "thread_id": response.thread_id or thread_id,
                "task_type": response.task_type or task_type or "regular",
                "artifact_ids": list(response.artifact_ids or outcome.artifact_ids),
                "final_artifact_ids": list(
                    response.final_artifact_ids or outcome.final_artifact_ids
                ),
                "pending_approval_ids": list(
                    response.pending_approval_ids or outcome.pending_approval_ids
                ),
                "child_turn_ids": list(response.child_turn_ids or outcome.child_turn_ids or child_turn_ids),
                "continuation": response.continuation or outcome.continuation,
            }
        )
        if response.result is not None:
            response = response.model_copy(
                update={
                    "result": {
                        **response.result,
                        "child_turn_ids": list(response.child_turn_ids),
                    }
                }
            )
        self._persist_response(response)
        return response

    def _validate_response_artifacts(
        self,
        *,
        response: ResponseEnvelope,
        outcome: TaskOutcome,
        turn_payload: dict[str, Any] | None,
    ) -> None:
        """Validate response references at the only root response boundary."""
        run_id = str(response.run_id or "").strip()
        if not run_id:
            return
        if not isinstance(turn_payload, dict):
            raise ValueError(f"Turn checkpoint is missing before response finalization: {run_id}")
        turn = TurnSnapshot.model_validate(turn_payload)
        if response.request_id != turn.request_id:
            raise ValueError("response request_id does not match the Root Turn")
        if response.thread_id and response.thread_id != turn.thread_id:
            raise ValueError("response thread_id does not match the Root Turn")
        root = self.run_store.load_run_paths(run_id).root
        request_payload = read_json(root / "request.json") or {}
        if not isinstance(request_payload, dict):
            request_payload = {}
        request = request_payload.get("request") if isinstance(request_payload.get("request"), dict) else request_payload
        expected_user_id = request.get("user_id")
        expected_tenant_id = request.get("tenant_id")
        board = ArtifactBoard(root / "artifact_board.jsonl")
        records = board.list_artifacts(
            thread_id=None,
            run_id=run_id,
            include_invalidated=True,
        )
        by_id = {record.artifact_id: record for record in records}

        references: set[str] = set()

        def collect(payload: Any) -> None:
            if not isinstance(payload, dict):
                return
            for key in ("artifact_ids", "final_artifact_ids", "source_artifact_ids"):
                values = payload.get(key)
                if isinstance(values, str):
                    references.add(values)
                elif isinstance(values, list):
                    references.update(
                        str(value).strip()
                        for value in values
                        if str(value).strip()
                    )

        collect(response.model_dump(mode="json"))
        collect(outcome.model_dump(mode="json"))
        collect(response.result)
        for child in turn.child_turns:
            references.update(
                str(artifact_id).strip()
                for artifact_id in child.artifact_ids
                if str(artifact_id).strip()
            )
        for item in response.artifacts:
            if isinstance(item, dict) and item.get("artifact_id"):
                references.add(str(item["artifact_id"]))

        for artifact_id in sorted(references):
            record = by_id.get(artifact_id)
            if record is None:
                raise ValueError(
                    f"response references an artifact outside the current Run: {artifact_id}"
                )
            if record.run_id != run_id or record.request_id != turn.request_id:
                raise ValueError(f"artifact does not belong to the current Root Turn: {artifact_id}")
            if record.thread_id != turn.thread_id:
                raise ValueError(f"artifact does not belong to the current Root thread: {artifact_id}")
            if record.user_id not in (None, "") and record.user_id != expected_user_id:
                raise ValueError(f"artifact user scope does not match the request: {artifact_id}")
            if record.tenant_id not in (None, "") and record.tenant_id != expected_tenant_id:
                raise ValueError(f"artifact tenant scope does not match the request: {artifact_id}")
            for parent_id in record.parent_artifact_ids:
                parent = by_id.get(parent_id)
                if parent is None or parent.run_id != run_id or parent.thread_id != turn.thread_id:
                    raise ValueError(
                        f"artifact lineage escapes the current Root Turn: {artifact_id}->{parent_id}"
                    )
            for source_ref in record.source_refs:
                source_type = str(source_ref.get("type") or "").strip()
                source_id = str(source_ref.get("id") or "").strip()
                if source_type == "artifact" and source_id not in by_id:
                    raise ValueError(
                        f"artifact source reference is missing from the current Run: {artifact_id}->{source_id}"
                    )
                if source_type == "request" and source_id and source_id != turn.request_id:
                    raise ValueError(
                        f"artifact source request does not match the Root Turn: {artifact_id}"
                    )

    def _load_or_create_turn(
        self,
        *,
        request: RequestEnvelope,
        run_id: str,
        thread_id: str,
        config: TurnConfig,
    ) -> tuple[TurnSnapshot, bool]:
        root = self.run_store.load_run_paths(run_id).root
        config_hash = self._config_hash(config)
        turn = self._read_persisted_turn(root)
        if turn is not None:
            if turn.request_id != request.request_id:
                raise ValueError(f"Turn already belongs to another request: {run_id}")
            stored_request = read_json(root / "request.json")
            if isinstance(stored_request, dict):
                for field in ("user_id", "tenant_id"):
                    stored_value = stored_request.get(field)
                    current_value = getattr(request, field)
                    if stored_value not in (None, "") and current_value != stored_value:
                        raise PermissionError(f"Turn {field} scope does not match: {run_id}")
            if turn.config_hash and turn.config_hash != config_hash:
                raise ValueError(f"Turn configuration changed during recovery: {run_id}")
            if turn.execution_mode != config.execution_mode:
                raise ValueError(f"Turn execution mode changed during recovery: {run_id}")
            turn = turn.model_copy(
                update={
                    "thread_id": turn.thread_id or thread_id,
                    "user_id": turn.user_id or request.user_id,
                    "tenant_id": turn.tenant_id or request.tenant_id,
                    "config_hash": turn.config_hash or config_hash,
                    "updated_at": _utc_now(),
                }
            )
            self._persist_turn(turn)
            return turn, False
        existing_events = read_jsonl(root / "execution_events.jsonl")
        initial_sequence = max(
            (int(event.get("sequence") or 0) for event in existing_events),
            default=0,
        )
        latest_event = max(
            existing_events,
            key=lambda event: int(event.get("sequence") or 0),
            default={},
        )
        turn = TurnSnapshot(
            turn_id=generate_ulid(),
            run_id=run_id,
            thread_id=thread_id,
            request_id=request.request_id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            task_type=config.task_type,
            execution_mode=config.execution_mode,
            config=config,
            config_hash=config_hash,
            task_selection=config.selection,
            sequence=initial_sequence,
            last_event_id=str(latest_event.get("event_id") or "").strip() or None,
        )
        self._persist_turn(turn)
        return turn, True

    @staticmethod
    def _read_persisted_turn(root: Path) -> TurnSnapshot | None:
        payload = read_json(root / "turn_state.json")
        return TurnSnapshot.model_validate(payload) if isinstance(payload, dict) else None

    def _create_recovery_continuation(
        self,
        turn: TurnSnapshot,
        *,
        request: RequestEnvelope,
        config: TurnConfig,
    ) -> TurnSnapshot:
        """Create a new logical Turn under the same Run for terminal recovery.

        Terminal Turns are immutable from a lifecycle perspective.  The
        latest ``turn_state.json`` becomes the active continuation, while the
        original terminal snapshot remains in ``turn_history.jsonl`` and in
        the canonical event stream.  This preserves one Run boundary without
        pretending that ``timed_out -> running`` was a legal transition.
        """
        if turn.status not in {"timed_out", "failed", "cancelled"}:
            raise ValueError(f"Turn is not recoverable: {turn.status}")
        root = self.run_store.load_run_paths(turn.run_id).root
        operation_id = f"recovery:{turn.turn_id}"
        history = read_jsonl(root / "turn_history.jsonl")
        prior = next(
            (
                item
                for item in history
                if isinstance(item, dict) and item.get("operation_id") == operation_id
            ),
            None,
        )
        continuation_turn_id = str(
            (prior or {}).get("continuation_turn_id") or generate_ulid()
        )
        continuation = {
            "source": "recovery",
            "operation_id": operation_id,
            "parent_turn_id": turn.turn_id,
            "parent_status": turn.status,
            "continuation_turn_id": continuation_turn_id,
        }
        if prior is None:
            append_jsonl(
                root / "turn_history.jsonl",
                {
                    "kind": "terminal_turn_snapshot",
                    "operation_id": operation_id,
                    "continuation_turn_id": continuation_turn_id,
                    "turn": turn.model_dump(mode="json"),
                    "created_at": _utc_now().isoformat(),
                },
            )
        timeout = config.timeout_seconds or self.timeout_seconds
        deadline_at = (
            _utc_now() + timedelta(seconds=float(timeout)) if timeout is not None else None
        )
        new_turn = TurnSnapshot(
            turn_id=continuation_turn_id,
            run_id=turn.run_id,
            thread_id=turn.thread_id,
            request_id=request.request_id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            parent_turn_id=turn.turn_id,
            relation="continuation",
            task_type=turn.task_type,
            execution_mode=turn.execution_mode,
            status="paused",
            config=config,
            config_hash=turn.config_hash or self._config_hash(config),
            task_selection=turn.task_selection,
            child_turns=list(turn.child_turns),
            usage=dict(turn.usage),
            sequence=turn.sequence,
            task_state_ref=turn.task_state_ref,
            graph_state_ref=turn.graph_state_ref,
            error=None,
            deadline_at=deadline_at,
        )
        self._persist_turn(new_turn)
        task_payload = read_json(root / "task_state.json")
        if isinstance(task_payload, dict):
            write_json(
                root / "task_state.json",
                {**task_payload, "continuation": continuation, "updated_at": _utc_now().isoformat()},
            )
        new_turn = self._append_snapshot_event(
            new_turn,
            "turn_recovery_continuation_created",
            operation_id=operation_id,
            parent_turn_id=turn.turn_id,
            parent_status=turn.status,
            continuation_turn_id=continuation_turn_id,
        )
        return new_turn

    def _finish_turn(self, context: TurnContext, outcome: TaskOutcome) -> None:
        target = self._normalize_status(outcome.status)
        context.turn = context.turn.model_copy(
            update={
                "status": self._transition_status(context.turn.status, target),
                "graph_state_ref": outcome.graph_state_ref,
                "task_state_ref": str(context.root / "task_state.json"),
                "task_selection": context.turn.task_selection,
                "updated_at": _utc_now(),
                "error": outcome.error,
            }
        )
        self._persist_turn(context.turn)

    def _ensure_turn_deadline(self, turn: TurnSnapshot, config: TurnConfig) -> TurnSnapshot:
        if turn.deadline_at is not None:
            return turn
        timeout = config.timeout_seconds or self.timeout_seconds
        if timeout is None:
            return turn
        updated = turn.model_copy(
            update={
                "deadline_at": _utc_now() + timedelta(seconds=float(timeout)),
                "updated_at": _utc_now(),
            }
        )
        self._persist_turn(updated)
        return updated

    @staticmethod
    def _monotonic_deadline(deadline_at: datetime | None) -> float | None:
        if deadline_at is None:
            return None
        remaining = (deadline_at - _utc_now()).total_seconds()
        return monotonic() + max(0.0, remaining)

    @staticmethod
    def _transition_status(current: str, target: str) -> str:
        if current == target:
            return target
        return transition_status(scope="run", current=current, target=target)

    def _set_status(self, context: TurnContext, target: str) -> None:
        current = context.turn.status
        if current == target:
            return
        new_status = self._transition_status(current, target)
        context.turn = context.turn.model_copy(
            update={"status": new_status, "updated_at": _utc_now()}
        )
        self._persist_turn(context.turn)

    @staticmethod
    def _normalize_status(status: str) -> str:
        return "paused" if status == "running" else status

    @staticmethod
    def _completion_event(status: str) -> str:
        return {
            "completed": "turn_completed",
            "completed_partial": "turn_completed_partial",
            "gated": "turn_gated",
            "paused": "turn_paused",
            "waiting_approval": "turn_waiting_approval",
            "cancelled": "turn_cancelled",
            "timed_out": "turn_timed_out",
        }.get(status, "turn_finished")

    def _persist_turn(self, turn: TurnSnapshot) -> None:
        root = self.run_store.load_run_paths(turn.run_id).root
        payload = turn.model_dump(mode="json")
        write_json(root / "turn_state.json", payload)

    @contextmanager
    def _mailbox_lease(self, run_id: str):
        """Serialize mailbox writers without blocking the active Turn worker."""
        root = self.run_store.load_run_paths(run_id).root
        with mailbox_lease(root, run_id):
            yield

    @contextmanager
    def _execution_lease(self, run_id: str):
        """Claim a filesystem lease so another worker cannot advance the Run."""
        root = self.run_store.load_run_paths(run_id).root
        lease_path = root / "turn_worker_lease.json"
        now = datetime.now(timezone.utc)
        existing = read_json(lease_path)
        if lease_path.exists():
            if not isinstance(existing, dict):
                raise ValueError(f"Turn execution lease is unreadable: {run_id}")
            expires_at = self._parse_lease_time(existing.get("expires_at"))
            if expires_at is not None and expires_at > now:
                raise ValueError(f"Turn is already being executed by another worker: {run_id}")
            lease_path.unlink(missing_ok=True)

        owner = f"{os.getpid()}:{get_ident()}:{generate_ulid()}"
        expires_at = now + timedelta(seconds=self._lease_seconds())
        payload = {
            "owner": owner,
            "run_id": run_id,
            "claimed_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        try:
            descriptor = os.open(
                lease_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as exc:
            raise ValueError(f"Turn is already being executed by another worker: {run_id}") from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, indent=2)
                handle.write("\n")
        except Exception:
            lease_path.unlink(missing_ok=True)
            raise
        try:
            yield
        finally:
            persisted = read_json(lease_path)
            if isinstance(persisted, dict) and persisted.get("owner") == owner:
                lease_path.unlink(missing_ok=True)

    def _lease_seconds(self) -> int:
        timeout = float(self.timeout_seconds or 300.0)
        return max(120, int(timeout * 2) + 60)

    @staticmethod
    def _parse_lease_time(value: object) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _input_hook_blocked_outcome(self, context: TurnContext, reason: str) -> TaskOutcome:
        continuation = {
            "source": "user_prompt_submit_hook",
            "operation_id": f"user-prompt-submit:{context.run_id}",
            "reason": reason,
        }
        if context.follow_up_runtime is not None:
            context.follow_up_runtime.state = context.follow_up_runtime.state.model_copy(
                update={"continuation": continuation}
            )
            context.follow_up_runtime.persist()
        self._emit(context, "user_prompt_submit_blocked", reason=reason)

        if context.config.execution_mode == "graph":
            graph = context.graph or ExecutionGraph(
                domain_id=context.decision.domain_id or "unknown",
                objective=context.request.user_query,
            )
            result: Any = ExecutionRunResult(
                snapshot=ExecutionRunSnapshot(
                    run_id=context.run_id,
                    thread_id=context.thread_id,
                    request_id=context.request.request_id,
                    graph=graph,
                    status="gated",
                )
            )
            write_json(
                context.root / "execution_snapshot.json",
                result.snapshot.model_dump(mode="json"),
            )
        else:
            state_payload = read_json(context.root / "main_agent_state.json")
            state = (
                MainAgentState.model_validate(state_payload)
                if isinstance(state_payload, dict)
                else MainAgentState(
                    run_id=context.run_id,
                    thread_id=context.thread_id,
                    request_id=context.request.request_id,
                    mode=context.config.execution_mode,
                    intent=context.decision.intent,
                )
            )
            state = state.model_copy(
                update={
                    "status": "gated",
                    "error": reason,
                    "continuation": continuation,
                    "updated_at": _utc_now(),
                }
            )
            persist_state = getattr(self.main_agent, "_persist_state", None)
            if callable(persist_state):
                persist_state(context.root, state)
            else:
                write_json(context.root / "main_agent_state.json", state.model_dump(mode="json"))
            result = MainAgentRunResult(
                run_id=context.run_id,
                thread_id=context.thread_id,
                request_id=context.request.request_id,
                status="gated",
                mode=context.config.execution_mode,
                intent=context.decision.intent,
                message=reason,
                continuation=continuation,
                state=state,
            )
        return TaskOutcome(
            execution_mode=context.config.execution_mode,
            status="gated",
            result=result,
            continuation=continuation,
            reason_codes=["user_prompt_submit_blocked"],
            error=reason,
        )

    def _sync_event_cursor(self, context: TurnContext) -> None:
        root = self.run_store.load_run_paths(context.run_id).root
        events = read_jsonl(root / "execution_events.jsonl")
        if not events:
            return
        latest = max(events, key=lambda event: int(event.get("sequence") or 0))
        latest_sequence = int(latest.get("sequence") or 0)
        if latest_sequence <= context.turn.sequence:
            return
        context.turn = context.turn.model_copy(
            update={
                "sequence": latest_sequence,
                "last_event_id": str(latest.get("event_id") or "").strip() or None,
                "updated_at": _utc_now(),
            }
        )
        self._persist_turn(context.turn)

    @staticmethod
    def _config_hash(config: TurnConfig) -> str:
        payload = json.dumps(
            config.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _persist_response(self, response: ResponseEnvelope) -> None:
        if response.run_id:
            root = self.run_store.load_run_paths(response.run_id).root
            write_json(root / "response.json", response.model_dump(mode="json"))

    def _prepare_resume_control(self, context: TurnContext) -> None:
        """Consume a prior pause request while preserving a pending cancel.

        A pause request is a safe-point instruction. Once a caller explicitly
        resumes a paused Turn, that instruction has been consumed. A cancel
        request remains authoritative until a Task reaches the cancelled
        state, including when it was issued while the Turn was paused.
        """
        if not context.resume:
            return
        control_path = context.root / "execution_control.json"
        control = read_json(control_path) or {}
        if not isinstance(control, dict):
            return
        if not control.get("pause_requested") or control.get("cancel_requested"):
            return
        write_json(
            control_path,
            {
                **control,
                "pause_requested": False,
                "status": "consumed",
                "consumed_action": "pause",
                "consumed_at": _utc_now().isoformat(),
                "updated_at": _utc_now().isoformat(),
            },
        )
        self._emit(
            context,
            "turn_control_consumed",
            actor="turn_runner.resume",
            action="pause",
            status="consumed",
        )

    def pause(self, run_id: str, *, reason: str = "pause requested") -> TurnSnapshot:
        return self._request_control(run_id, "pause", reason)

    def cancel(self, run_id: str, *, reason: str = "cancel requested") -> TurnSnapshot:
        return self._request_control(run_id, "cancel", reason)

    def enqueue_follow_up(
        self,
        run_id: str,
        *,
        text: str,
        source: str = "user",
        operation_id: str | None = None,
        delivery_phase: str = "current_turn",
    ):
        """Queue external input with the same mailbox audit as model input."""
        return self._enqueue_follow_up(
            run_id,
            text=text,
            source=source,
            operation_id=operation_id,
            delivery_phase=delivery_phase,
        )

    def _enqueue_follow_up(
        self,
        run_id: str,
        *,
        text: str,
        source: str,
        operation_id: str | None,
        delivery_phase: str,
    ):
        """Write one mailbox item through FollowUpRuntime's shared lease."""
        root = self.run_store.load_run_paths(run_id).root
        payload = read_json(root / "turn_state.json")
        if not isinstance(payload, dict):
            raise ValueError(f"Turn state is missing: {run_id}")
        turn = TurnSnapshot.model_validate(payload)
        event_turn = turn

        def emit_followup(event_type: str, event_payload: dict[str, Any]) -> None:
            nonlocal event_turn
            event_turn = self._append_snapshot_event(
                event_turn,
                event_type,
                actor="turn_runner.api",
                **{
                    key: value
                    for key, value in event_payload.items()
                    if key not in {"run_id", "thread_id", "turn_id"}
                },
            )

        runtime = FollowUpRuntime(
            root=root,
            run_id=run_id,
            turn_id=turn.turn_id,
            config=turn.config.follow_up_config,
            emitter=emit_followup,
        )
        return runtime.enqueue(
            source=source,
            kind="user_input" if source == "user" else "inter_agent_communication",
            text=text,
            operation_id=operation_id,
            delivery_phase=delivery_phase,
        )

    def followup_task(
        self,
        run_id: str,
        request: FollowupTaskRequest,
    ) -> FollowupTaskResult:
        """Submit a ChildTurn follow-up through the governed Core Tool path."""
        with self._lock_for_run(run_id), self._execution_lease(run_id):
            root = self.run_store.load_run_paths(run_id).root
            payload = read_json(root / "turn_state.json")
            if not isinstance(payload, dict):
                raise ValueError(f"Turn state is missing: {run_id}")
            turn = TurnSnapshot.model_validate(payload)
            existing_operation = any(
                item.operation_id == request.operation_id
                for item in FollowUpRuntime(
                    root=root,
                    run_id=run_id,
                    turn_id=turn.turn_id,
                    config=turn.config.follow_up_config,
                ).state.pending_inputs
            )
            registry = getattr(self.main_agent, "tools", None)
            if registry is None or not callable(getattr(registry, "invoke", None)):
                registry = build_core_tool_registry()
            tool_result = registry.invoke(
                tool_name="core.followup.task",
                arguments=request.model_dump(mode="json"),
                context=CoreToolContext(
                    run_id=run_id,
                    thread_id=turn.thread_id,
                    request_id=turn.request_id,
                    turn_id=turn.turn_id,
                    run_root=root,
                    actor="system",
                    allowed_tools=frozenset({"core.followup.task"}),
                    allowed_permissions=frozenset({"turn:followup"}),
                    followup_task_handler=lambda item: self._followup_task_internal(run_id, item),
                    idempotency_key=(
                        f"{run_id}:followup:{request.operation_id}:{generate_ulid()}"
                    ),
                ),
            )
            if tool_result.status != "completed" or not isinstance(tool_result.output, dict):
                return FollowupTaskResult(
                    target_child_turn_id=request.target_child_turn_id,
                    accepted=False,
                    delivery="rejected",
                    reason=tool_result.error or "follow-up Tool invocation was rejected",
                )
            result = FollowupTaskResult.model_validate(tool_result.output)
            if existing_operation and not result.duplicate:
                result = result.model_copy(update={"duplicate": True})
            return result

    def _followup_task_internal(
        self,
        run_id: str,
        request: FollowupTaskRequest,
    ) -> FollowupTaskResult:
        """Run Follow-up logic while the enclosing Turn already owns its lease."""
        root = self.run_store.load_run_paths(run_id).root
        payload = read_json(root / "turn_state.json")
        if not isinstance(payload, dict):
            raise ValueError(f"Turn state is missing: {run_id}")
        turn = TurnSnapshot.model_validate(payload)
        if request.parent_turn_id != turn.turn_id:
            return FollowupTaskResult(
                target_child_turn_id=request.target_child_turn_id,
                accepted=False,
                delivery="rejected",
                reason="parent Turn does not match the requested follow-up",
            )
        target_child = next(
            (item for item in turn.child_turns if item.child_turn_id == request.target_child_turn_id),
            None,
        )
        if target_child is not None and request.target_agent_id not in {
            target_child.role_id,
            target_child.parent_node_id,
        }:
            return FollowupTaskResult(
                target_child_turn_id=request.target_child_turn_id,
                accepted=False,
                delivery="rejected",
                reason="target agent does not match the ChildTurn role",
            )
        event_turn = turn

        def emit_followup(event_type: str, event_payload: dict[str, Any]) -> None:
            nonlocal event_turn
            event_turn = self._append_snapshot_event(
                event_turn,
                event_type,
                **{
                    key: value
                    for key, value in event_payload.items()
                    if key not in {"run_id", "thread_id", "turn_id"}
                },
            )

        runtime = FollowUpRuntime(
            root=root,
            run_id=run_id,
            turn_id=turn.turn_id,
            config=turn.config.follow_up_config,
            emitter=emit_followup,
        )
        emit_followup(
            "followup_task_started",
            {
                "target_child_turn_id": request.target_child_turn_id,
                "target_agent_id": request.target_agent_id,
                "operation_id": request.operation_id,
            },
        )
        result = runtime.followup_task(
            request,
            known_child_turn_ids={item.child_turn_id for item in turn.child_turns},
        )
        turn = event_turn
        if result.accepted and result.delivery == "triggered_new_turn" and not result.duplicate:
            target_child = next(
                item for item in turn.child_turns
                if item.child_turn_id == request.target_child_turn_id
            )
            if target_child.status in {
                "ready",
                "completed",
                "completed_partial",
                "gated",
                "failed",
                "cancelled",
                "timed_out",
                "paused",
                "waiting_approval",
            }:
                continuation = ChildTurnRuntime(root).spawn(
                    parent_turn_id=turn.turn_id,
                    parent_run_id=turn.run_id,
                    parent_node_id=target_child.parent_node_id,
                    role_id=target_child.role_id,
                    objective=request.message,
                    relation="continuation",
                    role_contract=target_child.role_contract,
                )
                runtime.retarget_operation(request.operation_id, continuation.child_turn_id)
                turn = turn.model_copy(
                    update={
                        "child_turns": [*turn.child_turns, continuation],
                        "updated_at": _utc_now(),
                    }
                )
                self._persist_turn(turn)
                event_turn = turn
                result = result.model_copy(
                    update={"target_turn_id": continuation.child_turn_id}
                )
                emit_followup(
                    "child_turn_queued",
                    {
                        "target_child_turn_id": continuation.child_turn_id,
                        "parent_node_id": continuation.parent_node_id,
                        "relation": continuation.relation,
                        "operation_id": request.operation_id,
                    },
                )
        child_turn_id = (
            result.target_turn_id
            if result.accepted and result.delivery == "triggered_new_turn" and not result.duplicate
            else None
        )
        if child_turn_id and self._child_execution_available():
            event_turn = self._execute_child_followup(
                parent_turn=event_turn,
                child_turn_id=child_turn_id,
                message=request.message,
            )
        runtime.record_operation_result(request.operation_id, result)
        emit_followup(
            "followup_task_result",
            {
                "target_child_turn_id": result.target_child_turn_id,
                "target_turn_id": result.target_turn_id,
                "accepted": result.accepted,
                "delivery": result.delivery,
                "duplicate": result.duplicate,
                "reason": result.reason,
            },
        )
        return result

    def _child_execution_available(self) -> bool:
        """Return whether this process can execute a child Core Turn."""
        return (
            callable(getattr(self.main_agent, "_provider_tool_turn", None))
            and callable(getattr(self.main_agent, "_trim_transcript", None))
            and callable(getattr(self.main_agent, "_assistant_artifact", None))
            and hasattr(self.main_agent, "tools")
        )

    def _execute_child_followup(
        self,
        *,
        parent_turn: TurnSnapshot,
        child_turn_id: str,
        message: str,
    ) -> TurnSnapshot:
        """Execute an idle ChildTurn continuation in a namespaced checkpoint.

        The child keeps the parent Run identity for traceability, while its
        state, transcript, tool checkpoints and artifacts live below the
        parent run's ``child_turns`` directory. Parent state is updated only
        at child safe points through the event callback.
        """
        parent_root = self.run_store.load_run_paths(parent_turn.run_id).root
        child = next(
            item for item in parent_turn.child_turns if item.child_turn_id == child_turn_id
        )
        role_contract = self._resolve_child_role_contract(child)
        child_root = ChildTurnRuntime(parent_root).storage_root(child.child_turn_id)
        child_root.mkdir(parents=True, exist_ok=True)
        child_request = RequestEnvelope(
            thread_id=child.child_thread_id,
            user_id=(read_json(parent_root / "request.json") or {}).get("user_id"),
            tenant_id=(read_json(parent_root / "request.json") or {}).get("tenant_id"),
            intent=f"child.{child.role_id}",
            user_query=message,
            conversation_context=[
                {
                    "role": "parent_turn",
                    "turn_id": parent_turn.turn_id,
                    "run_id": parent_turn.run_id,
                    "child_turn_id": child.child_turn_id,
                }
            ],
        )
        child_config = TurnConfig(
            task_type="regular",
            execution_mode="core",
            hook_config=parent_turn.config.hook_config or HookConfig(),
            follow_up_config=parent_turn.config.follow_up_config or FollowUpConfig(),
            child_turn_config=parent_turn.config.child_turn_config,
            compact_config=parent_turn.config.compact_config,
            selection=TaskSelection(
                task_type="regular",
                execution_mode="core",
                reason_codes=["child_followup_task"],
                requires_persistence=True,
            ),
        )
        child_turn = TurnSnapshot(
            turn_id=child.child_turn_id,
            run_id=parent_turn.run_id,
            thread_id=child.child_thread_id,
            request_id=child_request.request_id,
            user_id=child_request.user_id,
            tenant_id=child_request.tenant_id,
            task_type="regular",
            execution_mode="core",
            status="running",
            config=child_config,
            config_hash=self._config_hash(child_config),
            task_selection=child_config.selection,
            deadline_at=parent_turn.deadline_at,
        )
        child_turn = child_turn.model_copy(
            update={
                "task_selection": child_config.selection,
            }
        )
        write_json(child_root / "request.json", child_request.model_dump(mode="json"))
        write_json(child_root / "turn_state.json", child_turn.model_dump(mode="json"))

        event_turn = parent_turn

        def observe_child(event: dict[str, Any]) -> None:
            nonlocal child_turn, event_turn
            event_type = str(event.get("event_type") or "child_event")
            child_turn, canonical_event = self._record_child_event(
                child_root=child_root,
                child_turn=child_turn,
                event_type=event_type,
                source_event=event,
            )
            event_turn = self._append_child_event(
                event_turn,
                child_turn,
                event_type,
                source_event=canonical_event,
            )

        child_context = TurnContext(
            request=child_request,
            decision=RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
                intent=child_request.intent,
                confidence=1.0,
            ),
            run_id=parent_turn.run_id,
            thread_id=child.child_thread_id,
            turn=child_turn,
            config=child_config,
            run_store=self.run_store,
            main_agent=self.main_agent,
            event_callback=observe_child,
            storage_root=child_root,
            parent_turn_id=parent_turn.turn_id,
            child_role_id=child.role_id,
            role_contract=role_contract,
            approval_store=HumanApprovalStore(parent_root / "approvals.jsonl"),
            deadline_at=self._monotonic_deadline(parent_turn.deadline_at),
        )
        child_context.hook_runtime = self.hook_runtime.clone(
            policy=child_config.hook_config.policy if child_config.hook_config else None,
            emitter=lambda event_type, payload: observe_child(
                {
                    "event_type": event_type,
                    "payload": payload,
                    "message": payload.get("message"),
                }
            ),
        )
        child_context.follow_up_runtime = FollowUpRuntime(
            root=child_root,
            run_id=parent_turn.run_id,
            turn_id=child_turn.turn_id,
            config=child_config.follow_up_config,
            emitter=lambda event_type, payload: observe_child(
                {
                    "event_type": event_type,
                    "payload": payload,
                }
            ),
        )
        child_context.follow_up_runtime.bind_execution(
            task_type="regular",
            execution_mode="core",
        )
        child_turn, started_event = self._record_child_event(
            child_root=child_root,
            child_turn=child_turn,
            event_type="child_turn_started",
            source_event={"message": "ChildTurn follow-up started."},
        )
        event_turn = self._append_child_event(
            event_turn,
            child_turn,
            "child_turn_started",
            source_event=started_event,
        )
        previous_event_turn_id = self.main_agent._event_turn_ids.get(parent_turn.run_id)
        self.main_agent._event_turn_ids[parent_turn.run_id] = child_turn.turn_id
        outcome_artifact_ids: list[str] = []
        try:
            executor = CoreLoopExecutor(self.main_agent)
            outcome = executor.execute(child_context)
            while outcome.status == "running":
                child_context.resume = True
                outcome = executor.execute(child_context)
            outcome_artifact_ids = list(outcome.artifact_ids)
            child_status = outcome.status
            child_turn = child_turn.model_copy(
                update={
                    "status": child_status,
                    "artifact_ids": list(outcome.artifact_ids),
                    "usage": dict(outcome.usage),
                    "continuation": outcome.continuation,
                    "updated_at": _utc_now(),
                }
            )
            write_json(child_root / "turn_state.json", child_turn.model_dump(mode="json"))
            event_turn = self._append_child_event(
                event_turn,
                child_turn,
                "child_turn_completed" if child_status == "completed" else "child_turn_finished",
                source_event={
                    "status": child_status,
                    "artifact_ids": list(outcome.artifact_ids),
                    "continuation": outcome.continuation,
                },
            )
        except Exception as exc:
            child_turn = child_turn.model_copy(
                update={
                    "status": "failed",
                    "continuation": {"source": "child_execution", "error": f"{exc.__class__.__name__}: {exc}"},
                    "updated_at": _utc_now(),
                }
            )
            write_json(child_root / "turn_state.json", child_turn.model_dump(mode="json"))
            event_turn = self._append_child_event(
                event_turn,
                child_turn,
                "child_turn_failed",
                source_event={"error": f"{exc.__class__.__name__}: {exc}"},
            )
        finally:
            if previous_event_turn_id is None:
                self.main_agent._event_turn_ids.pop(parent_turn.run_id, None)
            else:
                self.main_agent._event_turn_ids[parent_turn.run_id] = previous_event_turn_id

        persisted_child_state = read_json(child_root / "turn_state.json")
        persisted_child_main_state = read_json(child_root / "main_agent_state.json")
        child_artifact_ids = list(
            (persisted_child_main_state or {}).get("artifact_ids")
            if isinstance(persisted_child_main_state, dict)
            else []
        )
        child_artifact_ids = list(
            dict.fromkeys([*child_artifact_ids, *outcome_artifact_ids])
        )
        child_continuation = (
            (persisted_child_state or {}).get("continuation")
            if isinstance(persisted_child_state, dict)
            else None
        )
        parent_request_payload = read_json(parent_root / "request.json") or {}
        if isinstance(parent_request_payload, dict) and isinstance(
            parent_request_payload.get("request"), dict
        ):
            parent_request_payload = parent_request_payload["request"]
        parent_request = RequestEnvelope.model_validate(parent_request_payload)
        self._project_child_artifacts(
            parent_root=parent_root,
            parent_turn=event_turn,
            child=child,
            artifact_ids=child_artifact_ids,
            parent_request=parent_request,
        )
        ChildTurnRuntime(parent_root).update(
            child_turn.turn_id,
            status=child_turn.status,
            artifact_ids=child_artifact_ids,
            usage=dict(child_turn.usage),
            continuation=child_continuation,
            sequence=child_turn.sequence,
            last_event_id=child_turn.last_event_id,
        )
        child_projection = next(
            item for item in event_turn.child_turns
            if item.child_turn_id == child_turn.turn_id
        ).model_copy(
            update={
                "status": child_turn.status,
                "artifact_ids": child_artifact_ids,
                "usage": dict(child_turn.usage),
                "continuation": child_continuation,
                "sequence": child_turn.sequence,
                "last_event_id": child_turn.last_event_id,
                "updated_at": _utc_now(),
            }
        )
        updated_children = [
            child_projection if item.child_turn_id == child_turn.turn_id else item
            for item in event_turn.child_turns
        ]
        event_turn = event_turn.model_copy(
            update={"child_turns": updated_children, "updated_at": _utc_now()}
        )
        self._persist_turn(event_turn)
        return event_turn

    def _resolve_child_role_contract(self, child: ChildTurnSnapshot) -> RoleContract:
        """Resolve the immutable contract used by a ChildTurn Core executor."""
        if child.role_contract:
            return RoleContract.model_validate(child.role_contract)
        return self.role_registry.get(child.role_id)

    def _request_control(self, run_id: str, action: str, reason: str) -> TurnSnapshot:
        if action not in {"pause", "cancel"}:
            raise ValueError(f"unsupported Turn control action: {action}")
        root = self.run_store.load_run_paths(run_id).root
        turn_payload = read_json(root / "turn_state.json")
        if not isinstance(turn_payload, dict):
            raise ValueError(f"Turn state is missing: {run_id}")
        turn = TurnSnapshot.model_validate(turn_payload)
        if turn.status in RUN_TERMINAL_STATUSES:
            return turn
        control = read_json(root / "execution_control.json") or {}
        cancel_already_requested = bool(
            isinstance(control, dict) and control.get("cancel_requested")
        )
        cancel_requested = cancel_already_requested or action == "cancel"
        pause_requested = action == "pause" and not cancel_requested
        write_json(
            root / "execution_control.json",
            {
                "run_id": turn.run_id,
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
                "actor": "turn_runner.api",
                "status": "requested",
                "pause_requested": pause_requested,
                "cancel_requested": cancel_requested,
                "reason": reason,
                "requested_at": _utc_now().isoformat(),
                "expires_at": turn.deadline_at.isoformat() if turn.deadline_at else None,
            },
        )
        self._append_snapshot_event(
            turn,
            "turn_control_requested",
            action=action,
            reason=reason,
            actor="turn_runner.api",
            status="requested",
            effective_action=("cancel" if cancel_requested else action),
            cancel_request_preserved=cancel_already_requested,
            expires_at=turn.deadline_at.isoformat() if turn.deadline_at else None,
        )
        return self._load_turn(run_id)

    def _load_turn(self, run_id: str) -> TurnSnapshot:
        root = self.run_store.load_run_paths(run_id).root
        payload = read_json(root / "turn_state.json")
        if not isinstance(payload, dict):
            raise ValueError(f"Turn state is missing: {run_id}")
        return TurnSnapshot.model_validate(payload)

    def _append_snapshot_event(
        self,
        turn: TurnSnapshot,
        event_type: str,
        *,
        actor: str = "turn_runner",
        **payload: Any,
    ) -> TurnSnapshot:
        root = self.run_store.load_run_paths(turn.run_id).root
        event = EventEnvelope(
            event_type=event_type,
            run_id=turn.run_id,
            thread_id=turn.thread_id,
            request_id=turn.request_id,
            user_id=turn.user_id,
            tenant_id=turn.tenant_id,
            turn_id=turn.turn_id,
            task_type=turn.task_type,
            execution_mode=turn.execution_mode,
            sequence=0,
            actor=actor,
            payload=payload,
        ).model_dump(mode="json")
        event = append_canonical_event(root / "execution_events.jsonl", event)
        turn = turn.model_copy(
            update={
                "sequence": event["sequence"],
                "last_event_id": event["event_id"],
                "updated_at": _utc_now(),
            }
        )
        self._persist_turn(turn)
        return turn

    def _append_child_event(
        self,
        parent_turn: TurnSnapshot,
        child_turn: Any,
        event_type: str,
        *,
        source_event: dict[str, Any] | None = None,
    ) -> TurnSnapshot:
        """Project a child event into the parent Run's canonical event stream."""
        root = self.run_store.load_run_paths(parent_turn.run_id).root
        source = dict(source_event or {})
        payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
        child_id = str(
            getattr(child_turn, "child_turn_id", None)
            or getattr(child_turn, "turn_id", "")
        )
        child_thread_id = str(
            getattr(child_turn, "child_thread_id", None)
            or getattr(child_turn, "thread_id", "")
            or parent_turn.thread_id
        )
        event = EventEnvelope(
            event_type=event_type,
            run_id=parent_turn.run_id,
            thread_id=child_thread_id,
            request_id=str(getattr(child_turn, "request_id", "") or parent_turn.request_id),
            user_id=getattr(child_turn, "user_id", None) or parent_turn.user_id,
            tenant_id=getattr(child_turn, "tenant_id", None) or parent_turn.tenant_id,
            turn_id=child_id,
            parent_turn_id=parent_turn.turn_id,
            task_type="regular",
            execution_mode="core",
            sequence=0,
            actor="child_turn",
            payload={
                **payload,
                "child_event_type": str(source.get("event_type") or event_type),
                "child_turn_id": child_id,
                "child_thread_id": child_thread_id,
                **{
                    key: value
                    for key, value in source.items()
                    if key not in {"payload", "event_type"}
                },
            },
        ).model_dump(mode="json")
        event = append_canonical_event(root / "execution_events.jsonl", event)
        updated = parent_turn.model_copy(
            update={
                "sequence": event["sequence"],
                "last_event_id": event["event_id"],
                "updated_at": _utc_now(),
            }
        )
        self._persist_turn(updated)
        return updated

    def _record_child_event(
        self,
        *,
        child_root: Path,
        child_turn: TurnSnapshot,
        event_type: str,
        source_event: dict[str, Any],
    ) -> tuple[TurnSnapshot, dict[str, Any]]:
        """Persist a ChildTurn-local event and advance its own cursor."""
        events_path = child_root / "execution_events.jsonl"
        source_event_id = str(source_event.get("event_id") or "").strip()
        existing = read_jsonl(events_path)
        if source_event_id:
            existing_event = next(
                (item for item in existing if str(item.get("event_id") or "") == source_event_id),
                None,
            )
            if existing_event is not None:
                sequence = int(existing_event.get("sequence") or child_turn.sequence)
                updated = child_turn.model_copy(
                    update={
                        "sequence": max(child_turn.sequence, sequence),
                        "last_event_id": source_event_id,
                        "updated_at": _utc_now(),
                    }
                )
                write_json(child_root / "turn_state.json", updated.model_dump(mode="json"))
                return updated, existing_event

        event = EventEnvelope(
            event_id=source_event_id or generate_ulid(),
            event_type=event_type,
            run_id=child_turn.run_id,
            thread_id=child_turn.thread_id,
            request_id=child_turn.request_id,
            user_id=child_turn.user_id,
            tenant_id=child_turn.tenant_id,
            turn_id=child_turn.turn_id,
            task_type=child_turn.task_type,
            execution_mode=child_turn.execution_mode,
            parent_turn_id=str(
                (source_event.get("parent_turn_id") or "")
            ).strip() or None,
            sequence=child_turn.sequence + 1,
            actor=str(source_event.get("actor") or "child_turn"),
            node_id=source_event.get("node_id"),
            operation_id=source_event.get("operation_id"),
            payload=(
                dict(source_event.get("payload"))
                if isinstance(source_event.get("payload"), dict)
                else {
                    key: value
                    for key, value in source_event.items()
                    if key not in {
                        "event_id",
                        "event_type",
                        "created_at",
                        "run_id",
                        "thread_id",
                        "request_id",
                        "turn_id",
                        "sequence",
                        "actor",
                        "node_id",
                        "operation_id",
                    }
                }
            ),
        ).model_dump(mode="json")
        event = append_canonical_event(events_path, event)
        updated = child_turn.model_copy(
            update={
                "sequence": event["sequence"],
                "last_event_id": event["event_id"],
                "updated_at": _utc_now(),
            }
        )
        write_json(child_root / "turn_state.json", updated.model_dump(mode="json"))
        return updated, event

    def _event_observer(
        self,
        context: TurnContext,
        external_callback: Callable[[dict[str, Any]], None] | None,
    ) -> Callable[[dict[str, Any]], None]:
        def observe(event: dict[str, Any]) -> None:
            sequence = int(event.get("sequence") or 0)
            next_sequence = (
                sequence
                if sequence > context.turn.sequence
                else context.turn.sequence + 1
                if sequence == 0
                else context.turn.sequence
            )
            context.turn = context.turn.model_copy(
                update={
                    "sequence": next_sequence,
                    "last_event_id": str(event.get("event_id") or "").strip() or None,
                    "updated_at": _utc_now(),
                }
            )
            self._persist_turn(context.turn)
            if external_callback is not None:
                try:
                    external_callback(event)
                except Exception:
                    pass

        return observe

    def _emit(self, context: TurnContext, event_type: str, **payload: Any) -> None:
        root = self.run_store.load_run_paths(context.run_id).root
        event = EventEnvelope(
            event_type=event_type,
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request.request_id,
            turn_id=context.turn.turn_id,
            tenant_id=context.request.tenant_id,
            user_id=context.request.user_id,
            sequence=0,
            task_type=(str(payload["task_type"]) if payload.get("task_type") else None),
            execution_mode=(
                str(payload["execution_mode"])
                if payload.get("execution_mode")
                else None
            ),
            actor="turn_runner",
            payload=payload,
        ).model_dump(mode="json")
        event = append_canonical_event(root / "execution_events.jsonl", event)
        context.turn = context.turn.model_copy(
            update={
                "sequence": event["sequence"],
                "last_event_id": event["event_id"],
                "updated_at": _utc_now(),
            }
        )
        self._persist_turn(context.turn)
        if context.event_callback is not None:
            try:
                context.event_callback(event)
            except Exception:
                # Observers must not be able to corrupt the persisted Turn.
                pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "CoreLoopExecutor",
    "GraphExecutor",
    "GraphRunnerAdapter",
    "RegularExecutor",
    "RegularTask",
    "ReviewTask",
    "CompactTask",
    "TaskFactory",
    "TurnTask",
    "TurnContext",
    "TurnRunner",
]
