from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from research_system.runtime.proposal_store import ProposalStore
from research_system.runtime.planner import RuntimePlanner
from research_system.runtime.supervisor import RuntimeSupervisor
from research_system.runtime.jsonl import read_jsonl
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.runtime import BlackboardEntry, RunEvent, RunStateSnapshot
from research_system.schemas.proposals import ProposalReviewEvent
from research_system.services.memory_service import MemoryService
from research_system.services.methodology_evolver import MethodologyEvolver
from research_system.services.methodology_manifest_service import MethodologyManifestService
from research_system.services.methodology_memory_writer import MethodologyMemoryWriter
from research_system.services.proposal_payloads import proposal_detail_payload, proposal_display_summary, proposal_summary_row
from research_system.services.retrieval_policy_service import RetrievalPolicyService
from research_system.services.progress_service import ProgressService
from research_system.services.methodologies.cashflow_resilience_service import CashflowResilienceService
from research_system.services.methodologies.comps_service import CompsService
from research_system.services.methodologies.dcf_service import DCFService
from research_system.services.proposal_generator import ProposalGenerator
from research_system.services.task_router import TaskRouter
from research_system.adapters.vendors.akshare_utils import coerce_numeric
from research_system.adapters.storage.mem0_adapter import Mem0Adapter
from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation


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
        self.task_router = TaskRouter(self.llm_provider, settings=settings)
        self.progress_service = ProgressService()
        self.skill_runtime = SkillRuntime(settings)
        yaml_configs = settings.load_yaml_configs()
        self.collector = CollectorService(self.skill_runtime, settings.runs_root)
        self.distiller = DistillerService(self.llm_provider, settings)
        self.critic = CriticService()
        self.reviewer = ReviewerService(yaml_configs.get("review_policy"))
        self.retriever = RetrieverService(self.llm_provider, settings, yaml_configs.get("retrieval_policy"))
        self.reporter = ReporterService(self.llm_provider)
        self.screener = ScreenerService()
        self.screen_reviewer = ScreenReviewerService()
        self.comps_service = CompsService()
        self.dcf_service = DCFService()
        self.cashflow_resilience_service = CashflowResilienceService()
        self.proposal_store = ProposalStore(settings.proposals_root)
        self.methodology_evolver = MethodologyEvolver()
        self.proposal_generator = ProposalGenerator()
        self.methodology_manifest_service = MethodologyManifestService(settings)
        self.retrieval_policy_service = RetrievalPolicyService(settings)
        self.memory_service = MemoryService(Mem0Adapter(settings))
        self.methodology_memory_writer = MethodologyMemoryWriter(self.memory_service)
        self.gates = GateEvaluator()
        self.retention = RunRetentionService(settings)
        self.planner = RuntimePlanner(self.llm_provider)
        self.supervisor = RuntimeSupervisor()
        self._attach_subagent_observers()

    def close(self) -> None:
        close = getattr(self.retriever, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "ResearchOrchestrator":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _attach_subagent_observers(self) -> None:
        for service in (
            self.collector,
            self.distiller,
            self.critic,
            self.retriever,
            self.reporter,
        ):
            registry = getattr(service, "registry", None)
            if registry is None or not hasattr(registry, "add_observer"):
                continue
            registry.add_observer(self._handle_subagent_registry_event)

    def run(self, request: RequestEnvelope) -> ResponseEnvelope:
        decision = self.task_router.identify(request)
        if decision.trace and isinstance(request.constraints, dict):
            request.constraints["routing_trace"] = dict(decision.trace)
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
                result={"routing_trace": decision.trace} if decision.trace else None,
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
        self._active_runtime_context = context

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
                requires_verified_snapshot=intent != "methodology_evolution",
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
        finally:
            self._active_runtime_context = None

    def replay(self, run_id: str) -> dict:
        paths = self.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).replay()

    def history(self) -> list[dict]:
        return self.run_store.list_runs()

    def list_proposals(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            proposals = self.proposal_store.list(status)
            proposals.sort(key=lambda item: item.updated_at, reverse=True)
        else:
            proposals = self.proposal_store.list_all()
        return [self._proposal_summary_row(item) for item in proposals[:limit]]

    def show_proposal(self, proposal_id: str) -> dict:
        located = self.proposal_store.find(proposal_id)
        if located is None:
            raise OrchestratorError(f"proposal not found: {proposal_id}")
        proposal, stored_status = located
        return proposal_detail_payload(proposal, stored_status=stored_status)

    def review_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        reviewer: str = "manual_cli_review",
        reason: str | None = None,
    ) -> dict:
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"approve", "reject"}:
            raise OrchestratorError(f"unsupported proposal decision: {decision}")
        located = self.proposal_store.find(proposal_id)
        if located is None:
            raise OrchestratorError(f"proposal not found: {proposal_id}")
        proposal, previous_status = located
        if proposal.status in {"approved", "rejected"}:
            current = self.show_proposal(proposal.proposal_id)
            return {
                "proposal_id": proposal.proposal_id,
                "status": proposal.status,
                "message": f"proposal already {proposal.status}",
                "display_summary": f"{proposal.proposal_id} already {proposal.status}",
                "result": current,
            }

        reviewed_at = datetime.now(timezone.utc)
        next_status = "approved" if normalized_decision == "approve" else "rejected"
        self._append_proposal_review_event(
            proposal,
            decision=next_status,
            reviewer=reviewer,
            reason=str(reason or f"manual_{next_status}_via_cli"),
            previous_status=previous_status,
            reviewed_at=reviewed_at,
            source="manual_cli_review",
        )
        proposal.status = next_status
        proposal.reviewer = reviewer
        proposal.review_reason = str(reason or f"manual_{proposal.status}_via_cli")
        proposal.reviewed_at = reviewed_at
        proposal.updated_at = reviewed_at
        apply_outputs = self._persist_proposal_transition(
            proposal,
            previous_status=previous_status,
            apply_approved_side_effects=proposal.status == "approved",
        )

        return {
            "proposal_id": proposal.proposal_id,
            "status": proposal.status,
            "message": f"proposal {proposal.status}",
            "display_summary": proposal_display_summary(
                {
                    "proposal_id": proposal.proposal_id,
                    "status": proposal.status,
                    "proposal_type": proposal.proposal_type,
                    "title": proposal.title,
                }
            ),
            "result": {
                **self.show_proposal(proposal.proposal_id),
                **apply_outputs,
            },
        }

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
        status = run_state_payload.get("status") if isinstance(run_state_payload, dict) else None
        active_run = status in {"received", "identifying_intent", "ready", "running", "waiting_external", "gated"}
        if active_run and run_state_payload:
            run_state_payload["status"] = "cancelled"
            write_json(paths.run_state, run_state_payload)
        if active_run:
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
        payload_status = "cancelled" if active_run else str(status or "unknown")
        payload_message = "Cancellation requested." if active_run else "Run is already terminal; no cancellation was applied."
        payload = {
            "run_id": run_id,
            "status": payload_status,
            "message": payload_message,
            "control_state": control_state.to_dict() if control_state else None,
            "cancellation_requested": active_run,
        }
        if active_run:
            write_json(paths.summary, payload)
        return payload

    def cleanup(self) -> dict:
        return self.retention.cleanup_artifacts(self.settings.runs_root)

    def _proposal_summary_row(self, proposal) -> dict:
        return proposal_summary_row(proposal)

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
            if intent == "methodology_evolution":
                evolution_entries = self._run_methodology_evolution_collect(
                    context=context,
                    request=request,
                    phase=phase,
                    agent=agent,
                )
                for entry in evolution_entries:
                    self._add_entry(context, request, intent, entry)
            else:
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
                if intent in {"stock_check", "stock_compare"}:
                    methodology_entries = self._build_stock_methodology_entries(
                        context=context,
                        request=request,
                        intent=intent,
                        phase=phase,
                        agent=agent,
                    )
                    for entry in methodology_entries:
                        self._add_entry(context, request, intent, entry)
        elif agent == "retriever":
            blackboard_entries = [entry.model_dump(mode="json") for entry in context.blackboard.list_entries()]
            payload = self.retriever.run(
                intent=intent,
                query=request.user_query,
                blackboard_entries=blackboard_entries,
                planning_context_hints=dict(action.inputs.get("planning_context_hints") or {}),
            )
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
            screener_constraints = dict(request.constraints)
            screener_constraints.update(action.inputs or {})
            payload = self.screener.run(
                query=request.user_query,
                source_payloads=source_payloads,
                snapshot_payloads=snapshot_payloads,
                constraints=screener_constraints,
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
                        snapshot_types=["market", "fundamental", "notice", "lhb", "capital_flow", "sentiment", "events"],
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
                    enriched_constraints = dict(screener_constraints)
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
                for methodology_entry in self._build_candidate_methodology_entries(
                    context=context,
                    request=request,
                    phase=phase,
                    agent=agent,
                    candidate=candidate,
                    source_type="screen_candidate",
                ):
                    self._add_entry(context, request, intent, methodology_entry)
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
                for methodology_entry in self._build_candidate_methodology_entries(
                    context=context,
                    request=request,
                    phase=phase,
                    agent=agent,
                    candidate=candidate,
                    source_type="watchlist_candidate",
                ):
                    self._add_entry(context, request, intent, methodology_entry)
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
                planning_context_hints=dict(action.inputs.get("planning_context_hints") or {}),
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
            for entry in self._build_review_methodology_entries(
                context=context,
                request=request,
                intent=intent,
                phase=phase,
                agent=agent,
                review_request=review_request or {},
                latest_market_snapshot=latest_market_snapshot,
                latest_fundamental_snapshot=latest_fundamental_snapshot,
            ):
                self._add_entry(context, request, intent, entry)
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
                planning_context_hints=dict(action.inputs.get("planning_context_hints") or {}),
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
                planning_context_hints=dict(action.inputs.get("planning_context_hints") or {}),
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

    def _handle_subagent_registry_event(
        self,
        *,
        stage: str,
        invocation: SubagentInvocation,
        record: SubagentExecutionRecord | None,
        outputs: dict | None,
        error: Exception | None,
    ) -> None:
        runtime_context = getattr(self, "_active_runtime_context", None)
        if runtime_context is None:
            return
        context: RuntimeContext = runtime_context
        phase = context.state.current_phase
        agent = context.state.active_agent
        payload = {
            "subagent_type": invocation.subagent_type,
            "objective": invocation.objective,
            "status": record.status if record is not None else ("failed" if stage == "failed" else "running"),
            "notes": list((record.notes if record is not None else []) or []),
            "output_keys": list((record.output_keys if record is not None else []) or []),
        }
        if error is not None and not payload["notes"]:
            payload["notes"] = [f"{error.__class__.__name__}: {error}"]
        event_type = {
            "started": "subagent_started",
            "completed": "subagent_completed",
            "failed": "subagent_failed",
        }.get(stage, "subagent_trace")
        message = f"{invocation.subagent_type} {stage}"
        self._emit_event(
            context,
            request_id=context.state.request_id,
            intent=context.state.intent,
            event_type=event_type,
            phase=phase,
            agent=agent,
            message=message,
            payload=payload,
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
        request_dispatch_trace: dict | None = None
        if isinstance(request.constraints, dict):
            routing_trace = request.constraints.get("routing_trace")
            chat_dispatch = request.constraints.get("chat_dispatch")
            if isinstance(routing_trace, dict):
                request_dispatch_trace = dict(routing_trace)
            if isinstance(chat_dispatch, dict):
                request_dispatch_trace = {**(request_dispatch_trace or {}), **dict(chat_dispatch)}
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

    def _build_candidate_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
        candidate: dict,
        source_type: str,
    ) -> list[BlackboardEntry]:
        methodology = candidate.get("methodology") or {}
        comps_payload = methodology.get("comps")
        if not isinstance(comps_payload, dict):
            return []
        symbol = str(candidate.get("symbol") or "").strip()
        if not symbol:
            return []
        return [
            self._methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="comps",
                title=f"methodology_comps:{symbol}",
                payload=comps_payload,
                target_symbol=symbol,
                source_refs=[{"type": source_type, "symbol": symbol, "run_id": context.run_id}],
            )
        ]

    def _build_stock_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
    ) -> list[BlackboardEntry]:
        source_records = [
            entry for entry in context.blackboard.list_entries()
            if entry.kind == "source_record"
        ]
        snapshots = {
            str(entry.payload.get("snapshot_type") or ""): entry.payload
            for entry in context.blackboard.list_entries()
            if entry.kind == "verified_snapshot"
        }
        cycle = self._infer_investment_cycle(request.user_query)
        entries: list[BlackboardEntry] = []
        compare_request_entry = next(
            (
                entry for entry in source_records
                if str((entry.payload or {}).get("source_type") or "") == "compare_request"
            ),
            None,
        )
        if compare_request_entry is not None:
            compare_targets = list(((compare_request_entry.payload if compare_request_entry else {}) or {}).get("compare_targets") or [])
            snapshot_groups = self._group_snapshots_by_symbol(context)
            peer_candidates = [
                self._methodology_candidate_payload(
                    symbol=str(target.get("symbol") or "").strip(),
                    name=str(target.get("name") or target.get("symbol") or "").strip(),
                    snapshots=snapshot_groups.get(str(target.get("symbol") or "").strip(), {}),
                    sector=((compare_request_entry.payload if compare_request_entry else {}) or {}).get("sector"),
                )
                for target in compare_targets
                if str(target.get("symbol") or "").strip()
            ]
            for candidate in peer_candidates:
                symbol = str(candidate.get("symbol") or "").strip()
                if not symbol:
                    continue
                candidate_snapshots = snapshot_groups.get(symbol, {})
                market_snapshot = candidate_snapshots.get("market")
                fundamental_snapshot = candidate_snapshots.get("fundamental")
                if market_snapshot is None and fundamental_snapshot is None:
                    continue
                peers = [peer for peer in peer_candidates if str(peer.get("symbol") or "").strip() != symbol]
                entries.extend(
                    self._build_methodology_entries_for_symbol(
                        context=context,
                        request=request,
                        phase=phase,
                        agent=agent,
                        intent=intent,
                        symbol=symbol,
                        candidate_name=str(candidate.get("name") or symbol),
                        sector=((compare_request_entry.payload if compare_request_entry else {}) or {}).get("sector"),
                        cycle=cycle,
                        market_snapshot=market_snapshot,
                        fundamental_snapshot=fundamental_snapshot,
                        peers=peers,
                    )
                )
            scorecard_entry = self._build_bank_compare_scorecard_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                compare_targets=compare_targets,
                peer_candidates=peer_candidates,
            )
            if scorecard_entry is not None:
                entries.append(scorecard_entry)
            return entries

        request_entry = next(
            (
                entry for entry in source_records
                if str((entry.payload or {}).get("source_type") or "") in {"user_query", "stock_check_replenish", "review_request"}
            ),
            None,
        )
        symbol = str(((request_entry.payload if request_entry else {}) or {}).get("symbol") or "").strip()
        if not symbol:
            return []
        market_snapshot = snapshots.get("market")
        fundamental_snapshot = snapshots.get("fundamental")
        if market_snapshot is None and fundamental_snapshot is None:
            return []
        entries.extend(
            self._build_methodology_entries_for_symbol(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                intent=intent,
                symbol=symbol,
                candidate_name=symbol,
                sector=((request_entry.payload if request_entry else {}) or {}).get("sector"),
                cycle=cycle,
                market_snapshot=market_snapshot,
                fundamental_snapshot=fundamental_snapshot,
                peers=[],
            )
        )
        return entries

    def _group_snapshots_by_symbol(self, context: RuntimeContext) -> dict[str, dict[str, dict]]:
        grouped: dict[str, dict[str, dict]] = {}
        for entry in context.blackboard.list_entries():
            if entry.kind != "verified_snapshot":
                continue
            symbol = str(entry.payload.get("symbol") or "").strip()
            snapshot_type = str(entry.payload.get("snapshot_type") or "").strip()
            if not symbol or not snapshot_type:
                continue
            grouped.setdefault(symbol, {})[snapshot_type] = entry.payload
        return grouped

    def _methodology_candidate_payload(
        self,
        *,
        symbol: str,
        name: str,
        snapshots: dict[str, dict],
        sector,
    ) -> dict:
        market_snapshot = snapshots.get("market")
        fundamental_snapshot = snapshots.get("fundamental")
        banking_metrics = self._banking_metrics(fundamental_snapshot)
        return {
            "symbol": symbol,
            "name": name or symbol,
            "sector": sector,
            "latest_price": self._methodology_market_price(market_snapshot),
            "latest_pct_change": self._methodology_market_pct_change(market_snapshot),
            "latest_amount": self._methodology_market_amount(market_snapshot),
            "pb": self._methodology_market_pb(market_snapshot),
            "pe_ttm": self._methodology_market_pe_ttm(market_snapshot),
            "market_cap": self._methodology_market_cap(market_snapshot),
            "float_market_cap": self._methodology_market_float_market_cap(market_snapshot),
            "market_total_shares": self._methodology_market_total_shares(market_snapshot),
            "market_float_shares": self._methodology_market_float_shares(market_snapshot),
            "revenue_proxy": self._methodology_revenue_proxy(fundamental_snapshot),
            "banking_metrics": banking_metrics,
            "bank_like": bool(banking_metrics.get("is_bank_like")),
            "score": 0.0,
        }

    def _build_methodology_entries_for_symbol(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
        intent: str,
        symbol: str,
        candidate_name: str,
        sector,
        cycle: str,
        market_snapshot: dict | None,
        fundamental_snapshot: dict | None,
        peers: list[dict],
    ) -> list[BlackboardEntry]:
        comps_payload = self.comps_service.build_for_candidate(
            candidate={
                "symbol": symbol,
                "name": candidate_name,
                "sector": sector,
                "latest_price": self._methodology_market_price(market_snapshot),
                "latest_pct_change": self._methodology_market_pct_change(market_snapshot),
                "latest_amount": self._methodology_market_amount(market_snapshot),
                "pb": self._methodology_market_pb(market_snapshot),
                "pe_ttm": self._methodology_market_pe_ttm(market_snapshot),
                "market_cap": self._methodology_market_cap(market_snapshot),
                "float_market_cap": self._methodology_market_float_market_cap(market_snapshot),
                "revenue_proxy": self._methodology_revenue_proxy(fundamental_snapshot),
                "score": 0.0,
            },
            peers=peers,
            cycle=cycle,
            sector_context={"sector": sector},
        )
        dcf_payload = self.dcf_service.build_for_stock(
            symbol=symbol,
            fundamental_snapshot=fundamental_snapshot,
            market_snapshot=market_snapshot,
            cycle=cycle,
        )
        resilience_payload = self.cashflow_resilience_service.build_for_stock(
            symbol=symbol,
            fundamental_snapshot=fundamental_snapshot,
            market_snapshot=market_snapshot,
            cycle=cycle,
        )
        return [
            self._methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="comps",
                title=f"methodology_comps:{symbol}",
                payload=comps_payload,
                target_symbol=symbol,
                source_refs=[{"type": f"{intent}_methodology", "run_id": context.run_id}],
            ),
            self._methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="dcf",
                title=f"methodology_dcf:{symbol}",
                payload=dcf_payload,
                target_symbol=symbol,
                source_refs=[{"type": f"{intent}_methodology", "run_id": context.run_id}],
            ),
            self._methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="cashflow_resilience",
                title=f"methodology_cashflow_resilience:{symbol}",
                payload=resilience_payload,
                target_symbol=symbol,
                source_refs=[{"type": f"{intent}_methodology", "run_id": context.run_id}],
            ),
        ]

    def _build_review_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
        review_request: dict,
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
    ) -> list[BlackboardEntry]:
        symbol = str(review_request.get("symbol") or "").strip()
        if not symbol:
            return []
        cycle = self._infer_investment_cycle(request.user_query)
        dcf_payload = self.dcf_service.build_for_stock(
            symbol=symbol,
            fundamental_snapshot=latest_fundamental_snapshot,
            market_snapshot=latest_market_snapshot,
            cycle=cycle,
        )
        resilience_payload = self.cashflow_resilience_service.build_for_stock(
            symbol=symbol,
            fundamental_snapshot=latest_fundamental_snapshot,
            market_snapshot=latest_market_snapshot,
            cycle=cycle,
        )
        return [
            self._methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="dcf",
                title=f"methodology_dcf:{symbol}",
                payload=dcf_payload,
                target_symbol=symbol,
                source_refs=[{"type": f"{intent}_review_methodology", "run_id": context.run_id}],
            ),
            self._methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="cashflow_resilience",
                title=f"methodology_cashflow_resilience:{symbol}",
                payload=resilience_payload,
                target_symbol=symbol,
                source_refs=[{"type": f"{intent}_review_methodology", "run_id": context.run_id}],
            ),
        ]

    def _run_methodology_evolution_collect(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
    ) -> list[BlackboardEntry]:
        target_run_ids = self._resolve_methodology_target_run_ids(request)
        methodology_memory_rows = self.memory_service.read_recent_methodology_memory(limit_per_type=3)
        search_terms: list[str] = [str(request.user_query or "").strip()]
        run_inputs = [
            {
                "run_id": target_run_id,
                "blackboard_entries": read_jsonl(self.run_store.load_run_paths(target_run_id).blackboard),
            }
            for target_run_id in target_run_ids
        ]
        for run_input in run_inputs:
            for entry in list(run_input.get("blackboard_entries") or []):
                payload = entry.get("payload") or {}
                if entry.get("kind") == "distilled_dimension":
                    candidate_title = str(payload.get("canonical_dimension_name") or payload.get("dimension_name") or "").strip()
                    if candidate_title:
                        search_terms.append(candidate_title)
                if entry.get("kind") == "source_record" and str(payload.get("source_type") or "").startswith("methodology_"):
                    candidate_title = str(payload.get("title") or payload.get("methodology_type") or "").strip()
                    if candidate_title:
                        search_terms.append(candidate_title)
        methodology_memory_search_rows = self.memory_service.search_methodology_memory(
            query="\n".join(term for term in search_terms if term),
            limit=12,
        )
        candidates = self.methodology_evolver.build_candidates_from_runs(
            runs=run_inputs,
            methodology_memory_rows=methodology_memory_rows,
            methodology_memory_search_rows=methodology_memory_search_rows,
        )
        proposals = self.proposal_generator.build(author_agent=agent, candidates=candidates)
        auto_approve = bool(request.constraints.get("auto_approve", True))
        entries: list[BlackboardEntry] = []

        entries.append(
            BlackboardEntry(
                run_id=context.run_id,
                request_id=request.request_id,
                kind="source_record",
                agent=agent,
                phase=phase,
                payload={
                    "title": "methodology_evolution_request",
                    "content": request.user_query,
                    "source_type": "methodology_evolution_request",
                    "target_run_id": target_run_ids[0] if len(target_run_ids) == 1 else None,
                    "target_run_ids": target_run_ids,
                    "candidate_count": len(candidates),
                    "proposal_count": len(proposals),
                },
                source_refs=[
                    {
                        "type": "request",
                        "target_run_id": target_run_ids[0] if len(target_run_ids) == 1 else None,
                        "target_run_ids": target_run_ids,
                        "run_id": context.run_id,
                    }
                ],
            )
        )

        for proposal in proposals:
            previous_status = proposal.status
            self.proposal_store.create(proposal)
            apply_outputs = {
                "manifest": None,
                "retrieval_policy": None,
                "memory_record": None,
            }
            approval_decision = self._evaluate_methodology_auto_approval(
                proposal=proposal,
                constraints=request.constraints,
            )
            if auto_approve and approval_decision["approved"]:
                reviewed_at = proposal.updated_at.__class__.now(proposal.updated_at.tzinfo)
                self._append_proposal_review_event(
                    proposal,
                    decision="approved",
                    reviewer="system_auto_approve",
                    reason=str(approval_decision["reason"] or "auto_approve_for_methodology_evolution"),
                    previous_status=previous_status,
                    reviewed_at=reviewed_at,
                    source="auto_gate",
                )
                proposal.status = "approved"
                proposal.reviewer = "system_auto_approve"
                proposal.review_reason = str(approval_decision["reason"] or "auto_approve_for_methodology_evolution")
                proposal.reviewed_at = reviewed_at
                proposal.updated_at = proposal.reviewed_at
                apply_outputs = self._persist_proposal_transition(
                    proposal,
                    previous_status=previous_status,
                    apply_approved_side_effects=True,
                )
            elif auto_approve:
                reviewed_at = proposal.updated_at.__class__.now(proposal.updated_at.tzinfo)
                self._append_proposal_review_event(
                    proposal,
                    decision="pending",
                    reviewer="system_threshold_gate",
                    reason=str(approval_decision["reason"] or "pending_manual_review"),
                    previous_status=previous_status,
                    reviewed_at=reviewed_at,
                    source="auto_gate",
                )
                proposal.reviewer = "system_threshold_gate"
                proposal.review_reason = str(approval_decision["reason"] or "pending_manual_review")
                proposal.reviewed_at = reviewed_at
                proposal.updated_at = reviewed_at
                apply_outputs = self._persist_proposal_transition(
                    proposal,
                    previous_status=previous_status,
                    apply_approved_side_effects=False,
                )

            entries.append(
                BlackboardEntry(
                    run_id=context.run_id,
                    request_id=request.request_id,
                    kind="proposal_draft",
                    agent=agent,
                    phase=phase,
                    payload={
                        "title": proposal.title,
                        "source_type": "proposal_draft",
                        "proposal": proposal.model_dump(mode="json"),
                        "approval_decision": approval_decision,
                        **apply_outputs,
                    },
                    source_refs=[
                        {
                            "type": "methodology_evolution",
                            "target_run_id": target_run_ids[0] if len(target_run_ids) == 1 else None,
                            "target_run_ids": target_run_ids,
                            "run_id": context.run_id,
                        }
                    ],
                    confidence=1.0,
                    risk_flags=[],
                )
            )
        return entries

    def _persist_proposal_transition(
        self,
        proposal,
        *,
        previous_status: str,
        apply_approved_side_effects: bool,
    ) -> dict[str, object]:
        self.proposal_store.save(proposal, previous_status=previous_status)
        outputs: dict[str, object] = {
            "manifest": None,
            "retrieval_policy": None,
            "memory_record": None,
        }
        if not apply_approved_side_effects:
            return outputs
        outputs["manifest"] = self.methodology_manifest_service.approve(proposal)
        outputs["retrieval_policy"] = self.retrieval_policy_service.approve(proposal)
        outputs["memory_record"] = self.methodology_memory_writer.write_from_proposal(proposal)
        return outputs

    def _evaluate_methodology_auto_approval(
        self,
        *,
        proposal,
        constraints: dict,
    ) -> dict[str, object]:
        if bool(constraints.get("force_auto_approve", False)):
            return {"approved": True, "reason": "force_auto_approve_override"}

        proposal_type = str(proposal.proposal_type or "")
        sample_size = int(proposal.sample_size or 0)
        min_dimension_sample_size = int(constraints.get("min_dimension_sample_size") or 2)
        min_valuation_sample_size = int(constraints.get("min_valuation_sample_size") or 2)
        min_review_pattern_sample_size = int(constraints.get("min_review_pattern_sample_size") or 2)
        min_dimension_confidence = float(constraints.get("min_dimension_average_confidence") or 0.5)
        min_valuation_confidence = float(constraints.get("min_valuation_average_confidence") or 0.5)
        max_memory_support_age_days = int(constraints.get("max_memory_support_age_days") or 180)
        min_dimension_memory_support_entity_scope_count = int(
            constraints.get("min_dimension_memory_support_entity_scope_count") or 2
        )
        min_valuation_memory_support_entity_scope_count = int(
            constraints.get("min_valuation_memory_support_entity_scope_count") or 2
        )

        if proposal_type == "MethodologyTemplatePatch":
            if sample_size < min_dimension_sample_size:
                return {
                    "approved": False,
                    "reason": f"pending_manual_review_dimension_sample_size<{min_dimension_sample_size}",
                }
            average_confidence = float((proposal.before_metrics or {}).get("average_confidence") or 0.0)
            if average_confidence < min_dimension_confidence:
                return {
                    "approved": False,
                    "reason": f"pending_manual_review_dimension_confidence<{min_dimension_confidence}",
                }
            staleness_reason = self._stale_memory_support_reason(
                proposal=proposal,
                max_age_days=max_memory_support_age_days,
            )
            if staleness_reason:
                return {"approved": False, "reason": staleness_reason}
            diversity_reason = self._insufficient_memory_support_reason(
                proposal=proposal,
                min_entity_scope_count=min_dimension_memory_support_entity_scope_count,
            )
            if diversity_reason:
                return {"approved": False, "reason": diversity_reason}
            return {"approved": True, "reason": "auto_approve_dimension_threshold_met"}

        if proposal_type == "ValuationHeuristicPatch":
            if sample_size < min_valuation_sample_size:
                return {
                    "approved": False,
                    "reason": f"pending_manual_review_valuation_sample_size<{min_valuation_sample_size}",
                }
            average_confidence = float((proposal.before_metrics or {}).get("average_confidence") or 0.0)
            if average_confidence < min_valuation_confidence:
                return {
                    "approved": False,
                    "reason": f"pending_manual_review_valuation_confidence<{min_valuation_confidence}",
                }
            staleness_reason = self._stale_memory_support_reason(
                proposal=proposal,
                max_age_days=max_memory_support_age_days,
            )
            if staleness_reason:
                return {"approved": False, "reason": staleness_reason}
            diversity_reason = self._insufficient_memory_support_reason(
                proposal=proposal,
                min_entity_scope_count=min_valuation_memory_support_entity_scope_count,
            )
            if diversity_reason:
                return {"approved": False, "reason": diversity_reason}
            return {"approved": True, "reason": "auto_approve_valuation_threshold_met"}

        if proposal_type == "MemoryLearningUpdate":
            if sample_size < min_review_pattern_sample_size:
                return {
                    "approved": False,
                    "reason": f"pending_manual_review_review_pattern_sample_size<{min_review_pattern_sample_size}",
                }
            severity_counts = dict((proposal.before_metrics or {}).get("severity_counts") or {})
            verdict_counts = dict((proposal.after_metrics or {}).get("verdict_counts") or {})
            high_count = int(severity_counts.get("high") or 0)
            rejected_count = int(verdict_counts.get("rejected") or 0)
            if high_count <= 0 and rejected_count <= 0:
                return {
                    "approved": False,
                    "reason": "pending_manual_review_review_pattern_signal_too_weak",
                }
            return {"approved": True, "reason": "auto_approve_review_pattern_threshold_met"}

        return {"approved": False, "reason": f"pending_manual_review_unknown_proposal_type:{proposal_type or 'unknown'}"}

    def _stale_memory_support_reason(
        self,
        *,
        proposal,
        max_age_days: int,
    ) -> str | None:
        before_metrics = dict(getattr(proposal, "before_metrics", {}) or {})
        memory_support = dict(before_metrics.get("memory_support") or {})
        latest_recorded_at = str(memory_support.get("latest_recorded_at") or "").strip()
        context_count = int(memory_support.get("context_count") or 0)
        entity_scope_count = int(memory_support.get("entity_scope_count") or 0)
        if not latest_recorded_at or context_count <= 0 or entity_scope_count <= 0:
            return None
        try:
            recorded_at = datetime.fromisoformat(latest_recorded_at)
        except ValueError:
            return None
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        if recorded_at < cutoff:
            return f"pending_manual_review_memory_support_stale>{max_age_days}d"
        return None

    def _insufficient_memory_support_reason(
        self,
        *,
        proposal,
        min_entity_scope_count: int,
    ) -> str | None:
        if min_entity_scope_count <= 1:
            return None
        before_metrics = dict(getattr(proposal, "before_metrics", {}) or {})
        memory_support = dict(before_metrics.get("memory_support") or {})
        context_count = int(memory_support.get("context_count") or 0)
        entity_scope_count = int(memory_support.get("entity_scope_count") or 0)
        snapshot_count = int(memory_support.get("snapshot_count") or 0)
        if context_count <= 0:
            return None
        if entity_scope_count < min_entity_scope_count:
            return (
                "pending_manual_review_memory_support_entity_scope_count"
                f"<{min_entity_scope_count}"
            )
        if snapshot_count < min_entity_scope_count:
            return (
                "pending_manual_review_memory_support_snapshot_count"
                f"<{min_entity_scope_count}"
            )
        return None

    def _append_proposal_review_event(
        self,
        proposal,
        *,
        decision: str,
        reviewer: str,
        reason: str,
        previous_status: str | None,
        reviewed_at,
        source: str,
    ) -> None:
        history = list(getattr(proposal, "review_history", []) or [])
        history.append(
            ProposalReviewEvent(
                decision=decision,
                reviewer=reviewer,
                reason=reason,
                reviewed_at=reviewed_at,
                previous_status=previous_status,
                source=source,
            )
        )
        proposal.review_history = history

    def _resolve_methodology_target_run_ids(self, request: RequestEnvelope) -> list[str]:
        raw_target_run_ids = list(request.constraints.get("target_run_ids") or [])
        normalized: list[str] = []
        for item in raw_target_run_ids:
            run_id = str(item or "").strip()
            if run_id and run_id not in normalized:
                normalized.append(run_id)
        target_run_id = str(request.constraints.get("target_run_id") or "").strip()
        if target_run_id and target_run_id not in normalized:
            normalized.append(target_run_id)
        if not normalized:
            raise OrchestratorError("methodology_evolution requires constraints.target_run_id or constraints.target_run_ids")
        return normalized

    def _methodology_entry(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
        methodology_type: str,
        title: str,
        payload: dict,
        target_symbol: str,
        source_refs: list[dict],
    ) -> BlackboardEntry:
        valuation_band = payload.get("valuation_band") or {}
        output_payload = {
            "title": title,
            "content": str(payload),
            "source_type": f"methodology_{methodology_type}",
            "methodology_type": methodology_type,
            "symbol": target_symbol,
            "methodology_tags": [methodology_type, *list(payload.get("assumption_tags") or [])],
            "scenario_tags": list(payload.get("sensitivity_tags") or []),
            "peer_group_tags": list(payload.get("peer_group_tags") or []),
            "assumption_tags": list(payload.get("assumption_tags") or []),
            "sensitivity_tags": list(payload.get("sensitivity_tags") or []),
            "valuation_band": valuation_band,
            "valuation_band_label": self._valuation_band_label(valuation_band),
            "methodology_result": payload,
        }
        return BlackboardEntry(
            run_id=context.run_id,
            request_id=request.request_id,
            kind="source_record",
            agent=agent,
            phase=phase,
            payload=output_payload,
            source_refs=source_refs,
            confidence=payload.get("confidence"),
            risk_flags=list(payload.get("warnings") or []),
        )

    def _valuation_band_label(self, valuation_band: dict) -> str | None:
        if not isinstance(valuation_band, dict):
            return None
        low = valuation_band.get("low")
        base = valuation_band.get("base")
        high = valuation_band.get("high")
        if low is None and base is None and high is None:
            return None
        return f"{low}|{base}|{high}"

    def _infer_investment_cycle(self, query: str) -> str:
        lowered = query.lower()
        if "短线" in query or "short" in lowered:
            return "short_term"
        if "长期" in query or "long" in lowered:
            return "long_term"
        return "mid_term"

    def _methodology_market_price(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or overview
        for key in ("latest_price", "最新价", "close"):
            value = coerce_numeric(spot.get(key))
            if value is not None:
                return value
        latest_bar = (facts.get("market_hist") or {}).get("latest_bar") or facts.get("latest_bar") or {}
        for key in ("close", "收盘"):
            value = coerce_numeric(latest_bar.get(key))
            if value is not None:
                return value
        return None

    def _methodology_market_pct_change(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or overview
        for key in ("pct_change", "涨跌幅"):
            value = coerce_numeric(spot.get(key))
            if value is not None:
                return value
        latest_bar = (facts.get("market_hist") or {}).get("latest_bar") or facts.get("latest_bar") or {}
        for key in ("pct_change", "涨跌幅"):
            value = coerce_numeric(latest_bar.get(key))
            if value is not None:
                return value
        return None

    def _methodology_market_amount(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or overview
        for key in ("amount", "成交额"):
            value = coerce_numeric(spot.get(key))
            if value is not None:
                return value
        return None

    def _methodology_market_pb(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        for key in ("pb", "市净率"):
            value = coerce_numeric(overview.get(key))
            if value is not None:
                return value
        valuation = overview.get("valuation") or {}
        for key in ("市净率", "pb"):
            value = coerce_numeric(valuation.get(key))
            if value is not None:
                return value
        return None

    def _methodology_market_pe_ttm(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        for key in ("pe_ttm", "PE(TTM)"):
            value = coerce_numeric(overview.get(key))
            if value is not None:
                return value
        valuation = overview.get("valuation") or {}
        for key in ("PE(TTM)", "pe_ttm"):
            value = coerce_numeric(valuation.get(key))
            if value is not None:
                return value
        return None

    def _methodology_market_cap(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        for key in ("market_cap", "总市值"):
            value = coerce_numeric(overview.get(key))
            if value is not None:
                return value
        valuation = overview.get("valuation") or {}
        for key in ("总市值", "market_cap"):
            value = coerce_numeric(valuation.get(key))
            if value is not None:
                return value
        return None

    def _methodology_market_float_market_cap(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        for key in ("float_market_cap", "流通市值"):
            value = coerce_numeric(overview.get(key))
            if value is not None:
                return value
        valuation = overview.get("valuation") or {}
        for key in ("流通市值", "float_market_cap"):
            value = coerce_numeric(valuation.get(key))
            if value is not None:
                return value
        return None

    def _methodology_market_total_shares(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        for key in ("total_shares", "总股本"):
            value = coerce_numeric(overview.get(key))
            if value is not None:
                return value
        valuation = overview.get("valuation") or {}
        for key in ("总股本", "total_shares"):
            value = coerce_numeric(valuation.get(key))
            if value is not None:
                return value
        return None

    def _methodology_market_float_shares(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        for key in ("float_shares", "流通股本"):
            value = coerce_numeric(overview.get(key))
            if value is not None:
                return value
        valuation = overview.get("valuation") or {}
        for key in ("流通股本", "float_shares"):
            value = coerce_numeric(valuation.get(key))
            if value is not None:
                return value
        return None

    def _methodology_revenue_proxy(self, fundamental_snapshot: dict | None) -> float | None:
        facts = (fundamental_snapshot or {}).get("facts") or {}
        banking_metrics = facts.get("banking_metrics") or {}
        for key in ("interest_income", "deposits_and_deposits", "customer_loan_advance_net_addition"):
            value = coerce_numeric(banking_metrics.get(key))
            if value is not None:
                return value
        abstract = facts.get("financial_abstract") or {}
        for key in ("营业总收入", "营业收入", "营收", "总营收"):
            value = coerce_numeric(abstract.get(key))
            if value is not None:
                return value
        for item_key, item_value in abstract.items():
            key_text = str(item_key)
            if any(token in key_text for token in ("营业", "营收", "收入")):
                value = coerce_numeric(item_value)
                if value is not None:
                    return value
        return None

    def _banking_metrics(self, fundamental_snapshot: dict | None) -> dict:
        facts = (fundamental_snapshot or {}).get("facts") or {}
        metrics = facts.get("banking_metrics") or {}
        return dict(metrics) if isinstance(metrics, dict) else {}

    def _build_bank_compare_scorecard_entry(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
        compare_targets: list[dict],
        peer_candidates: list[dict],
    ) -> BlackboardEntry | None:
        if len(peer_candidates) < 2:
            return None
        bank_candidates = [item for item in peer_candidates if bool((item.get("banking_metrics") or {}).get("is_bank_like"))]
        if len(bank_candidates) < 2:
            return None
        scorecard = self._bank_compare_scorecard(compare_targets=compare_targets, peer_candidates=bank_candidates)
        return BlackboardEntry(
            run_id=context.run_id,
            request_id=request.request_id,
            kind="source_record",
            agent=agent,
            phase=phase,
            payload={
                "title": "bank_compare_scorecard",
                "content": str(scorecard),
                "source_type": "bank_compare_scorecard",
                "scorecard": scorecard,
            },
            source_refs=[{"type": "stock_compare_scorecard", "run_id": context.run_id}],
            confidence=1.0,
            risk_flags=list(scorecard.get("missing_metrics") or []),
        )

    def _bank_compare_scorecard(self, *, compare_targets: list[dict], peer_candidates: list[dict]) -> dict:
        target_names = {
            str(item.get("symbol") or "").strip(): str(item.get("name") or item.get("symbol") or "").strip()
            for item in compare_targets
            if str(item.get("symbol") or "").strip()
        }
        metric_weights = {
            "pb": 0.20,
            "peer_pb_mrq": 0.08,
            "peer_pb_rank": 0.05,
            "roe": 0.16,
            "peer_roe_3y_avg": 0.14,
            "peer_roe_rank": 0.05,
            "asset_liability_ratio": 0.08,
            "capital_adequacy_ratio": 0.05,
            "core_tier1_capital_adequacy_ratio": 0.05,
            "net_interest_spread": 0.05,
            "nonperforming_loan_ratio": 0.07,
            "provision_coverage_ratio": 0.06,
            "dividend_yield_proxy": 0.08,
            "dividend_payout_ratio": 0.03,
            "revenue_yoy": 0.04,
            "peer_revenue_growth_ttm": 0.06,
            "peer_revenue_growth_ttm_vs_industry_avg": 0.03,
            "net_profit_yoy": 0.06,
            "peer_net_profit_growth_ttm": 0.08,
            "peer_net_profit_growth_ttm_vs_industry_avg": 0.05,
            "interest_margin_proxy": 0.04,
            "peer_net_margin_3y_avg": 0.03,
            "net_interest_spread_proxy": 0.03,
            "deposit_growth_proxy": 0.02,
            "loan_to_deposit_proxy": 0.02,
            "provision_coverage_proxy": 0.03,
        }
        direction = {
            "pb": "lower",
            "peer_pb_mrq": "lower",
            "peer_pb_rank": "lower",
            "roe": "higher",
            "peer_roe_3y_avg": "higher",
            "peer_roe_rank": "lower",
            "asset_liability_ratio": "lower",
            "capital_adequacy_ratio": "higher",
            "core_tier1_capital_adequacy_ratio": "higher",
            "net_interest_spread": "higher",
            "nonperforming_loan_ratio": "lower",
            "provision_coverage_ratio": "higher",
            "dividend_yield_proxy": "higher",
            "dividend_payout_ratio": "higher",
            "revenue_yoy": "higher",
            "peer_revenue_growth_ttm": "higher",
            "peer_revenue_growth_ttm_vs_industry_avg": "higher",
            "net_profit_yoy": "higher",
            "peer_net_profit_growth_ttm": "higher",
            "peer_net_profit_growth_ttm_vs_industry_avg": "higher",
            "interest_margin_proxy": "higher",
            "peer_net_margin_3y_avg": "higher",
            "net_interest_spread_proxy": "higher",
            "deposit_growth_proxy": "higher",
            "loan_to_deposit_proxy": "neutral",
            "provision_coverage_proxy": "higher",
        }
        metric_series: dict[str, list[float]] = {key: [] for key in metric_weights}
        for candidate in peer_candidates:
            metrics = self._bank_compare_metrics(candidate)
            for key in metric_weights:
                value = coerce_numeric(metrics.get(key))
                if value is not None:
                    metric_series[key].append(value)

        ranking_rows: list[dict] = []
        metric_presence_counts: dict[str, int] = {key: 0 for key in metric_weights}
        for candidate in peer_candidates:
            symbol = str(candidate.get("symbol") or "").strip()
            metrics = self._bank_compare_metrics(candidate)
            score = 0.0
            weight_used = 0.0
            metric_breakdown: dict[str, float | None] = {}
            highlights: list[str] = []
            for key, weight in metric_weights.items():
                value = coerce_numeric(metrics.get(key))
                metric_breakdown[key] = value
                if value is not None:
                    metric_presence_counts[key] += 1
                if value is None or len(metric_series[key]) < 2:
                    continue
                percentile = self._bank_metric_percentile(value=value, series=metric_series[key], preference=direction[key])
                if percentile is None:
                    continue
                score += percentile * weight
                weight_used += weight
            normalized_score = round((score / weight_used), 4) if weight_used > 0 else 0.0
            if metric_breakdown.get("peer_roe_3y_avg") is not None:
                highlights.append(f"同业ROE={metric_breakdown['peer_roe_3y_avg']:.2%}")
            asset_quality_highlight = self._bank_asset_quality_highlight(metric_breakdown)
            if asset_quality_highlight:
                highlights.append(asset_quality_highlight)
            net_interest_spread_highlight = self._bank_net_interest_spread_highlight(metric_breakdown)
            if net_interest_spread_highlight:
                highlights.append(net_interest_spread_highlight)
            capital_buffer_highlight = self._bank_capital_buffer_highlight(metric_breakdown)
            if capital_buffer_highlight:
                highlights.append(capital_buffer_highlight)
            if metric_breakdown.get("peer_pb_mrq") is not None:
                highlights.append(f"同业PB={metric_breakdown['peer_pb_mrq']:.2f}")
            if metric_breakdown.get("roe") is not None:
                highlights.append(f"ROE={metric_breakdown['roe']:.2%}")
            if metric_breakdown.get("dividend_yield_proxy") is not None:
                highlights.append(f"股息率代理={metric_breakdown['dividend_yield_proxy']:.2%}")
            if metric_breakdown.get("peer_net_profit_growth_ttm") is not None:
                highlights.append(f"同业利润增速={metric_breakdown['peer_net_profit_growth_ttm']:.2%}")
            if metric_breakdown.get("net_profit_yoy") is not None:
                highlights.append(f"利润增速={metric_breakdown['net_profit_yoy']:.2%}")
            if metric_breakdown.get("interest_margin_proxy") is not None:
                highlights.append(f"净利率代理={metric_breakdown['interest_margin_proxy']:.2f}")
            if metric_breakdown.get("provision_coverage_proxy") is not None:
                highlights.append(f"拨备代理={metric_breakdown['provision_coverage_proxy']:.2f}")
            if metric_breakdown.get("asset_liability_ratio") is not None:
                highlights.append(f"资产负债率={metric_breakdown['asset_liability_ratio']:.2%}")
            ranking_rows.append(
                {
                    "symbol": symbol,
                    "name": target_names.get(symbol) or str(candidate.get("name") or symbol),
                    "score": normalized_score,
                    "weight_coverage": round(weight_used, 4),
                    "latest_price": candidate.get("latest_price"),
                    "latest_pct_change": candidate.get("latest_pct_change"),
                    "latest_amount": candidate.get("latest_amount"),
                    "pb": candidate.get("pb"),
                    "pe_ttm": candidate.get("pe_ttm"),
                    "market_cap": candidate.get("market_cap"),
                    "float_market_cap": candidate.get("float_market_cap"),
                    "market_total_shares": candidate.get("market_total_shares"),
                    "market_float_shares": candidate.get("market_float_shares"),
                    "banking_metrics": metric_breakdown,
                    "highlights": highlights[:4],
                    "missing_metrics": sorted(key for key, value in metric_breakdown.items() if value is None),
                }
            )
        ranking_rows.sort(key=lambda item: (item.get("score") or 0.0, item.get("weight_coverage") or 0.0), reverse=True)
        for index, row in enumerate(ranking_rows, start=1):
            row["rank"] = index
        top_names = [str(item.get("name") or item.get("symbol") or "") for item in ranking_rows[:3]]
        candidate_count = len(ranking_rows)
        coverage_threshold = max(candidate_count - 1, 1)
        missing_metrics = sorted(
            key for key, count in metric_presence_counts.items()
            if count < coverage_threshold
        )
        summary = (
            f"bank peer comparison ranked {len(ranking_rows)} banks using same-basis banking metrics and valuation anchors; "
            f"top_tier={top_names}"
        )
        return {
            "scorecard_type": "bank_compare_scorecard",
            "candidate_count": len(ranking_rows),
            "ranked_candidates": ranking_rows,
            "metric_weights": metric_weights,
            "missing_metrics": sorted(missing_metrics),
            "summary": summary,
        }

    def _bank_compare_metrics(self, candidate: dict) -> dict[str, Any]:
        metrics = dict(candidate.get("banking_metrics") or {})
        metrics["pb"] = coerce_numeric(candidate.get("pb"))
        metrics["pe_ttm"] = coerce_numeric(candidate.get("pe_ttm"))
        metrics["market_cap"] = coerce_numeric(candidate.get("market_cap"))
        metrics["float_market_cap"] = coerce_numeric(candidate.get("float_market_cap"))
        metrics["dividend_yield_proxy"] = self._bank_dividend_yield_proxy(candidate)
        return metrics

    def _bank_dividend_yield_proxy(self, candidate: dict) -> float | None:
        metrics = dict(candidate.get("banking_metrics") or {})
        dividend_cash = coerce_numeric(metrics.get("pay_dividends_profits_interest_cash"))
        latest_price = coerce_numeric(candidate.get("latest_price"))
        total_shares = coerce_numeric(candidate.get("market_total_shares"))
        if dividend_cash in (None, 0) or latest_price in (None, 0) or total_shares in (None, 0):
            return None
        dividend_per_share = dividend_cash / total_shares
        return dividend_per_share / latest_price

    def _bank_asset_quality_highlight(self, metrics: dict[str, float | None]) -> str | None:
        npl = coerce_numeric(metrics.get("nonperforming_loan_ratio"))
        provision = coerce_numeric(metrics.get("provision_coverage_ratio"))
        parts: list[str] = []
        if npl is not None:
            parts.append(f"不良率{npl:.2%}")
        if provision is not None:
            parts.append(f"拨备覆盖率{provision:.2%}")
        if not parts:
            return None
        return "资产质量=" + "/".join(parts[:2])

    def _bank_capital_buffer_highlight(self, metrics: dict[str, float | None]) -> str | None:
        capital = coerce_numeric(metrics.get("capital_adequacy_ratio"))
        core_tier1 = coerce_numeric(metrics.get("core_tier1_capital_adequacy_ratio"))
        parts: list[str] = []
        if capital is not None:
            parts.append(f"资本充足率{capital:.2%}")
        if core_tier1 is not None:
            parts.append(f"核心一级{core_tier1:.2%}")
        if not parts:
            return None
        return "资本缓冲=" + "/".join(parts[:2])

    def _bank_net_interest_spread_highlight(self, metrics: dict[str, float | None]) -> str | None:
        net_interest_spread = coerce_numeric(metrics.get("net_interest_spread"))
        if net_interest_spread is None:
            return None
        return f"净息差={net_interest_spread:.2%}"

    def _bank_metric_percentile(self, *, value: float, series: list[float], preference: str) -> float | None:
        if not series:
            return None
        ordered = sorted(series)
        if preference == "neutral":
            median = ordered[len(ordered) // 2]
            max_distance = max(abs(item - median) for item in ordered) if ordered else 0.0
            if max_distance == 0:
                return 1.0
            return max(0.0, 1.0 - abs(value - median) / max_distance)
        below = sum(1 for item in ordered if item <= value)
        percentile = below / len(ordered)
        if preference == "lower":
            percentile = 1.0 - percentile
        return round(percentile, 4)

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

