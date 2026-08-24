from __future__ import annotations

from time import monotonic
from typing import Any

from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.presentation.display import runtime_status_message
from research_system.runtime.storage.jsonl import read_json
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.execution_runtime import ExecutionRunResult


_SYNTHESIS_FINALIZATION_RESERVE_SECONDS = 2.0
_MIN_SYNTHESIS_BUDGET_SECONDS = 1.0


def ensure_graph_assistant_message(
    agent_os: Any,
    result: ExecutionRunResult,
    *,
    deadline_at: float | None = None,
) -> ExecutionRunResult:
    runner = agent_os._runner(result.snapshot.run_id)
    existing_assistant = None
    artifacts = runner.board.list_artifacts(run_id=result.snapshot.run_id, status="active")
    reports = [artifact for artifact in artifacts if artifact.artifact_type == "research_report"]
    report = reports[-1] if reports else None
    delivery_artifacts = [
        artifact
        for artifact in artifacts
        if artifact.artifact_type not in {"assistant_message", "main_agent_synthesis"}
    ]
    existing_syntheses = runner.board.list_artifacts(
        run_id=result.snapshot.run_id,
        artifact_type="main_agent_synthesis",
        status="active",
    )
    latest_existing_synthesis = existing_syntheses[-1] if existing_syntheses else None
    existing_synthesis_artifact = next(
        (
            artifact
            for artifact in reversed(existing_syntheses)
            if _synthesis_matches_delivery(
                artifact,
                report=report,
                delivery_artifacts=delivery_artifacts,
            )
            and _synthesis_payload(artifact.content) is not None
        ),
        None,
    )
    existing = runner.board.list_artifacts(
        run_id=result.snapshot.run_id,
        artifact_type="assistant_message",
        status="active",
    )
    if existing:
        assistant_id = existing[-1].artifact_id
        existing_content = existing[-1].content if isinstance(existing[-1].content, dict) else {}
        existing_delivery_status = str(existing_content.get("delivery_status") or result.snapshot.status)
        existing_source_ids = {
            str(artifact_id)
            for artifact_id in list(existing_content.get("source_artifact_ids") or [])
            if str(artifact_id).strip()
        }
        assistant_has_canonical_synthesis = bool(
            existing_synthesis_artifact is not None
            and existing_synthesis_artifact.artifact_id in existing_source_ids
            and existing_delivery_status == "completed"
        )
        if result.snapshot.status not in {"completed", "completed_partial"}:
            # A rerun can replace a previously completed terminal state with a
            # failure, gate, timeout or pause. Supersede the old answer instead
            # of replaying it as if the rerun had succeeded.
            existing_assistant = existing[-1]
        if (
            result.snapshot.status in {"completed", "completed_partial"}
            and result.snapshot.graph.domain_id == "finance_ashare"
            and not assistant_has_canonical_synthesis
        ):
            if delivery_artifacts or existing_synthesis_artifact is not None:
                # Continue into canonical delivery and supersede the Pack
                # message. A pre-existing synthesis means the prior worker
                # stopped between the two delivery writes; reuse it instead
                # of making another model call.
                existing_assistant = existing[-1]
            elif result.snapshot.status == "completed" or existing_delivery_status != "synthesis_unavailable":
                agent_os._mark_graph_partial_delivery(result=result, runner=runner)
                partial_artifact = runner.board.add(
                    existing[-1].model_copy(
                        update={
                            "artifact_id": generate_ulid(),
                            "artifact_version": existing[-1].artifact_version + 1,
                            "supersedes_artifact_id": existing[-1].artifact_id,
                            "content": {
                                **existing_content,
                                "delivery_status": "synthesis_unavailable",
                                "synthesis_unavailable": True,
                            },
                        }
                    )
                )
                runner._emit(
                    "assistant_message_status",
                    run_id=result.snapshot.run_id,
                    thread_id=result.snapshot.thread_id,
                    request_id=result.snapshot.request_id,
                    mode="graph",
                    delivery_status="synthesis_unavailable",
                    synthesis_unavailable=True,
                    message=str(existing_content.get("message") or ""),
                    artifact_ids=[partial_artifact.artifact_id],
                )
                return result.model_copy(
                    update={
                        "artifact_ids": [
                            *result.artifact_ids,
                            partial_artifact.artifact_id,
                        ],
                    }
                )
        elif result.snapshot.status == "completed_partial" and assistant_has_canonical_synthesis and delivery_artifacts:
            agent_os._mark_graph_completed_delivery(result=result, runner=runner)
        if existing_assistant is None:
            existing_event_type = (
                "assistant_message_ready" if existing_delivery_status == "completed" else "assistant_message_status"
            )
            runner._emit(
                existing_event_type,
                run_id=result.snapshot.run_id,
                thread_id=result.snapshot.thread_id,
                request_id=result.snapshot.request_id,
                mode="graph",
                delivery_status=existing_delivery_status,
                message=str(existing_content.get("message") or ""),
                artifact_ids=[assistant_id],
            )
            return result.model_copy(
                update={
                    "artifact_ids": [*result.artifact_ids, assistant_id]
                    if assistant_id not in result.artifact_ids
                    else result.artifact_ids,
                }
            )
    if result.snapshot.status not in {"completed", "completed_partial"}:
        existing_synthesis_artifact = None
    synthesis = (
        _synthesis_payload(existing_synthesis_artifact.content) if existing_synthesis_artifact is not None else None
    )
    synthesis_artifact = existing_synthesis_artifact if synthesis is not None else None
    synthesis_artifact_pending = False
    synthesis_unavailable = result.snapshot.status == "completed_partial" and existing_synthesis_artifact is None
    synthesis_model_generated = (
        bool((existing_synthesis_artifact.content or {}).get("model_generated", True))
        if existing_synthesis_artifact is not None
        else True
    )
    synthesis_fallback_reason = (
        str((existing_synthesis_artifact.content or {}).get("fallback_reason") or "") or None
        if existing_synthesis_artifact is not None
        else None
    )
    if (
        result.snapshot.status in {"completed", "completed_partial"}
        and (report is not None or delivery_artifacts)
        and result.snapshot.graph.domain_id == "finance_ashare"
    ):
        request_payload = read_json(runner.run_root / "request.json") or {}
        request_constraints = (
            request_payload.get("constraints")
            if isinstance(request_payload, dict) and isinstance(request_payload.get("constraints"), dict)
            else {}
        )
        request_text = str(
            request_payload.get("user_query") or request_payload.get("query") or result.snapshot.graph.objective or ""
        )
        request_intent = str(
            request_payload.get("intent") or result.snapshot.graph.metadata.get("intent") or ""
        ).strip()
        board_facts = [artifact.model_dump(mode="json") for artifact in delivery_artifacts]
        if synthesis_artifact is None:
            if request_constraints.get("fund_holding_report_child") is True and report is not None:
                # Holding-report children are hidden orchestration Runs. Their
                # quality-gated report is already the canonical deliverable.
                report_content = dict(report.content)
                conclusion = str(
                    report_content.get("conclusion")
                    or report_content.get("summary")
                    or "UZI 股票研究报告已完成并通过质量门。"
                ).strip()
                synthesis = {
                    "conclusion": conclusion[:2_000],
                    "assistant_message": "持仓股票的 UZI 研究报告已生成并通过质量门，可在基金研究页查看或导出。",
                    "follow_up": [],
                }
                synthesis_model_generated = False
                synthesis_fallback_reason = "fund_holding_report_is_canonical"
            elif request_intent == "fund_research" and report is not None:
                # Fund reports already contain the governed conclusion, risks
                # and follow-up actions; a second model call adds no evidence.
                synthesis = _deterministic_report_synthesis(report.content)
                synthesis_model_generated = False
                synthesis_fallback_reason = "fund_report_is_canonical"
            else:
                synthesis_budget = _synthesis_budget_seconds(
                    agent_os,
                    deadline_at=deadline_at,
                )
                if synthesis_budget is None:
                    synthesis_fallback_reason = "turn_deadline_budget_exhausted"
                else:
                    runner._emit(
                        "main_agent_synthesis_started",
                        timeout_seconds=synthesis_budget,
                        max_retries=0,
                        max_continuation_attempts=0,
                    )
                    try:
                        synthesis = agent_os.main_agent.compose_research_synthesis(
                            request=request_text,
                            report=dict(report.content)
                            if report is not None
                            else {
                                "delivery_mode": "method_specific",
                                "artifact_types": [artifact.artifact_type for artifact in delivery_artifacts],
                            },
                            artifacts=board_facts,
                            timeout_seconds=synthesis_budget,
                            max_retries=0,
                            max_continuation_attempts=0,
                            raise_on_error=True,
                        )
                    except Exception as exc:
                        synthesis_fallback_reason = f"model_error:{exc.__class__.__name__}"
                        runner._emit(
                            "main_agent_synthesis_failed",
                            error_type=exc.__class__.__name__,
                            error=str(exc)[:1_000],
                            fallback_available=report is not None,
                        )
                    else:
                        if synthesis is None:
                            synthesis_fallback_reason = "model_returned_no_synthesis"
                if synthesis is None and report is not None:
                    synthesis = _deterministic_report_synthesis(report.content)
                    synthesis_model_generated = False
        if synthesis and synthesis_artifact is None:
            source_refs: list[dict] = []
            for artifact in delivery_artifacts:
                for source_ref in artifact.source_refs:
                    if source_ref not in source_refs:
                        source_refs.append(source_ref)
            synthesis_artifact = ArtifactRecord(
                artifact_id=generate_ulid(),
                artifact_type="main_agent_synthesis",
                schema_version="1",
                artifact_version=(
                    latest_existing_synthesis.artifact_version + 1 if latest_existing_synthesis is not None else 1
                ),
                supersedes_artifact_id=(
                    latest_existing_synthesis.artifact_id if latest_existing_synthesis is not None else None
                ),
                run_id=result.snapshot.run_id,
                thread_id=result.snapshot.thread_id,
                request_id=result.snapshot.request_id,
                user_id=result.snapshot.user_id,
                tenant_id=result.snapshot.tenant_id,
                producer=ArtifactProducer(
                    executor="main_agent",
                    capability="core.research.synthesize",
                    domain_id=result.snapshot.graph.domain_id,
                    responsibility_node_id="main_agent",
                ),
                content={
                    **synthesis,
                    "source_artifact_id": (
                        report.artifact_id if report is not None else delivery_artifacts[0].artifact_id
                    ),
                    "source_artifact_ids": [artifact.artifact_id for artifact in delivery_artifacts],
                    "model_generated": synthesis_model_generated,
                    **({"fallback_reason": synthesis_fallback_reason} if synthesis_fallback_reason else {}),
                },
                source_refs=source_refs,
                parent_artifact_ids=[artifact.artifact_id for artifact in delivery_artifacts],
                confidence=min([float(artifact.confidence or 0.0) for artifact in delivery_artifacts] or [0.0]),
                risk_flags=[
                    *dict.fromkeys(flag for artifact in delivery_artifacts for flag in artifact.risk_flags),
                    "main_agent_synthesis",
                    *(["deterministic_delivery_fallback"] if not synthesis_model_generated else []),
                ],
            )
            synthesis_artifact_pending = True
            synthesis_unavailable = False
        elif not synthesis:
            synthesis_unavailable = True
            # A graph can finish its Pack work without having a user-facing
            # Main Agent synthesis. Keep that distinction in the canonical
            # Run state instead of reporting a complete delivery.
            agent_os._mark_graph_partial_delivery(result=result, runner=runner)
            result = result.model_copy(
                update={
                    "snapshot": result.snapshot.model_copy(deep=True),
                }
            )
    delivery_recovered = bool(
        result.snapshot.status == "completed_partial"
        and synthesis is not None
        and delivery_artifacts
        and not synthesis_unavailable
    )
    delivery_completed = result.snapshot.status == "completed" or delivery_recovered
    content = report.content if report is not None else {}
    if synthesis_unavailable:
        message = "专业研究产物已完成，但主 Agent 暂时无法生成最终答复，请稍后重试。"
        source_refs = list(report.source_refs) if report is not None else []
    elif delivery_completed:
        if synthesis:
            message = str(synthesis.get("assistant_message") or "主 Agent 已完成研究交付。")
        else:
            message = str(content.get("conclusion") or content.get("summary") or "Agent OS 图运行已完成。")
        source_refs = list(report.source_refs) if report is not None else []
        if not source_refs:
            for artifact in delivery_artifacts:
                for source_ref in artifact.source_refs:
                    if source_ref not in source_refs:
                        source_refs.append(source_ref)
        if not source_refs:
            source_refs = [{"type": "request", "id": result.snapshot.request_id}]
    elif result.snapshot.clarification_question:
        message = result.snapshot.clarification_question
        source_refs = [{"type": "request", "id": result.snapshot.request_id}]
    else:
        message = {
            "gated": "Agent OS 已生成专业产物，但交付被质量门禁或审批阻止。",
            "waiting_approval": "Agent OS 正在等待所需审批，审批通过后才能交付。",
            "failed": "Agent OS 未能完成专业运行，已生成的产物仍可查看。",
        }.get(
            result.snapshot.status,
            runtime_status_message(result.snapshot.status, subject="Agent OS 图运行"),
        )
        source_refs = [{"type": "request", "id": result.snapshot.request_id}]
    delivery_status = (
        "synthesis_unavailable"
        if synthesis_unavailable
        else ("completed" if delivery_completed else result.snapshot.status)
    )
    assistant_event_type = "assistant_message_ready" if delivery_status == "completed" else "assistant_message_status"
    assistant_record = ArtifactRecord(
        artifact_id=generate_ulid(),
        artifact_type="assistant_message",
        schema_version="1",
        artifact_version=(existing_assistant.artifact_version + 1 if existing_assistant is not None else 1),
        supersedes_artifact_id=(existing_assistant.artifact_id if existing_assistant is not None else None),
        run_id=result.snapshot.run_id,
        thread_id=result.snapshot.thread_id,
        request_id=result.snapshot.request_id,
        user_id=result.snapshot.user_id,
        tenant_id=result.snapshot.tenant_id,
        producer=ArtifactProducer(
            executor="main_agent",
            capability="core.message.compose",
            domain_id=result.snapshot.graph.domain_id,
            responsibility_node_id="main_agent",
        ),
        content={
            "message": message,
            "source_artifact_ids": [
                artifact_id
                for artifact_id in (
                    report.artifact_id if report is not None else None,
                    synthesis_artifact.artifact_id if synthesis_artifact is not None else None,
                    *(artifact.artifact_id for artifact in delivery_artifacts if report is None),
                )
                if artifact_id
            ],
            "mode": "graph",
            "delivery_status": delivery_status,
            "synthesis_unavailable": synthesis_unavailable,
        },
        source_refs=source_refs,
        parent_artifact_ids=[
            artifact_id
            for artifact_id in (
                report.artifact_id if report is not None else None,
                synthesis_artifact.artifact_id if synthesis_artifact is not None else None,
                *(artifact.artifact_id for artifact in delivery_artifacts if report is None),
            )
            if artifact_id
        ],
        confidence=(
            report.confidence
            if delivery_completed and report is not None and not synthesis_unavailable
            else (
                min([float(artifact.confidence or 0.0) for artifact in delivery_artifacts] or [0.0])
                if delivery_completed and not synthesis_unavailable
                else 0.0
            )
        ),
    )
    if synthesis_artifact_pending and synthesis_artifact is not None:
        synthesis_artifact, assistant = runner.board.add_many([synthesis_artifact, assistant_record])
        if synthesis_model_generated:
            runner._emit(
                "main_agent_synthesis_completed",
                model_generated=True,
                artifact_ids=[synthesis_artifact.artifact_id],
            )
        else:
            runner._emit(
                "main_agent_synthesis_fallback",
                reason=synthesis_fallback_reason or "report_backed_fallback",
                source_artifact_id=(report.artifact_id if report is not None else None),
                artifact_ids=[synthesis_artifact.artifact_id],
            )
    else:
        assistant = runner.board.add(assistant_record)
    if delivery_recovered:
        agent_os._mark_graph_completed_delivery(result=result, runner=runner)
    runner._emit(
        assistant_event_type,
        run_id=result.snapshot.run_id,
        thread_id=result.snapshot.thread_id,
        request_id=result.snapshot.request_id,
        mode="graph",
        delivery_status=delivery_status,
        message=message,
        artifact_ids=[assistant.artifact_id],
        source_artifact_ids=[
            artifact_id
            for artifact_id in (
                report.artifact_id if report is not None else None,
                synthesis_artifact.artifact_id if synthesis_artifact is not None else None,
            )
            if artifact_id
        ],
    )
    return result.model_copy(
        update={
            "artifact_ids": list(
                dict.fromkeys(
                    [
                        *result.artifact_ids,
                        *([synthesis_artifact.artifact_id] if synthesis_artifact is not None else []),
                        assistant.artifact_id,
                    ]
                )
            ),
        }
    )


