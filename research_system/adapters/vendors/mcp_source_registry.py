from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


McpTransport = Literal["stdio"]


@dataclass(frozen=True)
class McpToolDefinition:
    name: str
    summary: str = ""
    read_only: bool = True
    input_json_schema: dict[str, Any] | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class McpServerSource:
    id: str
    name: str
    transport: McpTransport
    command: str
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    tools: tuple[McpToolDefinition, ...] = ()
    timeout_seconds: float = 20.0
    notes: str = ""


class McpSourceRegistry:
    def __init__(
        self,
        settings=None,
        servers: list[McpServerSource] | None = None,
        config_path: Path | None = None,
    ) -> None:
        if servers is not None:
            resolved_servers = list(servers)
        else:
            resolved_servers = self._load_servers_from_path(
                config_path or getattr(settings, "mcp_servers_path", None)
            )
        self._servers = {server.id: server for server in resolved_servers}

    def get(self, server_id: str) -> McpServerSource | None:
        return self._servers.get(str(server_id or "").strip())

    def tool(self, server_id: str, tool_name: str) -> McpToolDefinition | None:
        server = self.get(server_id)
        if server is None:
            return None
        normalized_tool_name = str(tool_name or "").strip()
        for tool in server.tools:
            if str(tool.name).strip() == normalized_tool_name:
                return tool
        return None

    def list_all(self) -> list[McpServerSource]:
        return list(self._servers.values())

    @staticmethod
    def _load_servers_from_path(config_path: Path | None) -> list[McpServerSource]:
        if config_path is None or not Path(config_path).exists():
            return []
        raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        rows = list((raw_config.get("servers") if isinstance(raw_config, dict) else []) or [])
        servers: list[McpServerSource] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            server_id = str(row.get("id") or "").strip()
            name = str(row.get("name") or row.get("name_cn") or server_id).strip()
            command = str(row.get("command") or "").strip()
            if not server_id or not command:
                continue
            tools: list[McpToolDefinition] = []
            for tool_row in list(row.get("tools") or []):
                if not isinstance(tool_row, dict):
                    continue
                tool_name = str(tool_row.get("name") or "").strip()
                if not tool_name:
                    continue
                tools.append(
                    McpToolDefinition(
                        name=tool_name,
                        summary=str(tool_row.get("summary") or "").strip(),
                        read_only=bool(tool_row.get("read_only", True)),
                        input_json_schema=(
                            dict(tool_row.get("input_json_schema") or {})
                            if isinstance(tool_row.get("input_json_schema"), dict)
                            else None
                        ),
                        notes=tuple(
                            str(item).strip()
                            for item in list(tool_row.get("notes") or [])
                            if str(item).strip()
                        ),
                    )
                )
            servers.append(
                McpServerSource(
                    id=server_id,
                    name=name,
                    transport=str(row.get("transport") or "stdio").strip().lower(),  # type: ignore[arg-type]
                    command=command,
                    args=tuple(
                        str(item).strip()
                        for item in list(row.get("args") or [])
                        if str(item).strip()
                    ),
                    cwd=str(row.get("cwd") or "").strip() or None,
                    env={
                        str(key): str(value)
                        for key, value in dict(row.get("env") or {}).items()
                        if str(key).strip()
                    },
                    tools=tuple(tools),
                    timeout_seconds=float(row.get("timeout_seconds") or 20.0),
                    notes=str(row.get("notes") or "").strip(),
                )
            )
        return servers


__all__ = [
    "McpServerSource",
    "McpSourceRegistry",
    "McpToolDefinition",
]
