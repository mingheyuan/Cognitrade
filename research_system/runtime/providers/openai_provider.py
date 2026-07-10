from __future__ import annotations

from collections.abc import Callable
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
    ) -> SchemaT:
        raw_text = self.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            force_json=True,
        )
        try:
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
    ) -> str:
        client = self._client_instance()
        prompt = user_prompt if not force_json else f"{user_prompt}\n\nReturn only a JSON object."
        effective_reasoning_effort = reasoning_effort or self.settings.openai_reasoning_effort
        try:
            parts: list[str] = []
            previous_response_id: str | None = None
            current_input = prompt
            for _ in range(self._MAX_CONTINUATION_ATTEMPTS + 1):
                response = client.responses.create(
                    model=self.settings.default_model,
                    instructions=system_prompt,
                    input=current_input,
                    previous_response_id=previous_response_id,
                    temperature=temperature,
                    reasoning={"effort": effective_reasoning_effort} if effective_reasoning_effort else None,
                    store=not self.settings.openai_disable_response_storage,
                    timeout=self.request_timeout_seconds,
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
            raise LLMProviderError(f"llm_request_timeout: {self.request_timeout_seconds}s") from exc
        except Exception as exc:
            raise LLMProviderError(f"llm_request_failed: {exc.__class__.__name__}: {exc}") from exc

    def stream_complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        on_text_delta: Callable[[str], None],
        on_reasoning_summary: Callable[[str], None] | None = None,
        temperature: float = 0.2,
        reasoning_effort: str | None = None,
    ) -> str:
        client = self._client_instance()
        effective_reasoning_effort = reasoning_effort or self.settings.openai_reasoning_effort
        try:
            parts: list[str] = []
            previous_response_id: str | None = None
            current_input = user_prompt
            for _ in range(self._MAX_CONTINUATION_ATTEMPTS + 1):
                part_text, final_response = self.response_support.stream_response_part(
                    client=client,
                    system_prompt=system_prompt,
                    user_input=current_input,
                    previous_response_id=previous_response_id,
                    temperature=temperature,
                    reasoning_effort=effective_reasoning_effort,
                    on_text_delta=on_text_delta,
                    on_reasoning_summary=on_reasoning_summary,
                )
                if part_text:
                    parts.append(part_text)
                if not self.response_support.should_continue_response(final_response):
                    return "".join(parts)
                previous_response_id = self.response_support.response_id(final_response)
                if not previous_response_id:
                    return "".join(parts)
                current_input = self._CONTINUATION_PROMPT
            return "".join(parts)
        except LLMProviderError:
            raise
        except APITimeoutError as exc:
            raise LLMProviderError(f"llm_request_timeout: {self.request_timeout_seconds}s") from exc
        except Exception as exc:
            raise LLMProviderError(f"llm_request_failed: {exc.__class__.__name__}: {exc}") from exc

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

    def _normalize_base_url(self, base_url: str | None) -> str | None:
        return self.client_support.normalize_base_url(base_url)

    def _looks_like_html(self, text: str) -> bool:
        return self.response_support.looks_like_html(text)
