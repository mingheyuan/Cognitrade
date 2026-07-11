from __future__ import annotations

import json
import os
import subprocess
from time import monotonic
from typing import Any

from research_system.adapters.vendors.mcp_source_registry import McpServerSource


class McpToolInvocationError(RuntimeError):
    pass


def invoke_mcp_tool(
    *,
    server_source: McpServerSource,
    tool_name: str,
    arguments: dict[str, Any],
    timeout_seconds: float,
    cancellation=None,
) -> dict[str, Any]:
    """Invoke one configured stdio MCP tool inside the current Tool boundary."""
    request_bytes = b"".join(
        _encode_message(message)
        for message in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cognitrade-agent-os", "version": "1"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": dict(arguments or {})},
            },
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}},
            {"jsonrpc": "2.0", "method": "exit", "params": {}},
        )
    )
    process = subprocess.Popen(
        [server_source.command, *list(server_source.args)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=server_source.cwd or None,
        env={**os.environ, **dict(server_source.env or {})},
    )
    stdout = b""
    stderr = b""
    sent = False
    started = monotonic()
    try:
        while process.poll() is None:
            if cancellation is not None and cancellation.is_set():
                process.kill()
                process.communicate()
                raise McpToolInvocationError(
                    f"mcp tool invocation cancelled: {server_source.id}/{tool_name}"
                )
            if monotonic() - started >= timeout_seconds:
                process.kill()
                process.communicate()
                raise McpToolInvocationError(
                    f"mcp tool invocation timed out: {server_source.id}/{tool_name}"
                )
            try:
                out, err = process.communicate(
                    input=None if sent else request_bytes,
                    timeout=min(0.1, max(timeout_seconds - (monotonic() - started), 0.01)),
                )
                stdout += out or b""
                stderr += err or b""
                sent = True
                break
            except subprocess.TimeoutExpired as exc:
                sent = True
                stdout += exc.output or b""
                stderr += exc.stderr or b""
        if process.poll() is None:
            out, err = process.communicate(timeout=max(timeout_seconds, 0.1))
            stdout += out or b""
            stderr += err or b""
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    if process.returncode not in {0, None}:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise McpToolInvocationError(
            f"mcp tool invocation failed: {server_source.id}/{tool_name}"
            + (f" stderr={detail}" if detail else "")
        )
    responses = {
        message.get("id"): message
        for message in _parse_messages(stdout)
        if isinstance(message, dict) and "id" in message
    }
    response = responses.get(2)
    if not isinstance(response, dict):
        raise McpToolInvocationError(
            f"mcp tool invocation missing tools/call response: {server_source.id}/{tool_name}"
        )
    if isinstance(response.get("error"), dict):
        error = response["error"]
        raise McpToolInvocationError(
            f"mcp tool invocation returned error: {server_source.id}/{tool_name} "
            f"{error.get('message') or error}"
        )
    result = response.get("result")
    if not isinstance(result, dict):
        return {"result": result}
    if result.get("isError"):
        raise McpToolInvocationError(
            f"mcp tool invocation returned tool error: {server_source.id}/{tool_name}"
        )
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return dict(structured)
    content = result.get("content")
    if isinstance(content, list):
        text_rows = [
            str(item.get("text") or "").strip()
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if len(text_rows) == 1 and text_rows[0]:
            try:
                decoded = json.loads(text_rows[0])
            except json.JSONDecodeError:
                return {"text": text_rows[0]}
            return decoded if isinstance(decoded, dict) else {"result": decoded}
    return dict(result)


def _encode_message(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return (
        f"Content-Length: {len(payload)}\r\n"
        "Content-Type: application/json\r\n\r\n"
    ).encode("ascii") + payload


def _parse_messages(buffer: bytes) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    offset = 0
    while offset < len(buffer):
        header_end = buffer.find(b"\r\n\r\n", offset)
        if header_end < 0:
            break
        headers = buffer[offset:header_end].decode("ascii", errors="replace")
        length = None
        for header in headers.split("\r\n"):
            key, separator, value = header.partition(":")
            if separator and key.lower() == "content-length":
                length = int(value.strip())
        if length is None:
            break
        body_start = header_end + 4
        body_end = body_start + length
        if body_end > len(buffer):
            break
        payload = json.loads(buffer[body_start:body_end].decode("utf-8"))
        if isinstance(payload, dict):
            messages.append(payload)
        offset = body_end
    return messages


__all__ = ["McpToolInvocationError", "invoke_mcp_tool"]
