from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from openai import APITimeoutError, OpenAI
from pydantic import BaseModel

from research_system.runtime.providers.openai_client_support import OpenAIClientSupport
from research_system.runtime.providers.openai_response_support import OpenAIResponseSupport
from research_system.runtime.platform.settings import Settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMProviderError(Exception):
    pass


class LLMUnavailableError(LLMProviderError):
    pass


class StructuredOutputError(LLMProviderError):
    pass


class OpenAIProvider:
    _CONTINUATION_PROMPT = "Continue exactly from where you stopped. Do not repeat any previous text. Emit only the continuation."
    _MAX_CONTINUATION_ATTEMPTS = 3
    _PUBLIC_PROGRESS_PATTERN = re.compile(
        r"<progress>\s*(\{.*?\})\s*</progress>",
        flags=re.IGNORECASE | re.DOTALL,
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: OpenAI | None = None
        self.request_timeout_seconds = float(settings.llm_request_timeout_seconds)
        self.client_support = OpenAIClientSupport(self)
        self.response_support = OpenAIResponseSupport(self)

    llm_unavailable_error = LLMUnavailableError
    structured_output_error = StructuredOutputError

    @property
    def available(self) -> bool:
        return bool(self.settings.openai_api_key)

    def _client_instance(self) -> OpenAI:
        return self.client_support.client_instance()

    def parse(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
        temperature: float = 0.2,
        reasoning_effort: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> SchemaT:
        client = self._client_instance()
        request_client = (
            client.with_options(max_retries=max_retries)
            if max_retries is not None
            else client
        )
        effective_reasoning_effort = (
            reasoning_effort or self.settings.openai_reasoning_effort
        )
        request_timeout_seconds = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else self.request_timeout_seconds
        )
        if request_timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        try:
            response = request_client.responses.parse(
                model=self.settings.default_model,
                instructions=system_prompt,
                input=user_prompt,
                text_format=schema,
                temperature=temperature,
                reasoning=(
                    {"effort": effective_reasoning_effort}
                    if effective_reasoning_effort
                    else None
                ),
                service_tier=self.settings.openai_service_tier,
                store=not self.settings.openai_disable_response_storage,
                timeout=request_timeout_seconds,
            )
        except LLMProviderError:
            raise
        except APITimeoutError as exc:
            raise LLMProviderError(
                f"llm_request_timeout: {request_timeout_seconds:g}s"
            ) from exc
        except Exception as exc:
            raise LLMProviderError(
                f"llm_request_failed: {exc.__class__.__name__}: {exc}"
            ) from exc

        try:
            parsed = getattr(response, "output_parsed", None)
            if isinstance(parsed, schema):
                return parsed
            if parsed is not None:
                return schema.model_validate(parsed)
            status = self.response_support.response_status(response)
            reason = self.response_support.incomplete_reason(response)
            if status == "incomplete":
                raise ValueError(
                    f"structured response incomplete: {reason or 'unknown_reason'}"
                )
            raw_text = self.response_support.coerce_responses_text(response)
            payload = self.response_support.extract_json_object(raw_text)
            return schema.model_validate(payload)
        except Exception as exc:
            raise StructuredOutputError(f"failed to parse structured response: {exc}") from exc

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        reasoning_effort: str | None = None,
        force_json: bool = False,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> str:
        client = self._client_instance()
        request_client = client.with_options(max_retries=max_retries) if max_retries is not None else client
        prompt = user_prompt if not force_json else f"{user_prompt}\n\nReturn only a JSON object."
        effective_reasoning_effort = reasoning_effort or self.settings.openai_reasoning_effort
        request_timeout_seconds = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else self.request_timeout_seconds
        )
        if request_timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        try:
            parts: list[str] = []
            previous_response_id: str | None = None
            current_input = prompt
            for _ in range(self._MAX_CONTINUATION_ATTEMPTS + 1):
                response = request_client.responses.create(
                    model=self.settings.default_model,
                    instructions=system_prompt,
                    input=current_input,
                    previous_response_id=previous_response_id,
                    temperature=temperature,
                    reasoning={"effort": effective_reasoning_effort} if effective_reasoning_effort else None,
                    service_tier=self.settings.openai_service_tier,
                    store=not self.settings.openai_disable_response_storage,
                    timeout=request_timeout_seconds,
                )
                response_text = self.response_support.coerce_responses_text(response)
                if response_text:
                    parts.append(response_text)
                if not self.response_support.should_continue_response(response):
                    return "".join(parts)
                previous_response_id = self.response_support.response_id(response)
                if not previous_response_id:
                    return "".join(parts)
                current_input = self._CONTINUATION_PROMPT
            return "".join(parts)
        except LLMProviderError:
            raise
        except APITimeoutError as exc:
            raise LLMProviderError(f"llm_request_timeout: {request_timeout_seconds:g}s") from exc
        except Exception as exc:
            raise LLMProviderError(f"llm_request_failed: {exc.__class__.__name__}: {exc}") from exc

    def complete_with_tools(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        """Execute one provider-native Agent turn.

        The Agent OS owns the loop and governance. This adapter only translates
        the provider response into a stable ``message`` or ``tool_calls``
        payload, so the same Main Agent can run with a test provider or a
        different model backend.
        """
        client = self._client_instance()
        input_messages = self._tool_input_messages(user_prompt, messages or [])
        provider_tools = [
            {
                "type": "function",
                "name": str(item.get("name") or "").strip(),
                "description": str(item.get("description") or ""),
                "parameters": dict(item.get("input_schema") or {"type": "object"}),
            }
            for item in tools
            if str(item.get("name") or "").strip()
        ]
        effective_reasoning_effort = reasoning_effort or self.settings.openai_reasoning_effort
        try:
            response = client.responses.create(
                model=self.settings.default_model,
                instructions=system_prompt,
                input=input_messages,
                tools=provider_tools,
                temperature=temperature,
                reasoning={"effort": effective_reasoning_effort} if effective_reasoning_effort else None,
                service_tier=self.settings.openai_service_tier,
                store=not self.settings.openai_disable_response_storage,
                timeout=self.request_timeout_seconds,
            )
            calls: list[dict[str, Any]] = []
            for item in list(getattr(response, "output", None) or []):
                item_type = getattr(item, "type", None)
                if isinstance(item, dict):
                    item_type = item.get("type")
                if item_type != "function_call":
                    continue
                name = getattr(item, "name", None) if not isinstance(item, dict) else item.get("name")
                raw_arguments = (
                    getattr(item, "arguments", None)
                    if not isinstance(item, dict)
                    else item.get("arguments")
                )
                if isinstance(raw_arguments, str):
                    arguments = json.loads(raw_arguments or "{}")
                elif isinstance(raw_arguments, dict):
                    arguments = raw_arguments
                else:
                    arguments = {}
                call_id = getattr(item, "call_id", None) if not isinstance(item, dict) else item.get("call_id")
                calls.append(
                    {
                        "tool_name": str(name or "").strip(),
                        "arguments": dict(arguments),
                        "call_id": str(call_id or "").strip() or None,
                    }
                )
            text = self.response_support.coerce_responses_text(response)
            public_progress = self._extract_public_progress(text)
            if calls:
                payload: dict[str, Any] = {"kind": "tool_calls", "tool_calls": calls}
                if public_progress is not None:
                    payload["public_progress"] = public_progress
                return payload
            payload = {"kind": "message", "message": self._strip_public_progress(text)}
            if public_progress is not None:
                payload["public_progress"] = public_progress
            return payload
        except LLMProviderError:
            raise
        except APITimeoutError as exc:
            raise LLMProviderError(f"llm_request_timeout: {self.request_timeout_seconds}s") from exc
        except Exception as exc:
            raise LLMProviderError(f"llm_tool_turn_failed: {exc.__class__.__name__}: {exc}") from exc

    @staticmethod
    def _tool_input_messages(user_prompt: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages:
            return [{"role": "user", "content": user_prompt}]
        normalized: list[dict[str, Any]] = []
        for row in messages:
            role = str(row.get("role") or "user")
            if role == "tool":
                normalized.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(row.get("call_id") or "unknown"),
                        "output": json.dumps(
                            {
                                "status": row.get("status"),
                                "output": row.get("output"),
                                "error": row.get("error"),
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
                continue
            if row.get("tool_calls"):
                normalized.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {"tool_calls": row.get("tool_calls")},
                            ensure_ascii=False,
                        ),
                    }
                )
                continue
            normalized.append({"role": role, "content": str(row.get("content") or "")})
        return normalized

    @classmethod
    def _extract_public_progress(cls, text: str) -> dict[str, str] | None:
        match = cls._PUBLIC_PROGRESS_PATTERN.search(str(text or ""))
        if match is None:
            return None
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        current = str(payload.get("current") or "").strip()
        next_action = str(payload.get("next") or "").strip()
        if not current and not next_action:
            return None
        return {"current": current[:500], "next": next_action[:500]}

    @classmethod
    def _strip_public_progress(cls, text: str) -> str:
        return cls._PUBLIC_PROGRESS_PATTERN.sub("", str(text or "")).strip()

    def embed_texts(
        self,
        *,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        client = self._client_instance()
        try:
            response = client.embeddings.create(
                model=model or self.settings.embedding_model,
                input=texts,
                dimensions=int(self.settings.embedding_dimensions),
                timeout=self.request_timeout_seconds,
            )
            return [list(item.embedding) for item in response.data]
        except LLMProviderError:
            raise
        except APITimeoutError as exc:
            raise LLMProviderError(f"llm_request_timeout: {self.request_timeout_seconds}s") from exc
        except Exception as exc:
            raise LLMProviderError(f"embedding_request_failed: {exc.__class__.__name__}: {exc}") from exc
