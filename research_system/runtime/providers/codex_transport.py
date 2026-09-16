"""One owned SDK runtime per attempt; optional scoped frozen-read tools only.

Authentication is managed by Codex. Never read auth.json, copy a token into an
HTTP client, or fall back to an API-key provider. Only safe execution metadata
leaves this adapter; the caller owns persistence and the retry budget.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import tempfile
from contextlib import aclosing
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

import tomllib
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.generated.v2_all import ReasoningEffort

from .errors import (
    CodexTurnError,
    LLMCancelledError,
    LLMDeadlineError,
    LLMProviderError,
    LLMUnavailableError,
)

TRANSPORT_VERSION = "codex-sdk-frozen-stage-v3-terminal-trace"
ISOLATION = {
    "model_provider": "openai", "forced_login_method": "chatgpt",
    "web_search": "disabled", "approval_policy": "never", "sandbox_mode": "read-only",
    "features.shell_tool": False, "features.unified_exec": False,
    "features.multi_agent": False, "features.plugins": False, "features.apps": False,
    "features.memories": False, "features.in_app_browser": False,
    "features.browser_use": False, "features.browser_use_external": False,
    "features.code_mode.enabled": False,
    "memories.generate_memories": False, "memories.use_memories": False,
    "project_doc_max_bytes": 0, "tools.view_image": False,
}


def execution_identity() -> dict[str, Any]:
    source = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        source.update(path.name.encode())
        source.update(path.read_bytes())
    return {"provider": "codex", "transport_version": TRANSPORT_VERSION,
            "sdk_version": version("openai-codex"),
            "runtime_version": version("openai-codex-cli-bin"),
            "adapter_source_hash": source.hexdigest(),
            "isolation_hash": hashlib.sha256(json.dumps(ISOLATION, sort_keys=True).encode()).hexdigest()}


def runtime_config(cwd: str) -> CodexConfig:
    # The SDK merges env into its parent environment. Explicitly blank legacy
    # transport variables without changing the host's global environment.
    environment = {key: "" for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY")}
    overrides = dict(ISOLATION)
    # Disable explicitly configured MCP servers too: an empty TOML table would
    # merge with user config and leave its children enabled. Do not inspect auth.
    config_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    config_file = config_root / "config.toml"
    if config_file.exists():
        with config_file.open("rb") as handle:
            config = tomllib.load(handle)
        for name in config.get("mcp_servers", {}):
            if "." in name:
                raise LLMUnavailableError("codex_config_requires_isolated_mcp_names")
            overrides[f"mcp_servers.{name}.enabled"] = False
        for name in config.get("plugins", {}):
            if "." in name:
                raise LLMUnavailableError("codex_config_requires_isolated_plugin_names")
            overrides[f"plugins.{name}.enabled"] = False
    # Disable local skill discovery as well as plugins. Their catalog text is
    # otherwise injected even when a turn never calls a tool.
    skill_paths = sorted({str(p.parent) for root in
        (config_root / "skills", Path.home() / ".agents" / "skills")
        if root.exists() for p in root.rglob("SKILL.md")})
    skill_config = "skills.config=[" + ",".join(
        "{path=" + json.dumps(path) + ",enabled=false}" for path in skill_paths) + "]"
    return CodexConfig(cwd=cwd, env=environment, client_name="cognitrade",
                       config_overrides=tuple(f"{k}={json.dumps(v)}" for k, v in overrides.items()) + (skill_config,))


def account_type(account: Any) -> str | None:
    return getattr(getattr(account.account, "root", account.account), "type", None)


async def close_owned_runtime(client) -> None:
    # SDK 0.154.0 waits on terminate, but its kill fallback does not reap the
    # child. Keep this pinned compatibility shim in one place and wait before
    # an outer recovery can acquire the released model slot.
    sync = getattr(getattr(client, "_client", None), "_sync", None)
    process = getattr(sync, "_proc", None)
    await client.close()
    if process is not None:
        try:
            await asyncio.to_thread(process.wait, timeout=2)
        except Exception:  # noqa: BLE001 — cleanup is a hard stop, not content repair
            raise CodexTurnError("codex_runtime_exit_unconfirmed") from None


async def health() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="cognitrade-codex-health-") as cwd:
        async with AsyncCodex(runtime_config(cwd)) as client:
            account = await client.account()
            auth = account_type(account)
            models = await client.models(include_hidden=True) if auth == "chatgpt" else None
            return {**execution_identity(), "auth_type": auth, "authenticated": auth == "chatgpt",
                    "models": [row.model for row in models.data] if models else []}


def turn_error(error: dict[str, Any]) -> CodexTurnError:
    # Some model validation errors arrive as a JSON message with info=other.
    # Extract only finite codes/status, never log the raw message or body.
    try:
        detail = json.loads(error.get("message") or "")
    except (ValueError, TypeError):
        detail = {}
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        code = detail["error"].get("code")
        if code in {"invalid_json_schema", "invalid_api_key", "model_not_found", "permission_denied",
                    "insufficient_quota", "rate_limit_exceeded"}:
            status = detail.get("status")
            return CodexTurnError(code, status_code=status if type(status) is int else None)
    info = error.get("codexErrorInfo") or error.get("codex_error_info") or "unknown"
    code = info if isinstance(info, str) else next(iter(info), "unknown") if isinstance(info, dict) else "unknown"
    # Only known protocol variants, not arbitrary server strings, enter logs.
    allowed = {"contextWindowExceeded", "usageLimitExceeded", "httpConnectionFailed",
               "responseStreamConnectionFailed", "responseStreamDisconnected", "responseTooManyFailedAttempts",
               "serverOverloaded", "unauthorized", "badRequest", "sandboxError", "internalServerError",
               "rateLimitExceeded", "sessionBudgetExceeded"}
    code = code if code in allowed else "codex_turn_failed"
    status = None
    if isinstance(info, dict):
        for value in info.values():
            if isinstance(value, dict):
                candidate = value.get("httpStatusCode")
                if type(candidate) is int and 100 <= candidate <= 599:
                    status = candidate
    if code in {"usageLimitExceeded", "sessionBudgetExceeded"}:
        code = "insufficient_quota"
    if code == "rateLimitExceeded":
        code, status = "rate_limit_exceeded", 429
    if code == "unauthorized":
        status = 401
    return CodexTurnError(code, status_code=status)


async def execute(*, model: str, instructions: str, prompt: str,
                  output_schema: dict | None, effort: str | None, timeout: float,
                  deadline_at: float | None = None, cancellation: Event | None = None,
                  frozen_session=None) -> dict[str, Any]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    started = monotonic()
    request_deadline = started + timeout
    bridge = None
    metadata = {**execution_identity(), "model": model, "reasoning_effort": effort,
                "status": "running"}
    trace = {"phase": "starting", "event_count": 0, "event_counts": {},
             "output_characters": 0, "runtime_closed": False,
             "input_characters": len(instructions) + len(prompt),
             "structured_output": output_schema is not None}
    metadata["request_trace"] = trace
    last_event_at = None
    known_events = {
        "turn/started", "turn/completed", "item/started", "item/completed",
        "item/agentMessage/delta", "item/reasoning/textDelta",
        "item/reasoning/summaryTextDelta", "thread/tokenUsage/updated",
        "model/rerouted", "error", "thread/status/changed",
    }

    def capture_trace() -> None:
        now = monotonic()
        metadata["elapsed_seconds"] = round(now - started, 6)
        trace["idle_seconds"] = round(now - (last_event_at or started), 6)
        trace["output_idle_seconds"] = (round(now - started - trace["last_output_seconds"], 6)
                                         if "last_output_seconds" in trace else None)
        trace["generation_state"] = (
            "final_received" if "final_output_characters" in trace else
            "output_incomplete" if trace["output_characters"] else "no_output"
        )

    def capture_stop() -> None:
        capture_trace()
        # Interruption can emit more events. Keep the actual failure instant
        # separate from cleanup, instead of misreading interrupted completion
        # as proof that the answer had completed before the deadline.
        trace["stop_snapshot"] = deepcopy({key: value for key, value in trace.items()
                                           if key not in {"stop_snapshot", "runtime_closed"}})
        trace["stop_snapshot"]["elapsed_seconds"] = metadata["elapsed_seconds"]

    def record_event(method: str, payload: dict) -> None:
        nonlocal last_event_at
        last_event_at = monotonic()
        name = method if method in known_events else "other"
        trace["event_count"] += 1
        trace["event_counts"][name] = trace["event_counts"].get(name, 0) + 1
        trace["last_event"] = name
        trace["last_event_seconds"] = round(last_event_at - started, 6)
        trace.setdefault("first_event_seconds", trace["last_event_seconds"])
        if method == "item/agentMessage/delta" and isinstance(payload.get("delta"), str):
            trace["output_characters"] += len(payload["delta"])
            trace["last_output_seconds"] = trace["last_event_seconds"]
            trace.setdefault("first_output_seconds", trace["last_output_seconds"])
        if method == "turn/completed":
            terminal_status = (payload.get("turn") or {}).get("status")
            trace["terminal_status"] = terminal_status if terminal_status in {"completed", "interrupted", "failed"} else "unknown"
            trace["terminal_seconds"] = trace["last_event_seconds"]
        if method == "thread/tokenUsage/updated":
            usage = payload.get("tokenUsage") or {}
            trace["token_usage"] = {
                period: {key: value for key, value in (usage.get(period) or {}).items()
                         if key in {"inputTokens", "outputTokens", "totalTokens",
                                    "cachedInputTokens", "reasoningOutputTokens"}
                         and type(value) is int and value >= 0}
                for period in ("total", "last")
            }

    def check() -> None:
        if cancellation is not None and cancellation.is_set():
            raise LLMCancelledError("llm_request_cancelled")
        if deadline_at is not None and monotonic() >= deadline_at:
            raise LLMDeadlineError("llm_stage_deadline_exceeded")
        if monotonic() >= request_deadline:
            raise LLMProviderError("llm_request_timeout")
        if bridge is not None and bridge.failure is not None:
            raise bridge.failure

    check()
    with tempfile.TemporaryDirectory(prefix="cognitrade-codex-stage-") as cwd:
        client = AsyncCodex(runtime_config(cwd))
        if frozen_session is not None:
            from .codex_frozen_tools import FrozenToolBridge, install_handler
            bridge = FrozenToolBridge(frozen_session, check)
            install_handler(client, bridge)
        handle = None
        task = None

        async def run() -> dict[str, Any]:
            nonlocal handle
            await client.__aenter__()
            trace["phase"] = "account_check"
            account = await client.account()
            if account_type(account) != "chatgpt":
                raise LLMUnavailableError("llm_provider_unavailable: codex_chatgpt_login_required")
            trace["phase"] = "thread_start"
            if bridge is not None:
                from .codex_frozen_tools import start_thread
                thread = await start_thread(client, bridge, model=model, instructions=instructions, cwd=cwd)
            else:
                thread = await client.thread_start(model=model, model_provider="openai",
                    base_instructions=instructions, developer_instructions="",
                    cwd=cwd, ephemeral=True, sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all)
            metadata["thread_id"] = thread.id
            trace["phase"] = "turn_start"
            handle = await thread.turn(prompt, model=model, output_schema=output_schema,
                effort=ReasoningEffort(effort) if effort else None,
                approval_mode=ApprovalMode.deny_all)
            metadata["turn_id"] = handle.id
            trace["phase"] = "streaming"
            final = None
            usage = None
            terminal = None
            item_types: list[str] = []
            async with aclosing(handle.stream()) as stream:
                async for event in stream:
                    payload = event.payload.model_dump(mode="json", by_alias=True)
                    record_event(event.method, payload)
                    if event.method == "model/rerouted":
                        raise CodexTurnError("model_rerouted")
                    if event.method == "item/completed":
                        item = payload.get("item") or {}
                        kind = item.get("type")
                        item_types.append(kind)
                        if kind == "dynamicToolCall" and bridge is not None:
                            bridge.verify_completed(item)
                        elif kind in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
                                    "webSearch", "collabAgentToolCall", "imageGeneration"}:
                            raise CodexTurnError("unexpected_tool_execution")
                        if kind == "agentMessage" and item.get("phase") != "commentary":
                            final = item.get("text")
                            if isinstance(final, str):
                                trace["final_output_characters"] = len(final)
                                trace["final_output_sha256"] = hashlib.sha256(final.encode()).hexdigest()
                    elif event.method == "thread/tokenUsage/updated":
                        usage = payload.get("tokenUsage")
                    elif event.method == "turn/completed":
                        terminal = payload.get("turn") or {}
                        break
            if terminal is None:
                raise CodexTurnError("missing_terminal_event")
            if terminal.get("status") == "failed":
                raise turn_error(terminal.get("error") or {})
            if terminal.get("status") == "interrupted":
                raise LLMCancelledError("llm_request_cancelled")
            if terminal.get("status") != "completed" or not isinstance(final, str) or not final.strip():
                raise CodexTurnError("missing_final_response")
            metadata.update(item_types=item_types, usage=usage)
            if "stop_snapshot" not in trace:
                metadata["status"] = "completed"
            if frozen_session is not None:
                metadata["frozen_read_audit"] = frozen_session.audit()
            capture_trace()
            return {"output_text": final, "metadata": metadata}

        try:
            task = asyncio.create_task(run())
            while not task.done():
                check()
                await asyncio.wait({task}, timeout=0.1)
            check()
            return await task
        except (LLMProviderError, asyncio.CancelledError) as exc:
            metadata["status"] = (
                "cancelled" if isinstance(exc, (LLMCancelledError, asyncio.CancelledError)) else
                "deadline" if isinstance(exc, LLMDeadlineError) else
                "timed_out" if str(exc) == "llm_request_timeout" else "failed"
            )
            capture_stop()
            exc.codex_metadata = metadata
            if frozen_session is not None:
                exc.frozen_read_audit = frozen_session.audit()
            raise
        except Exception as exc:  # noqa: BLE001 — sanitize SDK error boundary
            # Do not propagate RPC messages, stderr or secret-bearing causes.
            error = CodexTurnError(f"codex_{type(exc).__name__}")
            metadata["status"] = "failed"
            capture_stop()
            error.codex_metadata = metadata
            if frozen_session is not None:
                error.frozen_read_audit = frozen_session.audit()
            raise error from None
        finally:
            if task is not None and not task.done() and handle is not None:
                trace["interrupt_requested"] = True
                try:
                    await asyncio.wait_for(handle.interrupt(), timeout=2)
                    trace["interrupt_acknowledged"] = True
                    await asyncio.wait({task}, timeout=2)
                except Exception:  # noqa: BLE001 — close runtime even if interrupt fails
                    logging.getLogger(__name__).warning("Codex interrupt not acknowledged; closing owned runtime")
            # Closing the owned runtime precedes task cancellation: SDK stream
            # readers blocked in to_thread must see EOF, not leak indefinitely.
            try:
                await close_owned_runtime(client)
            except CodexTurnError as exc:
                metadata["status"] = "cleanup_failed"
                exc.codex_metadata = metadata
                raise
            trace["runtime_closed"] = True
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
