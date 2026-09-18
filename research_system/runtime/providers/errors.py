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


class LLMOutputRepetitionError(LLMProviderError):
    """An opted-in authored-text turn emitted a long exact periodic suffix."""

    def __init__(self):
        super().__init__("llm_output_repetition")


class CodexTurnError(LLMProviderError):
    def __init__(self, code: str, *, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(f"llm_provider_unavailable: {code}")
