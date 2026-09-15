"""Pinned SDK 0.154 dynamic-tool bridge; never grants general tool authority."""
from __future__ import annotations

from openai_codex import AsyncThread

from .errors import CodexTurnError
from .frozen_documents import encoded


class FrozenToolBridge:
    def __init__(self, session, check):
        self.session = session
        self.check = check
        self.thread_id = None
        self.failure = None
        self.completed_ids = set()
        self.calls = 0

    def handle(self, method, params):
        # SDK's default approval handler accepts commands. Never delegate to it.
        try:
            self.check()
            if method != "item/tool/call" or not isinstance(params, dict):
                raise CodexTurnError("unexpected_tool_request")
            if not self.thread_id or params.get("threadId") != self.thread_id:
                raise CodexTurnError("frozen_tool_scope_mismatch")
            call_id = params.get("callId")
            if not isinstance(call_id, str) or call_id in self.completed_ids:
                raise CodexTurnError("frozen_tool_call_mismatch")
            self.calls += 1
            if self.calls > 128:
                raise CodexTurnError("frozen_tool_call_budget_exceeded")
            if params.get("tool") not in {row["name"] for row in self.session.tools}:
                raise CodexTurnError("unexpected_tool_execution")
            try:
                body = self.session.call(params["tool"], params.get("arguments"))
                success = True
            except ValueError as exc:
                # Finite machine errors, not argument contents, reach the model.
                body, success = {"error": str(exc)}, False
            self.check()
            self.completed_ids.add(call_id)
            return {"contentItems": [{"type": "inputText", "text": encoded(body)}], "success": success}
        except Exception as exc:  # noqa: BLE001 — stop main task without killing SDK reader
            self.failure = exc
            if method == "item/permissions/requestApproval":
                return {"permissions": {}, "scope": "turn"}
            if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                return {"decision": "decline"}
            return {"contentItems": [{"type": "inputText", "text": "Frozen tool request rejected."}], "success": False}

    def verify_completed(self, item):
        if item.get("id") not in self.completed_ids or item.get("tool") not in {
            row["name"] for row in self.session.tools
        }:
            raise CodexTurnError("frozen_tool_completion_mismatch")


def install_handler(client, bridge):
    # 0.154 exposes the handler on CodexClient, but not AsyncCodex. Keep this
    # narrow compatibility shim here until the high-level SDK exposes it.
    client._client._sync._approval_handler = bridge.handle


async def start_thread(client, bridge, *, model, instructions, cwd):
    # Use SDK generated/protocol support rather than hand-rolled RPC/HTTP.
    started = await client._client.thread_start({
        "model": model, "modelProvider": "openai", "baseInstructions": instructions,
        "developerInstructions": "", "cwd": cwd, "ephemeral": True,
        "sandbox": "read-only", "approvalPolicy": "never", "dynamicTools": bridge.session.tools,
    })
    bridge.thread_id = started.thread.id
    return AsyncThread(client, started.thread.id)
