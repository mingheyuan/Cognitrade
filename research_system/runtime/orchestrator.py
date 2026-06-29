from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_system.agents.collector.service import CollectorService
from research_system.agents.critic.service import CriticService
from research_system.agents.distiller.service import DistillerService
from research_system.agents.reporter.service import ReporterService
from research_system.agents.reviewer.service import ReviewerService
from research_system.agents.retriever.service import RetrieverService
from research_system.agents.screener.service import ScreenerService
from research_system.agents.screen_reviewer.service import ScreenReviewerService
from research_system.runtime.blackboard import Blackboard
from research_system.runtime.checkpoint_store import CheckpointStore
from research_system.runtime.control_plane import RunControlPlane
from research_system.runtime.event_stream import EventStream
from research_system.runtime.gates import GateEvaluator
from research_system.runtime.jsonl import write_json
from research_system.runtime.logger import RunLogger
from research_system.runtime.provider_registry import ProviderRegistry
from research_system.runtime.retention import RunRetentionService
from research_system.runtime.run_store import RunPaths, RunStore
from research_system.runtime.session_history import SessionHistory
from research_system.runtime.settings import Settings
from research_system.runtime.skill_runtime import SkillRuntime
from research_system.runtime.planner import RuntimePlanner
from research_system.runtime.supervisor import RuntimeSupervisor
from research_system.runtime.jsonl import read_jsonl
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.runtime import BlackboardEntry, RunEvent, RunStateSnapshot
from research_system.services.progress_service import ProgressService
from research_system.services.task_router import TaskRouter


class OrchestratorError(Exception):
    pass


class RunCancelledError(OrchestratorError):
    pass


class RunTimedOutError(OrchestratorError):
    pass


@dataclass
class RuntimeContext:
    run_id: str
    paths: RunPaths
    event_stream: EventStream
    blackboard: Blackboard
    checkpoint_store: CheckpointStore
    control_plane: RunControlPlane
    session_history: SessionHistory
    logger: RunLogger
    state: RunStateSnapshot
    subagent_trace: list[dict]
    dynamic_trace: list[dict]


