from __future__ import annotations

from research_system.runtime.providers.openai_provider import (
    LLMProviderError,
    LLMUnavailableError,
    OpenAIProvider,
    StructuredOutputError,
)
from research_system.runtime.platform.settings import Settings


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._providers = {
            "openai": OpenAIProvider(settings),
        }

    def default(self) -> OpenAIProvider:
        return self._providers[self.settings.provider_name]
