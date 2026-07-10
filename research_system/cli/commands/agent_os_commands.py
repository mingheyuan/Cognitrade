from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_system.runtime.agent_os import AgentOS
from research_system.schemas.artifacts import ArtifactDraft


def execute_agent_os_command(settings, args) -> dict[str, Any] | list[dict[str, Any]]:
    agent_os = AgentOS(settings)
    command = args.command
    if command == "domain-packs":
        return agent_os.list_domain_packs()
    if command == "domain-run":
        result = agent_os.run_domain(
            domain_id=args.domain_id,
            objective=args.objective,
            input_artifacts=[_parse_artifact(value) for value in args.artifact],
            required_artifact_types=list(args.require or []),
            preferred_capabilities=list(args.prefer_capability or []),
            run_id=args.run_id,
        )
        return result.model_dump(mode="json")
    if command == "domain-inspect":
        return agent_os.inspect_run(args.run_id)
    if command == "domain-replay":
        return agent_os.replay(args.run_id).model_dump(mode="json")
    if command == "domain-resume":
        return agent_os.resume(args.run_id).model_dump(mode="json")
    if command == "domain-rerun":
        return agent_os.rerun_from(args.run_id, args.node_id).model_dump(mode="json")
    if command == "domain-pause":
        return {"run_id": args.run_id, "pause_requested": agent_os.pause(args.run_id)}
    if command == "domain-cancel":
        return {"run_id": args.run_id, "cancel_requested": agent_os.cancel(args.run_id)}
    raise ValueError(f"unsupported Agent OS command: {command}")


def _parse_artifact(value: str) -> ArtifactDraft:
    artifact_type, separator, raw_value = str(value or "").partition("=")
    artifact_type = artifact_type.strip()
    if not separator or not artifact_type or not raw_value.strip():
        raise ValueError("artifact must use ARTIFACT_TYPE=JSON_OR_@PATH")
    raw_value = raw_value.strip()
    source_refs: list[dict[str, Any]] = []
    if raw_value.startswith("@"):
        path = Path(raw_value[1:]).expanduser().resolve()
        text = path.read_text(encoding="utf-8")
        source_refs.append({"type": "file", "path": str(path)})
        try:
            content = json.loads(text)
        except json.JSONDecodeError:
            content = {"text": text}
    else:
        try:
            content = json.loads(raw_value)
        except json.JSONDecodeError:
            content = {"text": raw_value}
    if not isinstance(content, dict):
        content = {"value": content}
    return ArtifactDraft(
        artifact_type=artifact_type,
        content=content,
        source_refs=source_refs or [{"type": "cli", "id": artifact_type}],
    )


__all__ = ["execute_agent_os_command"]
