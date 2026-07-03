from __future__ import annotations

import ast

from research_system.runtime.orchestrator_context import RuntimeContext


def collect_distiller_source_records(context: RuntimeContext) -> list[dict]:
    return [
        entry.payload
        for entry in context.blackboard.list_entries()
        if entry.kind == "source_record"
    ]


def collect_critic_inputs(context: RuntimeContext) -> dict[str, object]:
    logic_blocks: list[dict] = []
    distilled_dimensions: list[dict] = []
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
                    try:
                        parsed = ast.literal_eval(content)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict):
                        distillation_summary = parsed
    return {
        "logic_blocks": logic_blocks,
        "distilled_dimensions": distilled_dimensions,
        "distillation_summary": distillation_summary,
    }
