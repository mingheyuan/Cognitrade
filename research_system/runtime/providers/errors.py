"""Provider-independent failures; safe codes, never authentication payloads."""


class LLMProviderError(Exception):
    pass


class LLMUnavailableError(LLMProviderError):
    pass


class StructuredOutputError(LLMProviderError):
    pass


class LLMCancelledError(LLMProviderError):
    pass


class LLMDeadlineError(LLMProviderError):
    pass


class CodexTurnError(LLMProviderError):
    def __init__(self, code: str, *, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(f"llm_provider_unavailable: {code}")
