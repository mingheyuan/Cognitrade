from __future__ import annotations

from openai import OpenAI


class OpenAIClientSupport:
    def __init__(self, host) -> None:
        self.host = host

    def client_instance(self) -> OpenAI:
        if not self.host.available:
            raise self.host.llm_unavailable_error("OPENAI_API_KEY is not configured")
        if self.host._client is None:
            kwargs: dict[str, str] = {"api_key": self.host.settings.openai_api_key or ""}
            base_url = self.normalize_base_url(self.host.settings.openai_base_url)
            if base_url:
                kwargs["base_url"] = base_url
            self.host._client = OpenAI(**kwargs)
        return self.host._client

    def normalize_base_url(self, base_url: str | None) -> str | None:
        if base_url is None:
            return None
        normalized = base_url.strip()
        if not normalized:
            return None
        if normalized.startswith(("http://", "https://")):
            normalized = normalized.rstrip("/")
            return normalized if normalized.endswith("/v1") else normalized + "/v1"
        return normalized
