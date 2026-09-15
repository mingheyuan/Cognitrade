from __future__ import annotations

from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.codex_provider import CodexProvider


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._providers = {
            "codex": CodexProvider(settings),
        }

    def default(self) -> CodexProvider:
        return self._providers[self.settings.provider_name]
