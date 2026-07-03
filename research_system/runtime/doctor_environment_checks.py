from __future__ import annotations

from pathlib import Path

from research_system.runtime.settings import Settings


def build_environment_checks(settings: Settings, env_path: Path) -> list[dict]:
    return [
        {
            "name": "env_file",
            "status": "passed" if env_path.exists() else "failed",
            "path": str(env_path),
        },
        {
            "name": "openai_api_key",
            "status": "passed" if bool(settings.openai_api_key) else "failed",
            "detail": "configured" if settings.openai_api_key else "missing",
        },
        {
            "name": "openai_base_url",
            "status": "passed" if settings.openai_base_url is not None else "skipped",
            "detail": settings.openai_base_url or "not_set",
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
