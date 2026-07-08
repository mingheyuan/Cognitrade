from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SkillSourceDefinition:
    skill_name: str
    registration_type: str
    summary: str = ""
    snapshot_type: str | None = None
    capabilities: tuple[str, ...] = ()
    suitable_intents: tuple[str, ...] = ()
    stage: str | None = None
    candidate_subagent_type: str | None = None
    input_json_schema: dict[str, Any] | None = None
    output_json_schema: dict[str, Any] | None = None
    notes: tuple[str, ...] = ()


class SkillSourceRegistry:
    def __init__(
        self,
        settings=None,
        sources: list[SkillSourceDefinition] | None = None,
        config_path: Path | None = None,
    ) -> None:
        if sources is not None:
            resolved_sources = list(sources)
        else:
            resolved_sources = self._load_sources_from_path(
                config_path or getattr(settings, "skill_sources_path", None)
            )
        self._sources = list(resolved_sources)

    def get(self, skill_name: str) -> SkillSourceDefinition | None:
        normalized_skill_name = str(skill_name or "").strip()
        for source in self._sources:
            if str(source.skill_name).strip() == normalized_skill_name:
                return source
        return None

    def list_all(self) -> list[SkillSourceDefinition]:
        return list(self._sources)

    @staticmethod
    def _load_sources_from_path(config_path: Path | None) -> list[SkillSourceDefinition]:
        if config_path is None or not Path(config_path).exists():
            return []
        raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        rows = list((raw_config.get("sources") if isinstance(raw_config, dict) else []) or [])
        sources: list[SkillSourceDefinition] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            skill_name = str(row.get("skill_name") or "").strip()
            registration_type = str(row.get("registration_type") or "").strip()
            if not skill_name or not registration_type:
                continue
            sources.append(
                SkillSourceDefinition(
                    skill_name=skill_name,
                    registration_type=registration_type,
                    summary=str(row.get("summary") or "").strip(),
                    snapshot_type=str(row.get("snapshot_type") or "").strip() or None,
                    capabilities=tuple(
                        str(item).strip()
                        for item in list(row.get("capabilities") or [])
                        if str(item).strip()
                    ),
                    suitable_intents=tuple(
                        str(item).strip()
                        for item in list(row.get("suitable_intents") or [])
                        if str(item).strip()
                    ),
                    stage=str(row.get("stage") or "").strip() or None,
                    candidate_subagent_type=str(row.get("candidate_subagent_type") or "").strip() or None,
                    input_json_schema=(
                        dict(row.get("input_json_schema") or {})
                        if isinstance(row.get("input_json_schema"), dict)
                        else None
                    ),
                    output_json_schema=(
                        dict(row.get("output_json_schema") or {})
                        if isinstance(row.get("output_json_schema"), dict)
                        else None
                    ),
                    notes=tuple(
                        str(item).strip()
                        for item in list(row.get("notes") or [])
                        if str(item).strip()
                    ),
                )
            )
        return sources


__all__ = [
    "SkillSourceDefinition",
    "SkillSourceRegistry",
]