class ResearchOrchestrator:
    def __init__(self, settings: Settings, event_listener=None) -> None:
        self.settings = settings
        self.event_listener = event_listener
        self.run_store = RunStore(settings.runs_root)
        self.provider_registry = ProviderRegistry(settings)
        self.llm_provider = self.provider_registry.default()
        self.task_router = TaskRouter(self.llm_provider)
        self.progress_service = ProgressService()
        self.skill_runtime = SkillRuntime()
        yaml_configs = settings.load_yaml_configs()
        self.collector = CollectorService(self.skill_runtime, settings.runs_root)
        self.distiller = DistillerService(self.llm_provider)
        self.critic = CriticService()
        self.reviewer = ReviewerService(yaml_configs.get("review_policy"))
        self.retriever = RetrieverService(self.llm_provider, settings, yaml_configs.get("retrieval_policy"))
        self.reporter = ReporterService(self.llm_provider)
        self.screener = ScreenerService()
        self.screen_reviewer = ScreenReviewerService()
        self.gates = GateEvaluator()
        self.retention = RunRetentionService(settings)
        self.planner = RuntimePlanner(self.llm_provider)
        self.supervisor = RuntimeSupervisor()

    def close(self) -> None:
        close = getattr(self.retriever, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "ResearchOrchestrator":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def run(self, request: RequestEnvelope) -> ResponseEnvelope:
        decision = self.task_router.identify(request)
        if decision.intent is None:
            progress = self.progress_service.build(
                current_phase="clarify",
                active_agent=None,
                latest_event="clarification_requested",
                status_message="Need clarification before execution",
                percent=0,
            )
            return ResponseEnvelope(
                request_id=request.request_id,
                intent=request.intent,
                status="needs_clarification",
                message="Clarification required",
                clarification=decision.clarification,
                progress=progress,
            )

        intent = decision.intent
        run_id, paths, state = self.run_store.create_run(request, intent)
        context = RuntimeContext(
            run_id=run_id,
            paths=paths,
            event_stream=EventStream(paths.events),
            blackboard=Blackboard(paths.blackboard),
            checkpoint_store=CheckpointStore(paths.checkpoint),
            control_plane=RunControlPlane(paths.run_control),
            session_history=SessionHistory(paths.root),
            logger=RunLogger(paths.logs),
            state=state,
            subagent_trace=[],
            dynamic_trace=[],
        )
        context.control_plane.initialize(run_id=run_id, run_timeout_seconds=self.settings.run_timeout_seconds)

        try:
            self._emit_event(context, request_id=request.request_id, intent=intent, event_type="request_received", message="Request received")
            self._transition(context, "identifying_intent", request_id=request.request_id, intent=intent, message="Identifying intent")
            self._emit_event(context, request_id=request.request_id, intent=intent, event_type="intent_identified", message=f"Intent identified: {intent}")
            plan = self.planner.build_initial_plan(intent=intent, request=request)
            write_json(
                paths.task_plan,
                {
                    "intent": plan.intent,
                    "strategy": plan.strategy,
                    "entry_strategy": plan.entry_strategy,
                    "entry_reason": plan.entry_reason,
                    "subagent_required": plan.subagent_required,
                    "dispatch_trace": plan.dispatch_trace,
                    "notes": plan.notes,
                    "actions": [action.model_dump(mode="json") for action in plan.actions],
                },
            )
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=intent,
                event_type="plan_created",
                message="Execution plan created",
                payload={
                    "strategy": plan.strategy,
                    "entry_strategy": plan.entry_strategy,
                    "entry_reason": plan.entry_reason,
                    "subagent_required": plan.subagent_required,
                    "dispatch_trace": plan.dispatch_trace,
                    "actions": [
                        {"node_id": action.node_id, "phase": action.phase, "agent": action.agent}
                        for action in plan.actions
                    ],
                    "notes": plan.notes,
                },
            )
            self._transition(context, "ready", request_id=request.request_id, intent=intent, message="Execution plan created")
            self._transition(context, "running", request_id=request.request_id, intent=intent, message="Run accepted")
            self._emit_event(context, request_id=request.request_id, intent=intent, event_type="run_accepted", message="Run accepted")
            self._execute_plan(context=context, request=request, plan=plan)

            self._enforce_runtime_controls(context, request_id=request.request_id, intent=intent)
            self._transition(context, "gated", request_id=request.request_id, intent=intent, message="Evaluating run gates")
            verdicts = self.gates.evaluate(
                run_id=run_id,
                phase="report",
                entries=context.blackboard.list_entries(),
                requires_verified_snapshot=True,
                requires_review_findings=intent == "review_thesis",
            )
            blocked = [verdict for verdict in verdicts if verdict.verdict == "fail"]
            for verdict in verdicts:
                self._emit_event(
                    context,
                    request_id=request.request_id,
                    intent=intent,
                    event_type="phase_completed",
                    phase="gate",
                    message=f"{verdict.gate_type}: {verdict.verdict}",
                    payload=verdict.model_dump(mode="json"),
                )

            if blocked:
                self._transition(context, "failed", request_id=request.request_id, intent=intent, message="One or more gates failed")
                result = {
                    "intent": intent,
                    "status": "failed",
                    "gate_failures": [verdict.model_dump(mode="json") for verdict in blocked],
                }
                summary = {
                    "intent": intent,
                    "status": "failed",
                    "message": "Run failed gate validation",
                }
                context.session_history.write_result(result, summary)
                self.retention.apply_on_terminal_failure(context.paths.root)
                return self._build_response(
                    request=request,
                    run_id=run_id,
                    intent=intent,
                    status="failed",
                    message="Run failed gate validation",
                    result=result,
                    phase="gate",
                    active_agent=None,
                    latest_event="run_failed",
                    percent=100,
                    errors=["gate_validation_failed"],
                )

            self._transition(context, "completed", request_id=request.request_id, intent=intent, message="Run completed")
            result = {
                "intent": intent,
                "report": self._load_result_payload(paths.result),
            }
            summary = {
                "intent": intent,
                "status": "completed",
                "message": "Run completed successfully",
            }
            context.session_history.write_result(result, summary)
            self.retention.apply_on_success(context.paths.root)
            self._emit_event(context, request_id=request.request_id, intent=intent, event_type="run_completed", message="Run completed")
            return self._build_response(
                request=request,
                run_id=run_id,
                intent=intent,
                status="completed",
                message="Run completed",
                result=result,
                phase="report",
                active_agent="reporter",
                latest_event="run_completed",
                percent=100,
            )
        except RunCancelledError:
            result = {"intent": intent, "status": "cancelled"}
            summary = {"intent": intent, "status": "cancelled", "message": "Run cancelled"}
            context.session_history.write_result(result, summary)
            self.retention.apply_on_terminal_failure(context.paths.root)
            return self._build_response(
                request=request,
                run_id=run_id,
                intent=intent,
                status="cancelled",
                message="Run cancelled",
                result=result,
                phase=context.state.current_phase,
                active_agent=context.state.active_agent,
                latest_event="run_cancelled",
                percent=100,
            )
        except RunTimedOutError:
            result = {"intent": intent, "status": "timed_out"}
            summary = {"intent": intent, "status": "timed_out", "message": "Run timed out"}
            context.session_history.write_result(result, summary)
            self.retention.apply_on_terminal_failure(context.paths.root)
            return self._build_response(
                request=request,
                run_id=run_id,
                intent=intent,
                status="timed_out",
                message="Run timed out",
                result=result,
                phase=context.state.current_phase,
                active_agent=context.state.active_agent,
                latest_event="run_timed_out",
                percent=100,
                errors=["run_timed_out"],
            )
        except Exception as exc:  # pragma: no cover - final guardrail
            context.logger.log(run_id, "ERROR", "orchestrator", str(exc))
            self._transition(context, "failed", request_id=request.request_id, intent=intent, message="Run failed unexpectedly", error_summary=str(exc))
            self._emit_event(context, request_id=request.request_id, intent=intent, event_type="run_failed", message="Run failed", payload={"error": str(exc)})
            result = {"intent": intent, "status": "failed"}
            summary = {"intent": intent, "status": "failed", "message": str(exc)}
            context.session_history.write_result(result, summary)
            self.retention.apply_on_terminal_failure(context.paths.root)
            return self._build_response(
                request=request,
                run_id=run_id,
                intent=intent,
                status="failed",
                message=str(exc),
                result=result,
                phase=context.state.current_phase,
                active_agent=context.state.active_agent,
                latest_event="run_failed",
                percent=100,
                errors=[str(exc)],
            )

    def replay(self, run_id: str) -> dict:
        paths = self.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).replay()

    def history(self) -> list[dict]:
        return self.run_store.list_runs()

    def resume(self, run_id: str) -> dict:
        paths = self.run_store.load_run_paths(run_id)
        run_state_payload = self._load_json_payload(paths.run_state) or {}
        checkpoint = CheckpointStore(paths.checkpoint).load()
        status = run_state_payload.get("status")
        resumable = status in {"failed", "timed_out", "cancelled"} and checkpoint is not None
        return {
            "run_id": run_id,
            "status": status,
            "resumable": resumable,
            "checkpoint": checkpoint.model_dump(mode="json") if checkpoint else None,
            "resume_from": {
                "current_phase": checkpoint.current_phase if checkpoint else None,
                "active_agent": checkpoint.active_agent if checkpoint else None,
                "completed_nodes": checkpoint.completed_nodes if checkpoint else [],
                "pending_nodes": checkpoint.pending_nodes if checkpoint else [],
            } if checkpoint else None,
        }

    def cancel(self, run_id: str) -> dict:
        paths = self.run_store.load_run_paths(run_id)
        control_plane = RunControlPlane(paths.run_control)
        control_state = control_plane.request_cancellation(reason="manual_cancel")
        run_state_payload = self._load_json_payload(paths.run_state)
        if run_state_payload and run_state_payload.get("status") in {"running", "waiting_external"}:
            run_state_payload["status"] = "cancelled"
            write_json(paths.run_state, run_state_payload)
        event_stream = EventStream(paths.events)
        event_stream.emit(
            RunEvent(
                request_id=(self._load_json_payload(paths.request) or {}).get("request_id", "unknown_request"),
                run_id=run_id,
                intent=(run_state_payload or {}).get("intent", "unknown_intent"),
                event_type="run_cancelled",
                message="Cancellation requested",
                payload={"reason": "manual_cancel"},
            )
        )
        payload = {
            "run_id": run_id,
            "status": "cancelled",
            "message": "Cancellation requested.",
            "control_state": control_state.to_dict() if control_state else None,
        }
        write_json(paths.summary, payload)
        return payload

    def cleanup(self) -> dict:
        return self.retention.cleanup_artifacts(self.settings.runs_root)

    def rebuild_qdrant(self) -> dict:
        if not self.retriever.qdrant_available or self.retriever.qdrant is None:
            return {
                "status": "skipped",
                "message": "Qdrant unavailable",
                "qdrant_error": self.retriever.qdrant_error,
            }

        qdrant = self.retriever.qdrant
        qdrant.reset_collection()

        indexed_entries = 0
        skipped_entries = 0
        indexed_runs = 0
        skipped_runs: list[str] = []

        for run_dir in sorted(self.settings.runs_root.iterdir()):
            if not run_dir.is_dir():
                continue
            blackboard_path = run_dir / "blackboard.jsonl"
            if not blackboard_path.exists():
                skipped_runs.append(run_dir.name)
                continue

            indexed_this_run = 0
            for row in read_jsonl(blackboard_path):
                if row.get("kind") not in {"source_record", "verified_snapshot", "logic_block", "distilled_dimension", "review_finding"}:
                    skipped_entries += 1
                    continue
                try:
                    entry = BlackboardEntry.model_validate(row)
                except Exception:
                    skipped_entries += 1
                    continue
                qdrant.upsert_entry(entry)
                indexed_entries += 1
                indexed_this_run += 1

            if indexed_this_run > 0:
                indexed_runs += 1
            else:
                skipped_runs.append(run_dir.name)

        return {
            "status": "completed",
            "message": "Qdrant rebuilt from workspace runs",
            "collection_name": self.settings.qdrant_collection_name,
            "indexed_runs": indexed_runs,
            "indexed_entries": indexed_entries,
            "skipped_entries": skipped_entries,
            "skipped_runs": skipped_runs,
        }

    def _execute_plan(self, *, context: RuntimeContext, request: RequestEnvelope, plan: ActionPlan) -> None:
        pending_actions: list[ActionSpec] = [action.model_copy(deep=True) for action in plan.actions]
        executed_node_ids: list[str] = []

        while True:
            self._enforce_runtime_controls(context, request_id=request.request_id, intent=plan.intent)
            decision = self.supervisor.decide(
                plan=plan,
                completed_actions=executed_node_ids[:],
                pending_actions=pending_actions[:],
                blackboard_entries=context.blackboard.list_entries(),
            )
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=plan.intent,
                event_type="supervisor_decision",
                message="Supervisor evaluated next actions",
                payload={
                    "decision_type": decision.decision_type,
                    "completed_actions": executed_node_ids[:],
                    "next_actions": [
                        {"node_id": action.node_id, "phase": action.phase, "agent": action.agent}
                        for action in decision.next_actions
                    ],
                    "notes": decision.notes,
                },
            )
            if decision.decision_type == "stop" or not decision.next_actions:
                self._update_state(
                    context,
                    pending_nodes=[],
                    completed_nodes=executed_node_ids[:],
                )
                context.checkpoint_store.save(context.state)
                return

            for action in decision.next_actions:
                if action.node_id not in [existing.node_id for existing in pending_actions]:
                    pending_actions.append(action)
                    context.dynamic_trace.append(
                        {
                            "event": "dynamic_action_inserted",
                            "node_id": action.node_id,
                            "phase": action.phase,
                            "agent": action.agent,
                            "tags": action.tags,
                        }
                    )
                    self._emit_event(
                        context,
                        request_id=request.request_id,
                        intent=plan.intent,
                        event_type="dynamic_action_inserted",
                        phase=action.phase,
                        agent=action.agent,
                        message=f"Dynamically inserted action: {action.node_id}",
                        payload=action.model_dump(mode="json"),
                    )
                action_index = next(
                    index for index, candidate in enumerate(pending_actions)
                    if candidate.node_id == action.node_id
                )
                current_action = pending_actions.pop(action_index)
                self._run_action(
                    context=context,
                    request=request,
                    plan=plan,
                    intent=plan.intent,
                    action=current_action,
                    completed_node_ids=executed_node_ids[:],
                    pending_actions_after_current=pending_actions[:],
                )
                executed_node_ids.append(current_action.node_id)

    def _run_action(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        intent: str,
        action: ActionSpec,
        completed_node_ids: list[str],
        pending_actions_after_current: list[ActionSpec],
    ) -> None:
        phase = action.phase
        agent = action.agent
        self._update_state(context, current_phase=phase, active_agent=agent)
        self._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="phase_started",
            phase=phase,
            agent=agent,
            message=f"Phase started: {phase}",
        )
        self._enforce_runtime_controls(context, request_id=request.request_id, intent=intent)
        self._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="agent_started",
            phase=phase,
            agent=agent,
            message=f"Agent started: {agent}",
        )

        if agent == "collector":
            collector_query = str(action.inputs.get("query") or request.user_query)
            collector_constraints = dict(request.constraints)
            collector_constraints.update(action.inputs or {})
            payload = self.collector.run(intent=intent, query=collector_query, constraints=collector_constraints)
            self._extend_subagent_trace(context, agent=agent, phase=phase, payload=payload)
            if payload.get("requires_wait"):
                self._transition(
                    context,
                    "waiting_external",
                    request_id=request.request_id,
                    intent=intent,
                    message="Waiting for external dependency",
                )
                context.control_plane.set_wait_deadline(wait_timeout_seconds=self.settings.external_wait_timeout_seconds)
                self._emit_event(
                    context,
                    request_id=request.request_id,
                    intent=intent,
                    event_type="phase_started",
                    phase=phase,
                    agent=agent,
                    message="Entered waiting_external state",
                    payload={"wait_timeout_seconds": self.settings.external_wait_timeout_seconds},
                )
                self._wait_for_external_dependency(context, request_id=request.request_id, intent=intent)
                context.control_plane.clear_wait_deadline()
                self._transition(
                    context,
                    "running",
                    request_id=request.request_id,
                    intent=intent,
                    message="External dependency resolved",
                )
            for source in payload["sources"]:
                entry = BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="source_record",
                    agent=agent,
                    phase=phase,
                    payload=source,
                    source_refs=[{"type": "request", "request_id": request.request_id, "run_id": context.run_id}],
                )
                self._add_entry(context, request, intent, entry)
            for snapshot in payload["snapshots"]:
                entry = BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="verified_snapshot",
                    agent=agent,
                    phase=phase,
                    payload=snapshot,
                    source_refs=[{"type": "skill", "skill": snapshot["source"]["skill"], "run_id": context.run_id}],
                    confidence=1.0,
                )
                self._add_entry(context, request, intent, entry)
        elif agent == "retriever":
            blackboard_entries = [entry.model_dump(mode="json") for entry in context.blackboard.list_entries()]
            payload = self.retriever.run(intent=intent, query=request.user_query, blackboard_entries=blackboard_entries)
            self._extend_subagent_trace(
                context,
                agent=agent,
                phase=phase,
                payload={
                    "subagent_trace": [
                        {
                            "subagent_type": "blackboard_evidence_indexer",
                            "status": "completed",
                            "objective": "Index blackboard entries and expose evidence candidates.",
                            "notes": [f"blackboard_entries={len(blackboard_entries)}"],
                        },
                        {
                            "subagent_type": "retrieval_decider",
                            "status": "completed",
                            "objective": "Select final evidence set and summarize retrieval.",
                            "notes": [
                                f"evidence_count={len(payload.get('evidences') or [])}",
                                f"degraded={str(bool(payload.get('degraded'))).lower()}",
                            ],
                        },
                    ]
                },
            )
            entry = BlackboardEntry(
                run_id=context.run_id,
                request_id=request.request_id,
                kind="retrieved_evidence",
                agent=agent,
                phase=phase,
                payload=payload,
                source_refs=[{"type": "blackboard", "count": len(blackboard_entries), "run_id": context.run_id}],
                confidence=1.0,
            )
            self._add_entry(context, request, intent, entry)
        elif agent == "screener":
            source_payloads = [
                entry.payload
                for entry in context.blackboard.list_entries()
                if entry.kind == "source_record"
            ]
            snapshot_payloads = [
                entry.payload
                for entry in context.blackboard.list_entries()
                if entry.kind == "verified_snapshot"
            ]
            payload = self.screener.run(
                query=request.user_query,
                source_payloads=source_payloads,
                snapshot_payloads=snapshot_payloads,
                constraints=request.constraints,
            )
            self._extend_subagent_trace(
                context,
                agent=agent,
                phase=phase,
                payload={
                    "subagent_trace": [
                        {
                            "subagent_type": "screening_decider",
                            "status": "completed",
                            "objective": "Rank candidates and decide enrichment targets.",
                            "notes": list(payload.get("screening_notes") or []),
                        }
                    ]
                },
            )
            if intent == "stock_screen":
                enrichment_targets = payload.get("enrichment_targets") or []
                if enrichment_targets:
                    enrichment_payload = self.collector.enrich_stock_screen_candidates(
                        candidates=enrichment_targets,
                        snapshot_types=["market", "fundamental", "notice"],
                    )
                    self._extend_subagent_trace(context, agent="collector", phase=phase, payload=enrichment_payload)
                    self._emit_event(
                        context,
                        request_id=request.request_id,
                        intent=intent,
                        event_type="agent_progress",
                        phase=phase,
                        agent=agent,
                        message=f"Enriching top {len(enrichment_targets)} stock_screen candidates",
                        payload={"candidate_symbols": [item.get("symbol") for item in enrichment_targets]},
                    )
                    for snapshot in enrichment_payload.get("snapshots", []):
                        snapshot_type = snapshot.get("snapshot_type", "snapshot")
                        entry = BlackboardEntry(
                            run_id=context.run_id,
                            request_id=request.request_id,
                            kind="verified_snapshot",
                            agent=agent,
                            phase=phase,
                            payload=snapshot,
                            source_refs=[{"type": "stock_screen_enrichment", "run_id": context.run_id}],
                            confidence=1.0,
                            risk_flags=[snapshot.get("degrade_reason")] if snapshot.get("degraded") else [],
                        )
                        self._add_entry(context, request, intent, entry)
                        self._emit_event(
                            context,
                            request_id=request.request_id,
                            intent=intent,
                            event_type="subagent_completed",
                            phase=phase,
                            agent=agent,
                            message=f"Enriched {snapshot_type} snapshot for {snapshot.get('symbol') or 'unknown'}",
                        )
                    refreshed_snapshot_payloads = [
                        entry.payload
                        for entry in context.blackboard.list_entries()
                        if entry.kind == "verified_snapshot"
                    ]
                    enriched_constraints = dict(request.constraints)
                    enriched_constraints["skip_enrichment"] = True
                    payload = self.screener.run(
                        query=request.user_query,
                        source_payloads=source_payloads,
                        snapshot_payloads=refreshed_snapshot_payloads,
                        constraints=enriched_constraints,
                    )
                    self._extend_subagent_trace(
                        context,
                        agent=agent,
                        phase=phase,
                        payload={
                            "subagent_trace": [
                                {
                                    "subagent_type": "screening_decider",
                                    "status": "completed",
                                    "objective": "Re-rank candidates after enrichment.",
                                    "notes": list(payload.get("screening_notes") or []) + ["post_enrichment=true"],
                                }
                            ]
                        },
                    )
            for candidate in payload.get("screened_candidates", []):
                entry = BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="source_record",
                    agent=agent,
                    phase=phase,
                    payload={
                        "title": f"screen_candidate:{candidate.get('symbol')}",
                        "content": str(candidate),
                        "source_type": "screen_candidate",
                        "candidate": candidate,
                    },
                    source_refs=[{"type": "screening", "run_id": context.run_id}],
                    confidence=1.0,
                    risk_flags=candidate.get("missing_evidence") or [],
                )
                self._add_entry(context, request, intent, entry)
            for candidate in payload.get("watchlist_candidates", []):
                entry = BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="source_record",
                    agent=agent,
                    phase=phase,
                    payload={
                        "title": f"watchlist_candidate:{candidate.get('symbol')}",
                        "content": str(candidate),
                        "source_type": "watchlist_candidate",
                        "candidate": candidate,
                    },
                    source_refs=[{"type": "screening_watchlist", "run_id": context.run_id}],
                    confidence=1.0,
                    risk_flags=candidate.get("missing_evidence") or [],
                )
                self._add_entry(context, request, intent, entry)
            scorecard = payload.get("scorecard") or {}
            scorecard_entry = BlackboardEntry(
                run_id=context.run_id,
                request_id=request.request_id,
                kind="source_record",
                agent=agent,
                phase=phase,
                payload={
                    "title": "screening_scorecard",
                    "content": str(scorecard),
                    "source_type": "screening_scorecard",
                    "scorecard": scorecard,
                },
                source_refs=[{"type": "screening", "run_id": context.run_id}],
                confidence=1.0,
                risk_flags=[],
            )
            self._add_entry(context, request, intent, scorecard_entry)
        elif agent == "distiller":
            source_records = [
                entry.payload
                for entry in context.blackboard.list_entries()
                if entry.kind == "source_record"
            ]
            payload = self.distiller.run(query=request.user_query, source_records=source_records)
            self._extend_subagent_trace(
                context,
                agent=agent,
                phase=phase,
                payload={
                    "subagent_trace": [
                        {
                            "subagent_type": "distillation_pipeline",
                            "status": "completed",
                            "objective": "Distill source records into logic blocks and dimensions.",
                            "notes": list(((payload.get("summary") or {}).get("processing_notes") or [])),
                        }
                    ]
                },
            )
            for logic_block in payload.get("logic_blocks", []):
                entry = BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="logic_block",
                    agent=agent,
                    phase=phase,
                    payload=logic_block,
                    source_refs=[
                        {
                            "type": "distillation",
                            "source_record_id": logic_block.get("source_record_id"),
                            "run_id": context.run_id,
                        }
                    ],
                    confidence=logic_block.get("confidence"),
                    risk_flags=logic_block.get("risk_flags") or [],
                )
                self._add_entry(context, request, intent, entry)
            for dimension in payload.get("distilled_dimensions", []):
                entry = BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="distilled_dimension",
                    agent=agent,
                    phase=phase,
                    payload=dimension,
                    source_refs=[
                        {
                            "type": "distillation",
                            "supported_claim_block_ids": dimension.get("supported_claim_block_ids", []),
                            "run_id": context.run_id,
                        }
                    ],
                    confidence=dimension.get("confidence"),
                    risk_flags=[],
                )
                self._add_entry(context, request, intent, entry)
            summary_entry = BlackboardEntry(
                run_id=context.run_id,
                request_id=request.request_id,
                kind="source_record",
                agent=agent,
                phase=phase,
                payload={
                    "title": "distillation_summary",
                    "content": str(payload.get("summary") or {}),
                    "source_type": "distillation_summary",
                    "summary": payload.get("summary") or {},
                },
                source_refs=[{"type": "distillation", "run_id": context.run_id}],
                confidence=payload.get("summary", {}).get("overall_confidence"),
                risk_flags=payload.get("summary", {}).get("major_risks") or [],
            )
            self._add_entry(context, request, intent, summary_entry)
        elif agent == "critic":
            logic_blocks = []
            distilled_dimensions = []
            distillation_summary = None
            for entry in context.blackboard.list_entries():
                if entry.kind == "logic_block":
                    logic_blocks.append(entry.payload)
                elif entry.kind == "distilled_dimension":
                    distilled_dimensions.append(entry.payload)
                elif entry.kind == "source_record" and entry.payload.get("source_type") == "distillation_summary":
                    distillation_summary = entry.payload.get("summary")
                    if distillation_summary is None:
                        content = entry.payload.get("content")
                        if isinstance(content, str):
                            import ast

                            try:
                                parsed = ast.literal_eval(content)
                            except Exception:
                                parsed = None
                            if isinstance(parsed, dict):
                                distillation_summary = parsed
            critic_payload = self.critic.run(
                distillation_summary=distillation_summary,
                logic_blocks=logic_blocks,
                distilled_dimensions=distilled_dimensions,
            )
            self._extend_subagent_trace(
                context,
                agent=agent,
                phase=phase,
                payload={
                    "subagent_trace": [
                        {
                            "subagent_type": "critic_review_pipeline",
                            "status": "completed",
                            "objective": "Review distillation risks and build scorecard.",
                            "notes": list((critic_payload.get("summary") or {}).get("notes") or []),
                        }
                    ]
                },
            )
            summary = critic_payload.get("summary") or {}
            scorecard = critic_payload.get("scorecard") or {}
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=intent,
                event_type="review_requested",
                phase=phase,
                agent=agent,
                message="Critic review requested",
                payload={
                    "review_required": summary.get("review_required", False),
                    "finding_count": summary.get("finding_count", 0),
                },
            )
            if scorecard:
                scorecard_entry = BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="source_record",
                    agent=agent,
                    phase=phase,
                    payload={
                        "title": "distillation_scorecard",
                        "content": str(scorecard),
                        "source_type": "distillation_scorecard",
                        "scorecard": scorecard,
                    },
                    source_refs=[{"type": "critic_review", "run_id": context.run_id}],
                    confidence=1.0,
                    risk_flags=[],
                )
                self._add_entry(context, request, intent, scorecard_entry)
            for finding in critic_payload.get("findings", []):
                entry = BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="review_finding",
                    agent=agent,
                    phase=phase,
                    status="needs_review" if finding.get("verdict") == "needs_review" else "active",
                    payload=finding,
                    source_refs=[{"type": "critic_review", "run_id": context.run_id}],
                    confidence=1.0,
                    risk_flags=[finding.get("finding_type")] if finding.get("finding_type") else [],
                )
                self._add_entry(context, request, intent, entry)
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=intent,
                event_type="review_completed",
                phase=phase,
                agent=agent,
                message="Critic review completed",
                payload={**summary, "scorecard": scorecard},
            )
        elif agent == "reviewer":
            review_request = None
            latest_sector_snapshot = None
            latest_market_snapshot = None
            latest_fundamental_snapshot = None
            latest_notice_snapshot = None
            source_entries: list[dict] = []
            for entry in context.blackboard.list_entries():
                if entry.kind == "source_record" and entry.payload.get("source_type") == "review_request":
                    review_request = entry.payload
                elif entry.kind == "verified_snapshot":
                    snapshot_type = entry.payload.get("snapshot_type")
                    if snapshot_type == "sector":
                        latest_sector_snapshot = entry.payload
                    elif snapshot_type == "market":
                        latest_market_snapshot = entry.payload
                    elif snapshot_type == "fundamental":
                        latest_fundamental_snapshot = entry.payload
                    elif snapshot_type == "notice":
                        latest_notice_snapshot = entry.payload
            if review_request is not None:
                source_entries = list(review_request.get("review_source_entries") or [])
            reviewer_payload = self.reviewer.run(
                source_run_id=str((review_request or {}).get("review_target_run_id") or ""),
                query=request.user_query,
                source_entries=source_entries,
                latest_sector_snapshot=latest_sector_snapshot,
                latest_market_snapshot=latest_market_snapshot,
                latest_fundamental_snapshot=latest_fundamental_snapshot,
                latest_notice_snapshot=latest_notice_snapshot,
            )
            self._extend_subagent_trace(
                context,
                agent=agent,
                phase=phase,
                payload={
                    "subagent_trace": [
                        {
                            "subagent_type": "review_claim_extractor",
                            "status": "completed",
                            "objective": "Extract reviewable claims from review target.",
                            "notes": [f"source_entry_count={len(source_entries)}"],
                        },
                        {
                            "subagent_type": "review_summary_builder",
                            "status": "completed",
                            "objective": "Aggregate posterior review findings into review summary.",
                            "notes": list((reviewer_payload.get("summary") or {}).get("notes") or []),
                        },
                    ]
                },
            )
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=intent,
                event_type="review_requested",
                phase=phase,
                agent=agent,
                message="Reviewer posterior review requested",
                payload={"source_run_id": (review_request or {}).get("review_target_run_id")},
            )
            for finding in reviewer_payload.get("findings", []):
                entry = BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="review_finding",
                    agent=agent,
                    phase=phase,
                    status="active" if finding.get("verdict") == "confirmed" else "needs_review",
                    payload=finding,
                    source_refs=[
                        {
                            "type": "posterior_review",
                            "review_target_run_id": (review_request or {}).get("review_target_run_id"),
                            "run_id": context.run_id,
                        }
                    ],
                    confidence=1.0,
                    risk_flags=[str(finding.get("verdict") or "inconclusive")],
                )
                self._add_entry(context, request, intent, entry)
            summary_entry = BlackboardEntry(
                run_id=context.run_id,
                request_id=request.request_id,
                kind="source_record",
                agent=agent,
                phase=phase,
                payload={
                    "title": "review_summary",
                    "content": str(reviewer_payload.get("summary") or {}),
                    "source_type": "review_summary",
                    "summary": reviewer_payload.get("summary") or {},
                },
                source_refs=[
                    {
                        "type": "posterior_review",
                        "review_target_run_id": (review_request or {}).get("review_target_run_id"),
                        "run_id": context.run_id,
                    }
                ],
                confidence=1.0,
                risk_flags=[],
            )
            self._add_entry(context, request, intent, summary_entry)
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=intent,
                event_type="review_completed",
                phase=phase,
                agent=agent,
                message="Reviewer posterior review completed",
                payload=reviewer_payload.get("summary") or {},
            )
        elif agent == "screen_reviewer":
            screen_candidates: list[dict] = []
            screening_scorecard = None
            for entry in context.blackboard.list_entries():
                if entry.kind != "source_record":
                    continue
                source_type = str(entry.payload.get("source_type") or "")
                if source_type in {"screen_candidate", "watchlist_candidate"}:
                    candidate = entry.payload.get("candidate")
                    if isinstance(candidate, dict):
                        screen_candidates.append(candidate)
                elif source_type == "screening_scorecard":
                    screening_scorecard = entry.payload.get("scorecard")
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=intent,
                event_type="review_requested",
                phase=phase,
                agent=agent,
                message="Screen reviewer requested",
                payload={"candidate_count": len(screen_candidates)},
            )
            reviewer_payload = self.screen_reviewer.run(
                source_run_id=context.run_id,
                candidates=screen_candidates,
                scorecard=screening_scorecard,
            )
            self._extend_subagent_trace(
                context,
                agent=agent,
                phase=phase,
                payload={
                    "subagent_trace": [
                        {
                            "subagent_type": "screen_candidate_reviewer",
                            "status": "completed",
                            "objective": "Perform lightweight evidence review for screened candidates.",
                            "notes": list((reviewer_payload.get("summary") or {}).get("notes") or []),
                        }
                    ]
                },
            )
            for finding in reviewer_payload.get("findings", []):
                entry = BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="review_finding",
                    agent=agent,
                    phase=phase,
                    status="active" if finding.get("verdict") == "confirmed" else "needs_review",
                    payload=finding,
                    source_refs=[{"type": "screen_review", "run_id": context.run_id}],
                    confidence=1.0,
                    risk_flags=[str(finding.get("verdict") or "inconclusive")],
                )
                self._add_entry(context, request, intent, entry)
            summary_entry = BlackboardEntry(
                run_id=context.run_id,
                request_id=request.request_id,
                kind="source_record",
                agent=agent,
                phase=phase,
                payload={
                    "title": "review_summary",
                    "content": str(reviewer_payload.get("summary") or {}),
                    "source_type": "review_summary",
                    "summary": reviewer_payload.get("summary") or {},
                },
                source_refs=[{"type": "screen_review", "run_id": context.run_id}],
                confidence=1.0,
                risk_flags=[],
            )
            self._add_entry(context, request, intent, summary_entry)
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=intent,
                event_type="review_completed",
                phase=phase,
                agent=agent,
                message="Screen reviewer completed",
                payload=reviewer_payload.get("summary") or {},
            )
        elif agent == "reporter":
            evidences = []
            retrieval_summary = None
            for entry in context.blackboard.list_entries():
                if entry.kind == "retrieved_evidence":
                    evidences.extend(entry.payload.get("evidences", []))
                    retrieval_summary = entry.payload.get("retrieval_summary")
            payload = self.reporter.run(
                intent=intent,
                query=request.user_query,
                evidences=evidences,
                retrieval_summary=retrieval_summary,
            )
            payload["debug"] = self._build_debug_payload(
                context=context,
                request=request,
                plan=plan,
                plan_intent=intent,
                retrieval_summary=retrieval_summary,
            )
            self._extend_subagent_trace(
                context,
                agent=agent,
                phase=phase,
                payload={
                    "subagent_trace": [
                        {
                            "subagent_type": "report_composer",
                            "status": "completed",
                            "objective": "Compose final response payload from evidences.",
                            "notes": [f"evidence_count={len(evidences)}", f"has_review_summary={str(bool(payload.get('review_summary'))).lower()}"],
                        }
                    ]
                },
            )
            entry = BlackboardEntry(
                run_id=context.run_id,
                request_id=request.request_id,
                kind="report_fragment",
                agent=agent,
                phase=phase,
                payload=payload,
                source_refs=[{"type": "retrieval", "evidence_count": len(evidences), "run_id": context.run_id}],
                confidence=0.7,
                risk_flags=[],
            )
            self._add_entry(context, request, intent, entry)
            write_json(context.paths.result, payload)
        else:
            raise OrchestratorError(f"unsupported agent: {agent}")

        self._enforce_runtime_controls(context, request_id=request.request_id, intent=intent)
        self._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="agent_completed",
            phase=phase,
            agent=agent,
            message=f"Agent completed: {agent}",
        )
        self._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="phase_completed",
            phase=phase,
            agent=agent,
            message=f"Phase completed: {phase}",
        )
        self._update_state(
            context,
            pending_nodes=[item.node_id for item in pending_actions_after_current],
            completed_nodes=completed_node_ids + [action.node_id],
        )
        context.checkpoint_store.save(context.state)

    def _add_entry(self, context: RuntimeContext, request: RequestEnvelope, intent: str, entry: BlackboardEntry) -> None:
        context.blackboard.add(entry)
        self._emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="blackboard_entry_added",
            phase=entry.phase,
            agent=entry.agent,
            message=f"Blackboard entry added: {entry.kind}",
            payload={"entry_id": entry.entry_id, "kind": entry.kind},
        )

    def _emit_event(
        self,
        context: RuntimeContext,
        *,
        request_id: str,
        intent: str,
        event_type: str,
        message: str,
        phase: str | None = None,
        agent: str | None = None,
        payload: dict | None = None,
    ) -> None:
        event = RunEvent(
            request_id=request_id,
            run_id=context.run_id,
            intent=intent,
            event_type=event_type,
            phase=phase,
            agent=agent,
            message=message,
            payload=payload,
        )
        context.event_stream.emit(event)
        if self.event_listener is not None:
            self.event_listener(event)
        self._update_state(context, last_event_id=event.event_id)

    def _transition(
        self,
        context: RuntimeContext,
        status: str,
        *,
        request_id: str,
        intent: str,
        message: str,
        error_summary: str | None = None,
    ) -> None:
        context.state.status = status  # type: ignore[assignment]
        if error_summary is not None:
            context.state.error_summary = error_summary
        context.state.updated_at = context.state.updated_at.__class__.now(context.state.updated_at.tzinfo)
        write_json(context.paths.run_state, context.state.model_dump(mode="json"))
        context.checkpoint_store.save(context.state)
        context.logger.log(context.run_id, "INFO", "orchestrator", message, {"status": status})

    def _update_state(
        self,
        context: RuntimeContext,
        *,
        current_phase: str | None = None,
        active_agent: str | None = None,
        pending_nodes: list[str] | None = None,
        completed_nodes: list[str] | None = None,
        last_event_id: str | None = None,
    ) -> None:
        if current_phase is not None:
            context.state.current_phase = current_phase
        if active_agent is not None:
            context.state.active_agent = active_agent
        if pending_nodes is not None:
            context.state.pending_nodes = pending_nodes
        if completed_nodes is not None:
            context.state.completed_nodes = completed_nodes
        if last_event_id is not None:
            context.state.last_event_id = last_event_id
        write_json(context.paths.run_state, context.state.model_dump(mode="json"))

    def _build_response(
        self,
        *,
        request: RequestEnvelope,
        run_id: str,
        intent: str,
        status: str,
        message: str,
        result: dict | None,
        phase: str | None,
        active_agent: str | None,
        latest_event: str | None,
        percent: int,
        errors: list[str] | None = None,
    ) -> ResponseEnvelope:
        progress = self.progress_service.build(
            current_phase=phase,
            active_agent=active_agent,
            latest_event=latest_event,
            status_message=message,
            percent=percent,
        )
        return ResponseEnvelope(
            request_id=request.request_id,
            run_id=run_id,
            intent=intent,
            status=status,  # type: ignore[arg-type]
            message=message,
            progress=progress,
            result=result,
            errors=errors or [],
        )

    def _load_result_payload(self, result_path: Path) -> dict | None:
        if not result_path.exists():
            return None
        import json

        with result_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _load_json_payload(self, path: Path) -> dict | None:
        import json

        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            content = handle.read().strip()
            if not content:
                return None
            return json.loads(content)

    def _extend_subagent_trace(self, context: RuntimeContext, *, agent: str, phase: str, payload: dict | None) -> None:
        trace_items = list((payload or {}).get("subagent_trace") or [])
        for item in trace_items:
            if not isinstance(item, dict):
                continue
            record = dict(item)
            record.setdefault("agent", agent)
            record.setdefault("phase", phase)
            context.subagent_trace.append(record)
            subagent_type = str(record.get("subagent_type") or agent).strip()
            status = str(record.get("status") or "completed").strip()
            objective = str(record.get("objective") or "").strip()
            message = f"{subagent_type} [{status}]"
            if objective:
                message = f"{message}: {objective}"
            self._emit_event(
                context,
                request_id=context.state.request_id,
                intent=context.state.intent,
                event_type="subagent_trace",
                phase=phase,
                agent=agent,
                message=message,
                payload=record,
            )

    def _build_debug_payload(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        plan_intent: str,
        retrieval_summary: str | None,
    ) -> dict:
        request_dispatch_trace = None
        if isinstance(request.constraints, dict):
            candidate = request.constraints.get("chat_dispatch")
            if isinstance(candidate, dict):
                request_dispatch_trace = dict(candidate)
        return {
            "intent": plan_intent,
            "dispatch_trace": request_dispatch_trace or dict(plan.dispatch_trace or {}),
            "entry_strategy": plan.entry_strategy,
            "entry_reason": plan.entry_reason,
            "subagent_required": plan.subagent_required,
            "subagent_trace": context.subagent_trace[:],
            "dynamic_trace": context.dynamic_trace[:],
            "retrieval_summary": retrieval_summary,
        }

    def _enforce_runtime_controls(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        if context.control_plane.cancellation_requested():
            self._transition(context, "cancelled", request_id=request_id, intent=intent, message="Run cancelled by user")
            self._emit_event(context, request_id=request_id, intent=intent, event_type="run_cancelled", message="Run cancelled")
            raise RunCancelledError("run_cancelled")
        if context.control_plane.run_timed_out():
            self._transition(context, "timed_out", request_id=request_id, intent=intent, message="Run timed out")
            self._emit_event(context, request_id=request_id, intent=intent, event_type="run_timed_out", message="Run timed out")
            raise RunTimedOutError("run_timed_out")

    def _wait_for_external_dependency(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        import time

        while True:
            if context.control_plane.cancellation_requested():
                self._transition(context, "cancelled", request_id=request_id, intent=intent, message="Run cancelled while waiting")
                self._emit_event(context, request_id=request_id, intent=intent, event_type="run_cancelled", message="Run cancelled while waiting")
                raise RunCancelledError("run_cancelled")
            if context.control_plane.external_wait_timed_out() or context.control_plane.run_timed_out():
                self._transition(context, "timed_out", request_id=request_id, intent=intent, message="External wait timed out")
                self._emit_event(context, request_id=request_id, intent=intent, event_type="run_timed_out", message="External wait timed out")
                raise RunTimedOutError("run_timed_out")
            time.sleep(0.2)