def _synthesis_matches_delivery(
    synthesis_artifact: ArtifactRecord,
    *,
    report: ArtifactRecord | None,
    delivery_artifacts: list[ArtifactRecord],
) -> bool:
    content = synthesis_artifact.content
    if not isinstance(content, dict):
        return False
    source_ids = {
        str(artifact_id) for artifact_id in list(content.get("source_artifact_ids") or []) if str(artifact_id).strip()
    }
    source_id = str(content.get("source_artifact_id") or "").strip()
    if report is not None:
        return report.artifact_id == source_id or report.artifact_id in source_ids
    active_delivery_ids = {artifact.artifact_id for artifact in delivery_artifacts}
    return bool(source_ids) and source_ids == active_delivery_ids


def _synthesis_budget_seconds(
    agent_os: Any,
    *,
    deadline_at: float | None,
) -> float | None:
    configured = float(getattr(agent_os.settings, "llm_request_timeout_seconds", 25.0))
    if deadline_at is None:
        return configured
    available = float(deadline_at) - monotonic() - _SYNTHESIS_FINALIZATION_RESERVE_SECONDS
    if available < _MIN_SYNTHESIS_BUDGET_SECONDS:
        return None
    return min(configured, available)


def _deterministic_report_synthesis(
    report_content: Any,
) -> dict[str, Any] | None:
    if not isinstance(report_content, dict):
        return None
    conclusion = str(report_content.get("conclusion") or report_content.get("summary") or "").strip()
    if not conclusion:
        return None
    risk_items = _report_text_items(report_content.get("risk"), limit=3)
    assistant_message = conclusion
    if risk_items:
        assistant_message = f"{conclusion}\n\n主要风险与限制：{'；'.join(risk_items)}"
    follow_up = _report_text_items(
        report_content.get("follow_up"),
        limit=5,
        max_length=500,
    )
    return {
        "conclusion": conclusion[:2_000],
        "assistant_message": assistant_message[:4_000],
        "follow_up": follow_up,
    }


def _synthesis_payload(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, dict):
        return None
    conclusion = str(content.get("conclusion") or "").strip()
    assistant_message = str(content.get("assistant_message") or conclusion).strip()
    if not conclusion or not assistant_message:
        return None
    return {
        "conclusion": conclusion[:2_000],
        "assistant_message": assistant_message[:4_000],
        "follow_up": _report_text_items(
            content.get("follow_up"),
            limit=5,
            max_length=500,
        ),
    }


def _report_text_items(
    value: Any,
    *,
    limit: int,
    max_length: int | None = None,
) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = list(value)
    else:
        values = []
    items = [str(item).strip() for item in values if str(item).strip()]
    if max_length is not None:
        items = [item[:max_length] for item in items]
    return items[:limit]


__all__ = ["ensure_graph_assistant_message"]
