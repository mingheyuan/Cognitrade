from __future__ import annotations

from typing import Any

from research_system.schemas.tools import ToolDefinition


def register_builtin_tools(registry: Any) -> None:
    """Register the domain-neutral tools exposed to the Main Agent."""
    from research_system.runtime.core.tool_registry import (
        _followup_task,
        _inspect_artifacts,
        _inspect_graph,
        _invoke_skill,
        _query_memory,
        _read_file,
        _request_intake,
        _run_status,
        _search_files,
    )

    registry.register(
        ToolDefinition(
            name="core.artifact.inspect",
            description="Inspect current run artifacts and lineage metadata.",
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "artifact_type": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
            output_schema={"type": "object"},
            permissions=["artifact:read"],
            parallel_safe=True,
        ),
        _inspect_artifacts,
    )
    registry.register(
        ToolDefinition(
            name="core.memory.query",
            description="Query verified layered memory available to the Main Agent.",
            input_schema={
                "type": "object",
                "properties": {
                    "layer": {"type": "string", "enum": ["episodic", "domain", "policy"]},
                    "domain_id": {"type": "string"},
                    "query": {"type": "string", "maxLength": 2000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
            output_schema={"type": "object"},
            permissions=["memory:read"],
            parallel_safe=True,
        ),
        _query_memory,
    )
    registry.register(
        ToolDefinition(
            name="core.request.intake",
            description="Inspect the request and return a governed Core or DomainPack route proposal.",
            input_schema={"type": "object"},
            output_schema={"type": "object", "required": ["decision"]},
            permissions=["request:intake"],
            parallel_safe=False,
        ),
        _request_intake,
    )
    registry.register(
        ToolDefinition(
            name="core.graph.inspect",
            description="Inspect the current graph and revision when Graph Mode is active.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permissions=["graph:read"],
            parallel_safe=True,
        ),
        _inspect_graph,
    )
    registry.register(
        ToolDefinition(
            name="core.run.status",
            description="Inspect the current Main Agent run status.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permissions=["run:read"],
            parallel_safe=True,
        ),
        _run_status,
    )
    registry.register(
        ToolDefinition(
            name="core.file.read",
            description="Read a UTF-8 file within the configured workspace roots.",
            input_schema={
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 1},
                },
            },
            output_schema={"type": "object", "required": ["path", "content"]},
            permissions=["file:read"],
            parallel_safe=True,
        ),
        _read_file,
    )
    registry.register(
        ToolDefinition(
            name="core.file.search",
            description="Search UTF-8 text files within the configured workspace roots.",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
            output_schema={"type": "object", "required": ["matches"]},
            permissions=["file:read"],
            parallel_safe=True,
        ),
        _search_files,
    )
    registry.register(
        ToolDefinition(
            name="core.skill.invoke",
            description="Invoke an installed general Skill through the governed runtime.",
            input_schema={
                "type": "object",
                "required": ["skill_name"],
                "properties": {
                    "skill_name": {"type": "string", "minLength": 1},
                    "arguments": {"type": "object"},
                },
            },
            output_schema={"type": "object", "required": ["result", "source_refs"]},
            permissions=["skill:invoke"],
            parallel_safe=False,
        ),
        _invoke_skill,
    )
    registry.register(
        ToolDefinition(
            name="core.task.clarify",
            description="Create a structured clarification request for the user.",
            input_schema={
                "type": "object",
                "required": ["question"],
                "properties": {
                    "question": {"type": "string"},
                    "missing_fields": {"type": "array"},
                },
            },
            output_schema={"type": "object", "required": ["question", "missing_fields"]},
            permissions=["conversation:clarify"],
            parallel_safe=True,
        ),
        lambda arguments, _context: {
            "question": str(arguments.get("question") or "").strip(),
            "missing_fields": list(arguments.get("missing_fields") or []),
        },
    )
    registry.register(
        ToolDefinition(
            name="core.followup.task",
            description="Send governed follow-up work to an existing non-root ChildTurn.",
            input_schema={
                "type": "object",
                "required": [
                    "parent_turn_id",
                    "target_child_turn_id",
                    "target_agent_id",
                    "message",
                ],
                "properties": {
                    "parent_turn_id": {"type": "string", "minLength": 1},
                    "target_child_turn_id": {"type": "string", "minLength": 1},
                    "target_agent_id": {"type": "string", "minLength": 1},
                    "message": {"type": "string", "minLength": 1},
                    "trigger_turn": {"type": "boolean"},
                    "operation_id": {"type": "string", "minLength": 1},
                },
            },
            output_schema={"type": "object", "required": ["accepted", "delivery"]},
            permissions=["turn:followup"],
            parallel_safe=False,
            idempotency_required=True,
            allowed_callers=["main_agent", "system"],
        ),
        _followup_task,
    )
    registry.register(
        ToolDefinition(
            name="core.message.compose",
            description="Compose a user-facing assistant message from verified content.",
            input_schema={
                "type": "object",
                "required": ["message"],
                "properties": {"message": {"type": "string"}},
            },
            output_schema={"type": "object", "required": ["message"]},
            permissions=["conversation:write"],
            parallel_safe=True,
        ),
        lambda arguments, _context: {"message": str(arguments.get("message") or "")},
    )


__all__ = ["register_builtin_tools"]
