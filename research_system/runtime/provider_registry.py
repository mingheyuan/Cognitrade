from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from openai import APITimeoutError, OpenAI
from pydantic import BaseModel

from research_system.runtime.settings import Settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMProviderError(Exception):
    pass


class LLMUnavailableError(LLMProviderError):
    pass


class StructuredOutputError(LLMProviderError):
    pass


class OpenAIProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: OpenAI | None = None
        self.request_timeout_seconds = float(settings.llm_request_timeout_seconds)

    @property
    def available(self) -> bool:
        return bool(self.settings.openai_api_key)

    def _client_instance(self) -> OpenAI:
        if not self.available:
            raise LLMUnavailableError("OPENAI_API_KEY is not configured")
        if self._client is None:
            kwargs: dict[str, str] = {"api_key": self.settings.openai_api_key or ""}
            base_url = self._normalize_base_url(self.settings.openai_base_url)
            if base_url:
                kwargs["base_url"] = base_url
            self._client = OpenAI(**kwargs)
        return self._client

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
            payload = self._extract_json_object(raw_text)
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
        try:
            response = client.responses.create(
                model=self.settings.default_model,
                instructions=system_prompt,
                input=prompt,
                temperature=temperature,
                reasoning={"effort": reasoning_effort} if reasoning_effort else None,
                timeout=self.request_timeout_seconds,
            )
            return self._coerce_responses_text(response)
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
        temperature: float = 0.2,
        reasoning_effort: str | None = None,
    ) -> str:
        client = self._client_instance()
        chunks: list[str] = []
        try:
            with client.responses.stream(
                model=self.settings.default_model,
                instructions=system_prompt,
                input=user_prompt,
                temperature=temperature,
                reasoning={"effort": reasoning_effort} if reasoning_effort else None,
                timeout=self.request_timeout_seconds,
            ) as stream:
                for event in stream:
                    if getattr(event, "type", "") != "response.output_text.delta":
                        continue
                    delta = getattr(event, "delta", None)
                    if not isinstance(delta, str) or not delta:
                        continue
                    chunks.append(delta)
                    on_text_delta(delta)
                final_response = stream.get_final_response()
                final_text = self._coerce_responses_text(final_response)
                return final_text if final_text else "".join(chunks)
        except LLMProviderError:
            raise
        except APITimeoutError as exc:
            raise LLMProviderError(f"llm_request_timeout: {self.request_timeout_seconds}s") from exc
        except Exception as exc:
            raise LLMProviderError(f"llm_request_failed: {exc.__class__.__name__}: {exc}") from exc

    def _coerce_responses_text(self, response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str):
            if self._looks_like_html(output_text):
                raise StructuredOutputError("gateway returned HTML instead of model output")
            return output_text

        if isinstance(response, str):
            if self._looks_like_html(response):
                raise StructuredOutputError("gateway returned HTML instead of model output")
            return response

        if isinstance(response, dict):
            output_text = response.get("output_text")
            if isinstance(output_text, str):
                return output_text

        dumped = repr(response)
        if self._looks_like_html(dumped):
            raise StructuredOutputError("gateway returned HTML instead of model output")
        raise StructuredOutputError(f"unsupported responses API payload: {type(response).__name__}")

    def _extract_json_object(self, text: str) -> dict:
        text = text.strip()
        if not text:
            raise ValueError("empty fallback response")
        if self._looks_like_html(text):
            raise ValueError("gateway returned HTML instead of JSON")
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                text = "\n".join(lines[1:-1]).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("no json object found in response")
        return json.loads(text[start : end + 1])

    def _normalize_base_url(self, base_url: str | None) -> str | None:
        if base_url is None:
            return None
        normalized = base_url.strip()
        if not normalized:
            return None
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return normalized if normalized.endswith("/v1") else normalized.rstrip("/") + "/v1"
        return normalized

    def _looks_like_html(self, text: str) -> bool:
        lowered = text.lstrip().lower()
        return lowered.startswith("<!doctype html") or lowered.startswith("<html")


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._providers = {
            "openai": OpenAIProvider(settings),
        }

    def default(self) -> OpenAIProvider:
        return self._providers[self.settings.provider_name]
