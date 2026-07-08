from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class HttpJsonEndpointDefinition:
    source_id: str
    endpoint_path: str
    http_method: str = "GET"
    summary: str = ""
    read_only: bool = True
    timeout_seconds: float | None = None
    input_json_schema: dict[str, Any] | None = None
    output_json_schema: dict[str, Any] | None = None
    capabilities: tuple[str, ...] = ()
    suitable_intents: tuple[str, ...] = ()
    stage: str | None = None
    candidate_subagent_type: str | None = None
    notes: tuple[str, ...] = ()


class HttpJsonSourceRegistry:
    def __init__(
        self,
        settings=None,
        endpoints: list[HttpJsonEndpointDefinition] | None = None,
        config_path: Path | None = None,
    ) -> None:
        if endpoints is not None:
            resolved_endpoints = list(endpoints)
        else:
            resolved_endpoints = self._load_endpoints_from_path(
                config_path or getattr(settings, "http_json_sources_path", None)
            )
        self._endpoints = {
            (
                str(endpoint.source_id).strip(),
                str(endpoint.http_method).strip().upper() or "GET",
                str(endpoint.endpoint_path).strip(),
            ): endpoint
            for endpoint in resolved_endpoints
        }

    def get(
        self,
        source_id: str,
        endpoint_path: str,
        http_method: str = "GET",
    ) -> HttpJsonEndpointDefinition | None:
        return self._endpoints.get(
            (
                str(source_id or "").strip(),
                str(http_method or "").strip().upper() or "GET",
                str(endpoint_path or "").strip(),
            )
        )

    def list_all(self) -> list[HttpJsonEndpointDefinition]:
        return list(self._endpoints.values())

    @staticmethod
    def _load_endpoints_from_path(config_path: Path | None) -> list[HttpJsonEndpointDefinition]:
        if config_path is None or not Path(config_path).exists():
            return []
        raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        rows = list((raw_config.get("sources") if isinstance(raw_config, dict) else []) or [])
        endpoints: list[HttpJsonEndpointDefinition] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("source_id") or row.get("id") or "").strip()
            if not source_id:
                continue
            shared_summary = str(row.get("summary") or "").strip()
            shared_read_only = bool(row.get("read_only", True))
            shared_timeout_seconds = HttpJsonSourceRegistry._normalize_timeout_seconds(
                row.get("timeout_seconds")
            )
            shared_capabilities = tuple(
                str(item).strip()
                for item in list(row.get("capabilities") or [])
                if str(item).strip()
            )
            shared_suitable_intents = tuple(
                str(item).strip()
                for item in list(row.get("suitable_intents") or [])
                if str(item).strip()
            )
            shared_stage = str(row.get("stage") or "").strip() or None
            shared_candidate_subagent_type = str(row.get("candidate_subagent_type") or "").strip() or None
            shared_notes = tuple(
                str(item).strip()
                for item in list(row.get("notes") or [])
                if str(item).strip()
            )
            for endpoint_row in list(row.get("endpoints") or []):
                if not isinstance(endpoint_row, dict):
                    continue
                endpoint_path = str(
                    endpoint_row.get("endpoint_path")
                    or endpoint_row.get("path")
                    or ""
                ).strip()
                http_method = str(
                    endpoint_row.get("http_method")
                    or endpoint_row.get("method")
                    or "GET"
                ).strip().upper() or "GET"
                if not endpoint_path or http_method not in {"GET", "POST"}:
                    continue
                endpoints.append(
                    HttpJsonEndpointDefinition(
                        source_id=source_id,
                        endpoint_path=endpoint_path,
                        http_method=http_method,
                        summary=str(endpoint_row.get("summary") or shared_summary).strip(),
                        read_only=bool(endpoint_row.get("read_only", shared_read_only)),
                        timeout_seconds=HttpJsonSourceRegistry._normalize_timeout_seconds(
                            endpoint_row.get("timeout_seconds")
                        )
                        or shared_timeout_seconds,
                        input_json_schema=(
                            dict(endpoint_row.get("input_json_schema") or {})
                            if isinstance(endpoint_row.get("input_json_schema"), dict)
                            else None
                        ),
                        output_json_schema=(
                            dict(endpoint_row.get("output_json_schema") or {})
                            if isinstance(endpoint_row.get("output_json_schema"), dict)
                            else None
                        ),
                        capabilities=tuple(
                            str(item).strip()
                            for item in list(endpoint_row.get("capabilities") or shared_capabilities)
                            if str(item).strip()
                        ),
                        suitable_intents=tuple(
                            str(item).strip()
                            for item in list(endpoint_row.get("suitable_intents") or shared_suitable_intents)
                            if str(item).strip()
                        ),
                        stage=str(endpoint_row.get("stage") or shared_stage or "").strip() or None,
                        candidate_subagent_type=str(
                            endpoint_row.get("candidate_subagent_type")
                            or shared_candidate_subagent_type
                            or ""
                        ).strip()
                        or None,
                        notes=(
                            *shared_notes,
                            *tuple(
                                str(item).strip()
                                for item in list(endpoint_row.get("notes") or [])
                                if str(item).strip()
                            ),
                        ),
                    )
                )
        return endpoints

    @staticmethod
    def _normalize_timeout_seconds(raw_value: object) -> float | None:
        if raw_value is None or raw_value == "":
            return None
        try:
            normalized = float(raw_value)
        except (TypeError, ValueError):
            return None
        if normalized <= 0:
            return None
        return normalized


__all__ = [
    "HttpJsonEndpointDefinition",
    "HttpJsonSourceRegistry",
]
