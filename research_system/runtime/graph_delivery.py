from __future__ import annotations

from typing import Any

from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.storage.jsonl import read_json
from research_system.runtime.presentation.display import runtime_status_message
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.execution_runtime import ExecutionRunResult


def ensure_graph_assistant_message(
    agent_os: Any,
    result: ExecutionRunResult,
) -> ExecutionRunResult:
    runner = agent_os._runner(result.snapshot.run_id)
    existing = runner.board.list_artifacts(
        run_id=result.snapshot.run_id,
        artifact_type="assistant_message",
        status="active",
    )
    if existing:
        assistant_id = existing[-1].artifact_id
        existing_content = existing[-1].content if isinstance(existing[-1].content, dict) else {}
        existing_delivery_status = str(
            existing_content.get("delivery_status") or result.snapshot.status
        )
        has_synthesis = bool(
            runner.board.list_artifacts(
                run_id=result.snapshot.run_id,
                artifact_type="main_agent_synthesis",
                status="active",
            )
        )
        if (
            result.snapshot.status == "completed"
            and result.snapshot.graph.domain_id == "finance_ashare"
            and not has_synthesis
            and existing_delivery_status == "completed"
        ):
            # A Pack may have emitted an assistant message before the
            # canonical Main Agent synthesis stage. Preserve the old
            # artifact for replay, but supersede it so delivery status
            # cannot claim a complete user-facing answer.
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
        existing_event_type = (
            "assistant_message_ready"
            if existing_delivery_status == "completed"
            else "assistant_message_status"
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
    artifacts = runner.board.list_artifacts(run_id=result.snapshot.run_id, status="active")
    report = next(
        (
            artifact
            for artifact in reversed(artifacts)
            if artifact.artifact_type == "research_report"
        ),
        None,
    )
    delivery_artifacts = [
        artifact
        for artifact in artifacts
        if artifact.artifact_type not in {"assistant_message", "main_agent_synthesis"}
    ]
    synthesis = None
    synthesis_artifact = None
    synthesis_unavailable = False
    if (
        result.snapshot.status == "completed"
        and (report is not None or delivery_artifacts)
        and result.snapshot.graph.domain_id == "finance_ashare"
    ):
        request_payload = read_json(runner.run_root / "request.json") or {}
        request_text = str(
            request_payload.get("user_query")
            or request_payload.get("query")
            or result.snapshot.graph.objective
            or ""
        )
        board_facts = [artifact.model_dump(mode="json") for artifact in delivery_artifacts]
        synthesis = agent_os.main_agent.compose_research_synthesis(
            request=request_text,
            report=dict(report.content) if report is not None else {
                "delivery_mode": "method_specific",
                "artifact_types": [artifact.artifact_type for artifact in delivery_artifacts],
            },
            artifacts=board_facts,
        )
        if synthesis:
            source_refs: list[dict] = []
            for artifact in delivery_artifacts:
                for source_ref in artifact.source_refs:
                    if source_ref not in source_refs:
                        source_refs.append(source_ref)
            synthesis_artifact = runner.board.add(
                ArtifactRecord(
                    artifact_type="main_agent_synthesis",
                    schema_version="1",
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
                            report.artifact_id
                            if report is not None
                            else delivery_artifacts[0].artifact_id
                        ),
                        "source_artifact_ids": [artifact.artifact_id for artifact in delivery_artifacts],
                        "model_generated": True,
                    },
                    source_refs=source_refs,
                    parent_artifact_ids=[artifact.artifact_id for artifact in delivery_artifacts],
                    confidence=min(
                        [float(artifact.confidence or 0.0) for artifact in delivery_artifacts]
                        or [0.0]
                    ),
                    risk_flags=[
                        *dict.fromkeys(
                            flag
                            for artifact in delivery_artifacts
                            for flag in artifact.risk_flags
                        ),
                        "main_agent_synthesis",
                    ],
                )
            )
        else:
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
    content = report.content if report is not None else {}
    if synthesis_unavailable:
        message = "专业研究产物已完成，但主 Agent 暂时无法生成最终答复，请稍后重试。"
        source_refs = list(report.source_refs) if report is not None else []
    elif result.snapshot.status == "completed":
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
        else ("completed" if result.snapshot.status == "completed" else result.snapshot.status)
    )
    assistant_event_type = (
        "assistant_message_ready"
        if delivery_status == "completed"
        else "assistant_message_status"
    )
    assistant = runner.board.add(
        ArtifactRecord(
            artifact_type="assistant_message",
            schema_version="1",
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
                        *(
                            artifact.artifact_id
                            for artifact in delivery_artifacts
                            if report is None
                        ),
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
                    *(
                        artifact.artifact_id
                        for artifact in delivery_artifacts
                        if report is None
                    ),
                )
                if artifact_id
            ],
            confidence=(
                report.confidence
                if result.snapshot.status == "completed" and report is not None and not synthesis_unavailable
                else (
                    min([float(artifact.confidence or 0.0) for artifact in delivery_artifacts] or [0.0])
                    if result.snapshot.status == "completed" and not synthesis_unavailable
                    else 0.0
                )
            ),
        )
    )
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
            "artifact_ids": [
                *result.artifact_ids,
                *(
                    [synthesis_artifact.artifact_id]
                    if synthesis_artifact is not None
                    else []
                ),
                assistant.artifact_id,
            ],
        }
    )



__all__ = ["ensure_graph_assistant_message"]
