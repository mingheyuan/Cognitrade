from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_system.agents.collector.service import CollectorService
from research_system.agents.critic.service import CriticService
from research_system.agents.distiller.service import DistillerService
from research_system.agents.reporter.service import ReporterService
from research_system.agents.reviewer.service import ReviewerService
from research_system.agents.retriever.service import RetrieverService
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
from research_system.runtime.task_graph import TaskGraph, build_task_graph
from research_system.runtime.team_templates import get_team_template
from research_system.runtime.jsonl import read_jsonl
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope
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
        self.gates = GateEvaluator()
        self.retention = RunRetentionService(settings)

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
        )
        context.control_plane.initialize(run_id=run_id, run_timeout_seconds=self.settings.run_timeout_seconds)

        try:
            self._emit_event(context, request_id=request.request_id, intent=intent, event_type="request_received", message="Request received")
            self._transition(context, "identifying_intent", request_id=request.request_id, intent=intent, message="Identifying intent")
            self._emit_event(context, request_id=request.request_id, intent=intent, event_type="intent_identified", message=f"Intent identified: {intent}")
            get_team_template(intent)
            graph = build_task_graph(intent)
            write_json(paths.task_plan, {"intent": graph.intent, "nodes": [node.__dict__ for node in graph.nodes]})
            self._transition(context, "ready", request_id=request.request_id, intent=intent, message="Task graph created")
            self._transition(context, "running", request_id=request.request_id, intent=intent, message="Run accepted")
            self._emit_event(context, request_id=request.request_id, intent=intent, event_type="run_accepted", message="Run accepted")

            for index, node in enumerate(graph.nodes, start=1):
                self._enforce_runtime_controls(context, request_id=request.request_id, intent=intent)
                self._run_node(context, request, graph, node.node_id, node.phase, node.agent, index, len(graph.nodes))

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

    def _run_node(
        self,
        context: RuntimeContext,
        request: RequestEnvelope,
        graph: TaskGraph,
        node_id: str,
        phase: str,
        agent: str,
        index: int,
        total_nodes: int,
    ) -> None:
        self._update_state(context, current_phase=phase, active_agent=agent)
        self._emit_event(
            context,
            request_id=request.request_id,
            intent=graph.intent,
            event_type="phase_started",
            phase=phase,
            agent=agent,
            message=f"Phase started: {phase}",
        )
        self._enforce_runtime_controls(context, request_id=request.request_id, intent=graph.intent)
        self._emit_event(
            context,
            request_id=request.request_id,
            intent=graph.intent,
            event_type="agent_started",
            phase=phase,
            agent=agent,
            message=f"Agent started: {agent}",
        )

        if agent == "collector":
            payload = self.collector.run(intent=graph.intent, query=request.user_query, constraints=request.constraints)
            if payload.get("requires_wait"):
                self._transition(
                    context,
                    "waiting_external",
                    request_id=request.request_id,
                    intent=graph.intent,
                    message="Waiting for external dependency",
                )
                context.control_plane.set_wait_deadline(wait_timeout_seconds=self.settings.external_wait_timeout_seconds)
                self._emit_event(
                    context,
                    request_id=request.request_id,
                    intent=graph.intent,
                    event_type="phase_started",
                    phase=phase,
                    agent=agent,
                    message="Entered waiting_external state",
                    payload={"wait_timeout_seconds": self.settings.external_wait_timeout_seconds},
                )
                self._wait_for_external_dependency(context, request_id=request.request_id, intent=graph.intent)
                context.control_plane.clear_wait_deadline()
                self._transition(
                    context,
                    "running",
                    request_id=request.request_id,
                    intent=graph.intent,
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
                self._add_entry(context, request, graph.intent, entry)
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
                self._add_entry(context, request, graph.intent, entry)
        elif agent == "retriever":
            blackboard_entries = [entry.model_dump(mode="json") for entry in context.blackboard.list_entries()]
            payload = self.retriever.run(intent=graph.intent, query=request.user_query, blackboard_entries=blackboard_entries)
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
            self._add_entry(context, request, graph.intent, entry)
        elif agent == "distiller":
            source_records = [
                entry.payload
                for entry in context.blackboard.list_entries()
                if entry.kind == "source_record"
            ]
            payload = self.distiller.run(query=request.user_query, source_records=source_records)
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
                self._add_entry(context, request, graph.intent, entry)
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
                self._add_entry(context, request, graph.intent, entry)
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
            self._add_entry(context, request, graph.intent, summary_entry)
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
            summary = critic_payload.get("summary") or {}
            scorecard = critic_payload.get("scorecard") or {}
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=graph.intent,
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
                self._add_entry(context, request, graph.intent, scorecard_entry)
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
                self._add_entry(context, request, graph.intent, entry)
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=graph.intent,
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
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=graph.intent,
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
                self._add_entry(context, request, graph.intent, entry)
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
            self._add_entry(context, request, graph.intent, summary_entry)
            self._emit_event(
                context,
                request_id=request.request_id,
                intent=graph.intent,
                event_type="review_completed",
                phase=phase,
                agent=agent,
                message="Reviewer posterior review completed",
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
                intent=graph.intent,
                query=request.user_query,
                evidences=evidences,
                retrieval_summary=retrieval_summary,
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
            self._add_entry(context, request, graph.intent, entry)
            write_json(context.paths.result, payload)
        else:
            raise OrchestratorError(f"unsupported agent: {agent}")

        self._enforce_runtime_controls(context, request_id=request.request_id, intent=graph.intent)
        self._emit_event(
            context,
            request_id=request.request_id,
            intent=graph.intent,
            event_type="agent_completed",
            phase=phase,
            agent=agent,
            message=f"Agent completed: {agent}",
        )
        self._emit_event(
            context,
            request_id=request.request_id,
            intent=graph.intent,
            event_type="phase_completed",
            phase=phase,
            agent=agent,
            message=f"Phase completed: {phase}",
        )
        self._update_state(
            context,
            pending_nodes=graph.node_ids[index:],
            completed_nodes=graph.node_ids[:index],
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
