"""Codex-only adapter preserving the Agent OS's orchestration contract."""
from __future__ import annotations

import asyncio
import json
from threading import Event, local
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from . import codex_transport
from .errors import LLMUnavailableError, StructuredOutputError
from .structured_output import strict_schema

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class CodexProvider:
    deterministic_core_routing = True
    supports_embeddings = False
    supports_frozen_reading = True

    def __init__(self, settings):
        self.settings = settings
        self.request_timeout_seconds = float(settings.llm_request_timeout_seconds)
        self._calls = local()

    @property
    def available(self) -> bool:
        # Runtime presence is cheap; authentication is checked before every
        # turn and by explicit health, not a paid probe during application boot.
        return bool(getattr(self.settings, "codex_enabled", True))

    @property
    def last_call_metadata(self) -> dict:
        return dict(getattr(self._calls, "metadata", {}))

    def health(self) -> dict:
        return asyncio.run(asyncio.wait_for(codex_transport.health(), timeout=20))

    async def execute_async(self, *, system_prompt: str, user_prompt: str,
                            output_schema: dict | None = None, timeout_seconds: float | None = None,
                            reasoning_effort: str | None = None, cancellation: Event | None = None,
                            deadline_at: float | None = None, frozen_corpus=None,
                            output_repetition_guard: bool = False) -> dict:
        if not self.available:
            raise LLMUnavailableError("llm_provider_unavailable: codex_explicitly_disabled")
        return await codex_transport.execute(model=self.settings.default_model,
            instructions=system_prompt, prompt=user_prompt, output_schema=output_schema,
            effort=reasoning_effort or self.settings.codex_reasoning_effort,
            timeout=self.request_timeout_seconds if timeout_seconds is None else float(timeout_seconds),
            deadline_at=deadline_at, cancellation=cancellation,
            output_repetition_guard=output_repetition_guard,
            **({"frozen_session": frozen_corpus.session()} if frozen_corpus is not None else {}))

    def _execute(self, **kwargs) -> str:
        self._calls.metadata = {}
        try:
            result = asyncio.run(self.execute_async(**kwargs))
        except Exception as exc:
            self._calls.metadata = dict(getattr(exc, "codex_metadata", {}))
            if audit := getattr(exc, "frozen_read_audit", None):
                self._calls.metadata["frozen_read_audit"] = audit
            raise
        self._calls.metadata = result["metadata"]
        return result["output_text"]

    def parse(self, **kwargs) -> SchemaT:
        return self.parse_cancellable(**kwargs)

    def parse_cancellable(self, *, system_prompt: str, user_prompt: str,
                          schema: type[SchemaT], temperature: float = 0.2,
                          reasoning_effort: str | None = None, timeout_seconds: float | None = None,
                          max_retries: int | None = None, cancellation: Event | None = None,
                          deadline_at: float | None = None, frozen_corpus=None,
                          validation_context: dict | None = None,
                          output_repetition_guard: bool = False,
                          json_schema_override: dict | None = None) -> SchemaT:
        # Temperature is a legacy caller argument, not a supported Codex knob.
        # Preserve caller compatibility without claiming it was applied.
        original_schema = schema.model_json_schema() if json_schema_override is None else json_schema_override
        output_schema = strict_schema(original_schema)
        if output_schema is None:
            user_prompt += "\nReturn only JSON matching this schema (preserve arbitrary map keys):\n" + json.dumps(original_schema, ensure_ascii=False)
        text = self._execute(system_prompt=system_prompt, user_prompt=user_prompt,
            output_schema=output_schema, reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds, cancellation=cancellation, deadline_at=deadline_at,
            output_repetition_guard=output_repetition_guard,
            **({"frozen_corpus": frozen_corpus} if frozen_corpus is not None else {}))
        try:
            return schema.model_validate_json(text, context=validation_context)
        except ValidationError as exc:
            raise StructuredOutputError("model structured output failed schema validation") from exc

    def complete(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.2,
                 reasoning_effort: str | None = None, force_json: bool = False,
                 timeout_seconds: float | None = None, max_retries: int | None = None,
                 max_continuation_attempts: int | None = None) -> str:
        return self._execute(system_prompt=system_prompt,
            user_prompt=user_prompt + ("\nReturn only a JSON object." if force_json else ""),
            reasoning_effort=reasoning_effort, timeout_seconds=timeout_seconds)

    def complete_with_tools(self, *, system_prompt: str, user_prompt: str,
                            tools: list[dict], messages: list[dict] | None = None,
                            temperature: float = 0.2, reasoning_effort: str | None = None) -> dict:
        # This is a decision envelope, not SDK tool execution. Core validates
        # each requested tool and executes it under existing governance.
        schema = {"type": "object", "additionalProperties": False,
            "properties": {"kind": {"type": "string", "enum": ["message", "tool_calls"]},
                "message": {"type": "string"}, "tool_calls": {"type": "array", "items": {
                    "type": "object", "properties": {"tool_name": {"type": "string"},
                        "arguments_json": {"type": "string"}},
                    "required": ["tool_name", "arguments_json"], "additionalProperties": False}}},
            "required": ["kind", "message", "tool_calls"]}
        text = self._execute(system_prompt=system_prompt +
            "\nOnly propose tool decisions as JSON; do not execute tools. Encode arguments as arguments_json.",
            user_prompt=json.dumps({"request": user_prompt, "messages": messages or [],
                                    "available_tools": tools}, ensure_ascii=False),
            output_schema=schema, reasoning_effort=reasoning_effort)
        try:
            value = json.loads(text)
            if value["kind"] == "message":
                if value["tool_calls"]:
                    raise ValueError("message has tool calls")
                return {"kind": "message", "message": value["message"]}
            allowed = {item["name"] for item in tools}
            calls = []
            for item in value["tool_calls"]:
                arguments = json.loads(item["arguments_json"])
                if item["tool_name"] not in allowed or not isinstance(arguments, dict):
                    raise ValueError("invalid tool decision")
                calls.append({"tool_name": item["tool_name"], "arguments": arguments, "call_id": None})
            if value["kind"] != "tool_calls" or not calls:
                raise ValueError("empty tool decision")
            return {"kind": "tool_calls", "tool_calls": calls}
        except (KeyError, ValueError, TypeError) as exc:
            raise StructuredOutputError("invalid Codex tool decision envelope") from exc

    def embed_texts(self, **_kwargs: Any):
        raise LLMUnavailableError("Codex SDK has no embedding provider; use lexical retrieval")
