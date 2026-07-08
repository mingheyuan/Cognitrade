from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class OpenApiOperationDefinition:
    source_id: str
    operation_id: str
    summary: str = ""
    read_only: bool = True
    input_json_schema: dict[str, Any] | None = None
    output_json_schema: dict[str, Any] | None = None
    capabilities: tuple[str, ...] = ()
    suitable_intents: tuple[str, ...] = ()
    stage: str | None = None
    candidate_subagent_type: str | None = None
    spec_uri: str | None = None
    spec: dict[str, Any] | None = None
    notes: tuple[str, ...] = ()


class OpenApiSourceRegistry:
    def __init__(
        self,
        settings=None,
        operations: list[OpenApiOperationDefinition] | None = None,
        config_path: Path | None = None,
    ) -> None:
        if operations is not None:
            resolved_operations = list(operations)
        else:
            resolved_operations = self._load_operations_from_path(
                config_path or getattr(settings, "openapi_sources_path", None)
            )
        self._operations = {
            (operation.source_id, operation.operation_id): operation
            for operation in resolved_operations
        }

    def get(self, source_id: str, operation_id: str) -> OpenApiOperationDefinition | None:
        return self._operations.get(
            (str(source_id or "").strip(), str(operation_id or "").strip())
        )

    def list_all(self) -> list[OpenApiOperationDefinition]:
        return list(self._operations.values())

    @staticmethod
    def _load_operations_from_path(config_path: Path | None) -> list[OpenApiOperationDefinition]:
        if config_path is None or not Path(config_path).exists():
            return []
        resolved_path = Path(config_path)
        raw_config = yaml.safe_load(resolved_path.read_text(encoding="utf-8")) or {}
        rows = list((raw_config.get("sources") if isinstance(raw_config, dict) else []) or [])
        operations: list[OpenApiOperationDefinition] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("source_id") or row.get("id") or "").strip()
            if not source_id:
                continue
            shared_spec_uri = OpenApiSourceRegistry._resolve_spec_uri(
                row.get("spec_uri"),
                config_path=resolved_path,
            )
            shared_spec = (
                dict(row.get("spec") or {})
                if isinstance(row.get("spec"), dict)
                else None
            )
            shared_notes = tuple(
                str(item).strip()
                for item in list(row.get("notes") or [])
                if str(item).strip()
            )
            for operation_row in list(row.get("operations") or []):
                if not isinstance(operation_row, dict):
                    continue
                operation_id = str(
                    operation_row.get("operation_id") or operation_row.get("id") or ""
                ).strip()
                if not operation_id:
                    continue
                operation_spec_uri = OpenApiSourceRegistry._resolve_spec_uri(
                    operation_row.get("spec_uri"),
                    config_path=resolved_path,
                ) or shared_spec_uri
                operation_spec = (
                    dict(operation_row.get("spec") or {})
                    if isinstance(operation_row.get("spec"), dict)
                    else (dict(shared_spec) if isinstance(shared_spec, dict) else None)
                )
                operations.append(
                    OpenApiOperationDefinition(
                        source_id=source_id,
                        operation_id=operation_id,
                        summary=str(operation_row.get("summary") or "").strip(),
                        read_only=bool(operation_row.get("read_only", True)),
                        input_json_schema=(
                            dict(operation_row.get("input_json_schema") or {})
                            if isinstance(operation_row.get("input_json_schema"), dict)
                            else None
                        ),
                        output_json_schema=(
                            dict(operation_row.get("output_json_schema") or {})
                            if isinstance(operation_row.get("output_json_schema"), dict)
                            else None
                        ),
                        capabilities=tuple(
                            str(item).strip()
                            for item in list(operation_row.get("capabilities") or [])
                            if str(item).strip()
                        ),
                        suitable_intents=tuple(
                            str(item).strip()
                            for item in list(operation_row.get("suitable_intents") or [])
                            if str(item).strip()
                        ),
                        stage=str(operation_row.get("stage") or "").strip() or None,
                        candidate_subagent_type=str(
                            operation_row.get("candidate_subagent_type") or ""
                        ).strip()
                        or None,
                        spec_uri=operation_spec_uri,
                        spec=operation_spec,
                        notes=(
                            *shared_notes,
                            *tuple(
                                str(item).strip()
                                for item in list(operation_row.get("notes") or [])
                                if str(item).strip()
                            ),
                        ),
                    )
                )
        return operations

    @staticmethod
    def _resolve_spec_uri(raw_value: object, *, config_path: Path) -> str | None:
        normalized = str(raw_value or "").strip()
        if not normalized:
            return None
        if normalized.startswith(("http://", "https://")):
            return normalized
        candidate = Path(normalized)
        if candidate.is_absolute():
            return str(candidate)
        return str((config_path.parent / candidate).resolve())


__all__ = [
    "OpenApiOperationDefinition",
    "OpenApiSourceRegistry",
]
