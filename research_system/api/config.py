from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_system.runtime.config.settings_env import load_settings_env_values


_DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:4173",
    "http://localhost:4173",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ApiConfig:
    api_token: str | None
    allow_unauthenticated: bool = False
    cors_origins: tuple[str, ...] = _DEFAULT_CORS_ORIGINS
    sse_poll_interval_seconds: float = 0.25
    sse_heartbeat_seconds: float = 15.0
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"
    reload: bool = False

    @classmethod
    def from_env(cls, repo_root: Path) -> "ApiConfig":
        env = load_settings_env_values(repo_root)
        raw_origins = str(env.get("COGNITRADE_API_CORS_ORIGINS") or "").strip()
        origins = (
            tuple(item.strip() for item in raw_origins.split(",") if item.strip())
            if raw_origins
            else _DEFAULT_CORS_ORIGINS
        )
        token = str(env.get("COGNITRADE_API_TOKEN") or "").strip() or None
        return cls(
            api_token=token,
            allow_unauthenticated=_as_bool(env.get("COGNITRADE_API_ALLOW_UNAUTHENTICATED")),
            cors_origins=origins,
            sse_poll_interval_seconds=float(env.get("COGNITRADE_API_SSE_POLL_SECONDS", "0.25")),
            sse_heartbeat_seconds=float(env.get("COGNITRADE_API_SSE_HEARTBEAT_SECONDS", "15")),
            host=str(env.get("COGNITRADE_API_HOST") or "127.0.0.1").strip(),
            port=int(env.get("COGNITRADE_API_PORT", "8000")),
            log_level=str(env.get("COGNITRADE_API_LOG_LEVEL") or "info").strip().lower(),
            reload=_as_bool(env.get("COGNITRADE_API_RELOAD")),
        )

    @property
    def authentication_enabled(self) -> bool:
        return self.api_token is not None

    def validate(self) -> None:
        if not self.authentication_enabled and not self.allow_unauthenticated:
            raise ValueError(
                "COGNITRADE_API_TOKEN is required; set "
                "COGNITRADE_API_ALLOW_UNAUTHENTICATED=true only for isolated local development"
            )
        if (
            not self.authentication_enabled
            and self.allow_unauthenticated
            and self.host.strip().lower() not in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError(
                "unauthenticated API mode must bind to a loopback host "
                "(127.0.0.1, localhost, or ::1)"
            )
        if self.sse_poll_interval_seconds <= 0:
            raise ValueError("COGNITRADE_API_SSE_POLL_SECONDS must be positive")
        if self.sse_heartbeat_seconds <= 0:
            raise ValueError("COGNITRADE_API_SSE_HEARTBEAT_SECONDS must be positive")
        if not 1 <= self.port <= 65535:
            raise ValueError("COGNITRADE_API_PORT must be between 1 and 65535")
        if any(origin == "*" for origin in self.cors_origins):
            raise ValueError("COGNITRADE_API_CORS_ORIGINS must not contain '*'")


__all__ = ["ApiConfig"]
