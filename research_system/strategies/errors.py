from __future__ import annotations

from typing import Any


class StrategyDomainError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


class StrategyNotFound(StrategyDomainError):
    def __init__(self) -> None:
        super().__init__("strategy_not_found", "未找到指定策略", status_code=404)


class StrategyConflict(StrategyDomainError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code, message, status_code=409, details=details)


__all__ = ["StrategyConflict", "StrategyDomainError", "StrategyNotFound"]
