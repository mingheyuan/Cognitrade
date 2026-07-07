from __future__ import annotations

import unittest

from research_system.runtime.providers.openai_client_support import OpenAIClientSupport
from research_system.runtime.providers.openai_provider import (
    LLMProviderError,
    LLMUnavailableError,
    OpenAIProvider,
    StructuredOutputError,
)
from research_system.runtime.providers.openai_response_support import OpenAIResponseSupport
from research_system.runtime.providers.provider_registry import ProviderRegistry
from research_system.runtime.providers.openai_client_support import (
    OpenAIClientSupport as ProviderOpenAIClientSupport,
)
from research_system.runtime.providers.openai_provider import (
    LLMProviderError as ProviderLLMProviderError,
    LLMUnavailableError as ProviderLLMUnavailableError,
    OpenAIProvider as ProviderOpenAIProvider,
    StructuredOutputError as ProviderStructuredOutputError,
)
from research_system.runtime.providers.openai_response_support import (
    OpenAIResponseSupport as ProviderOpenAIResponseSupport,
)
from research_system.runtime.providers.provider_registry import (
    ProviderRegistry as RuntimeProviderRegistry,
)


class ProviderRuntimeTests(unittest.TestCase):
    def test_provider_exports_are_stable_within_providers_package(self) -> None:
        self.assertIs(OpenAIProvider, ProviderOpenAIProvider)
        self.assertIs(LLMProviderError, ProviderLLMProviderError)
        self.assertIs(LLMUnavailableError, ProviderLLMUnavailableError)
        self.assertIs(StructuredOutputError, ProviderStructuredOutputError)
        self.assertIs(OpenAIClientSupport, ProviderOpenAIClientSupport)
        self.assertIs(OpenAIResponseSupport, ProviderOpenAIResponseSupport)
        self.assertIs(ProviderRegistry, RuntimeProviderRegistry)


if __name__ == "__main__":
    unittest.main()
