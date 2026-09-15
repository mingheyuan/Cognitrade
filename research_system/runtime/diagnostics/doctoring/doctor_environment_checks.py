from __future__ import annotations

from pathlib import Path

from research_system.runtime.platform.settings import Settings


def build_environment_checks(settings: Settings, env_path: Path) -> list[dict]:
    return [
        {
            "name": "env_file",
            "status": "passed" if env_path.exists() else "failed",
            "path": str(env_path),
        },
        {
            "name": "codex_provider",
            "status": "passed" if settings.provider_name == "codex" else "failed",
            "detail": "Codex SDK; login/model readiness requires explicit provider health",
        },
        {
            "name": "codex_authentication",
            "status": "skipped",
            "detail": "Run the explicit Codex health check; no API-key fallback",
        },
        {
            "name": "llm_request_timeout_seconds",
            "status": "passed" if settings.llm_request_timeout_seconds > 0 else "failed",
            "detail": str(settings.llm_request_timeout_seconds),
        },
        {
            "name": "distill_llm_timeout_budget",
            "status": "passed" if settings.llm_request_timeout_seconds < settings.run_timeout_seconds else "failed",
            "detail": (
                f"llm_timeout={settings.llm_request_timeout_seconds}s, "
                f"run_timeout={settings.run_timeout_seconds}s"
            ),
        },
    ]


__all__ = ["build_environment_checks"]
