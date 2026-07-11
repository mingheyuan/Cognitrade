from __future__ import annotations

import inspect
from typing import Any

from research_system.runtime.domain_packs.registry import DomainPackRegistry


class PackSkillRuntime:
    """Run one version-pinned Pack Skill inside an existing Agent OS node."""

    def __init__(
        self,
        *,
        registry: DomainPackRegistry,
        settings: Any,
        domain_id: str,
        version: str | None = None,
        caller: str = "subagent",
    ) -> None:
        self.registry = registry
        self.settings = settings
        self.domain_id = domain_id
        self.version = version
        self.caller = caller

    def run(self, skill_name: str, **kwargs: Any) -> dict[str, Any]:
        definition = next(
            (
                item
                for item in self.registry.skill_definitions(
                    domain_id=self.domain_id,
                    version=self.version,
                )
                if item.skill_name == skill_name
            ),
            None,
        )
        if definition is None:
            raise ValueError(f"Skill is not declared by Pack: {self.domain_id}:{skill_name}")
        if self.caller not in definition.allowed_callers:
            raise PermissionError(
                f"Skill is not available to caller: {self.domain_id}:{skill_name}:{self.caller}"
            )
        skill_type = self.registry.resolve_skill(
            domain_id=self.domain_id,
            skill_name=skill_name,
            version=self.version,
        )
        if inspect.isclass(skill_type):
            skill = skill_type()
            result = skill.run(**kwargs)
        else:
            result = skill_type(
                skill_name=skill_name,
                settings=self.settings,
                arguments=dict(kwargs),
            )
        payload = dict(result or {})
        if definition.source_refs_required and not payload.get("source_refs"):
            raise ValueError(f"Skill produced no required source_refs: {skill_name}")
        return payload


__all__ = ["PackSkillRuntime"]
