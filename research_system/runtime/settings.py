from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_name: str = "openai"
    default_model: str = "GPT-5.5"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "research_evidence"
    mem0_api_key: str | None = None
    mem0_base_url: str | None = None
    default_timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"
    llm_request_timeout_seconds: int = 25
    artifacts_retention_days: int = 30
    max_artifacts_size_mb: int = 1024
    run_timeout_seconds: int = 120
    external_wait_timeout_seconds: int = 30
    retrieval_policy_path: Path
    methodology_manifest_path: Path
    taxonomy_dimensions_path: Path
    review_policy_path: Path
    workspace_root: Path
    runs_root: Path
    proposals_root: Path
    reports_root: Path
    memory_root: Path
    config_root: Path
    taxonomy_root: Path
    scraper_root: Path

    @classmethod
    def from_env(cls, repo_root: Path | None = None) -> "Settings":
        root = (repo_root or Path.cwd()).resolve()
        env_path = root / ".env"
        if env_path.exists():
            load_dotenv(env_path)

        workspace_root = root / "workspace"
        config_root = workspace_root / "config"
        taxonomy_root = workspace_root / "taxonomy"

        return cls(
            provider_name=os.getenv("PROVIDER_NAME", "openai"),
            default_model=os.getenv("OPENAI_MODEL", "GPT-5.5"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_base_url=os.getenv("OPENAI_BASE_URL"),
            qdrant_url=os.getenv("QDRANT_URL"),
            qdrant_api_key=os.getenv("QDRANT_API_KEY"),
            qdrant_collection_name=os.getenv("QDRANT_COLLECTION_NAME", "research_evidence"),
            mem0_api_key=os.getenv("MEM0_API_KEY"),
            mem0_base_url=os.getenv("MEM0_BASE_URL"),
            default_timezone=os.getenv("DEFAULT_TIMEZONE", "Asia/Shanghai"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            llm_request_timeout_seconds=int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "25")),
            artifacts_retention_days=int(os.getenv("ARTIFACTS_RETENTION_DAYS", "30")),
            max_artifacts_size_mb=int(os.getenv("MAX_ARTIFACTS_SIZE_MB", "1024")),
            run_timeout_seconds=int(os.getenv("RUN_TIMEOUT_SECONDS", "120")),
            external_wait_timeout_seconds=int(os.getenv("EXTERNAL_WAIT_TIMEOUT_SECONDS", "30")),
            retrieval_policy_path=config_root / "retrieval_policy.yml",
            methodology_manifest_path=config_root / "methodology_manifest.yml",
            taxonomy_dimensions_path=taxonomy_root / "dimensions.yml",
            review_policy_path=config_root / "review_policy.yml",
            workspace_root=workspace_root,
            runs_root=workspace_root / "runs",
            proposals_root=workspace_root / "proposals",
            reports_root=workspace_root / "reports",
            memory_root=workspace_root / "memory",
            config_root=config_root,
            taxonomy_root=taxonomy_root,
            scraper_root=root / "scraper",
        )

    def load_yaml_configs(self) -> dict[str, Any]:
        configs: dict[str, Any] = {}
        for name, path in {
            "retrieval_policy": self.retrieval_policy_path,
            "methodology_manifest": self.methodology_manifest_path,
            "taxonomy_dimensions": self.taxonomy_dimensions_path,
            "review_policy": self.review_policy_path,
        }.items():
            if path.exists():
                with path.open("r", encoding="utf-8") as handle:
                    configs[name] = yaml.safe_load(handle) or {}
            else:
                configs[name] = {}
        return configs

    def validate(self) -> None:
        if self.provider_name != "openai":
            raise ValueError("provider_name must default to 'openai' for the current phase")
        if not self.default_model:
            raise ValueError("default_model must be configured")
        if self.run_timeout_seconds <= 0:
            raise ValueError("run_timeout_seconds must be positive")
        if self.external_wait_timeout_seconds <= 0:
            raise ValueError("external_wait_timeout_seconds must be positive")
        if self.llm_request_timeout_seconds <= 0:
            raise ValueError("llm_request_timeout_seconds must be positive")


def ensure_workspace_layout(settings: Settings) -> None:
    required_dirs = [
        settings.workspace_root,
        settings.runs_root,
        settings.proposals_root / "pending",
        settings.proposals_root / "approved",
        settings.proposals_root / "rejected",
        settings.reports_root,
        settings.memory_root / "exports",
        settings.memory_root / "cache",
        settings.config_root,
        settings.taxonomy_root,
    ]
    for directory in required_dirs:
        directory.mkdir(parents=True, exist_ok=True)
