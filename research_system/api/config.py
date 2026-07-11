from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import secrets

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
class ApiPrincipalBinding:
    """An explicit, non-header-derived identity for one API token."""

    token: str = field(repr=False)
    actor_id: str
    user_id: str
    tenant_id: str
    roles: tuple[str, ...] = ("user",)

    def __post_init__(self) -> None:
        token = self.token.strip()
        actor_id = self.actor_id.strip()
        user_id = self.user_id.strip()
        tenant_id = self.tenant_id.strip()
        roles = tuple(dict.fromkeys(str(role).strip() for role in self.roles if str(role).strip()))
        if not token or not actor_id or not user_id or not tenant_id:
            raise ValueError("API principal bindings require token, actor_id, user_id and tenant_id")
        if not roles:
            raise ValueError("API principal bindings require at least one role")
        object.__setattr__(self, "token", token)
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "user_id", user_id)
        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(self, "roles", roles)


@dataclass(frozen=True)
class ApiConfig:
    api_token: str | None
    admin_tokens: tuple[str, ...] = ()
    principal_bindings: tuple[ApiPrincipalBinding, ...] = ()
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
        admin_tokens = tuple(
            item.strip()
            for item in str(env.get("COGNITRADE_API_ADMIN_TOKENS") or "").split(",")
            if item.strip()
        )
        principal_bindings = _parse_principal_bindings(
            str(env.get("COGNITRADE_API_PRINCIPALS") or "").strip()
        )
        return cls(
            api_token=token,
            admin_tokens=admin_tokens,
            principal_bindings=principal_bindings,
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
        return bool(self.api_token or self.admin_tokens or self.principal_bindings)

    def has_explicit_admin_tokens(self) -> bool:
        return bool(self.admin_tokens)

    def principal_for_token(self, token: str) -> ApiPrincipalBinding | None:
        for binding in self.principal_bindings:
            if secrets.compare_digest(token, binding.token):
                return binding
        return None

    def validate(self) -> None:
        if not self.authentication_enabled and not self.allow_unauthenticated:
            raise ValueError(
                "COGNITRADE_API_TOKEN is required unless COGNITRADE_API_PRINCIPALS is configured; set "
                "COGNITRADE_API_ALLOW_UNAUTHENTICATED=true only for isolated local development"
            )
        tokens = [binding.token for binding in self.principal_bindings]
        if len(tokens) != len(set(tokens)):
            raise ValueError("COGNITRADE_API_PRINCIPALS contains duplicate tokens")
        if any(not token.strip() for token in self.admin_tokens):
            raise ValueError("COGNITRADE_API_ADMIN_TOKENS must not contain empty tokens")
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


def _parse_principal_bindings(raw: str) -> tuple[ApiPrincipalBinding, ...]:
    if not raw:
        return ()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("COGNITRADE_API_PRINCIPALS must be valid JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("COGNITRADE_API_PRINCIPALS must be a JSON list")
    bindings: list[ApiPrincipalBinding] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("COGNITRADE_API_PRINCIPALS entries must be objects")
        raw_roles = item.get("roles") or ("user",)
        if isinstance(raw_roles, str):
            raw_roles = (raw_roles,)
        if not isinstance(raw_roles, (list, tuple)):
            raise ValueError("COGNITRADE_API_PRINCIPALS roles must be a list")
        bindings.append(
            ApiPrincipalBinding(
                token=str(item.get("token") or ""),
                actor_id=str(item.get("actor_id") or item.get("user_id") or ""),
                user_id=str(item.get("user_id") or ""),
                tenant_id=str(item.get("tenant_id") or ""),
                roles=tuple(raw_roles),
            )
        )
    return tuple(bindings)


__all__ = ["ApiConfig", "ApiPrincipalBinding"]
