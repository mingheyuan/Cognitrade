from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
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
    router_redis_url: str | None = None
    router_redis_active_key: str = "cognitrade:router:intent_rules:active"
    router_redis_key_prefix: str = "cognitrade:router:intent_rules:"
    router_redis_last_good_key: str = "cognitrade:router:intent_rules:last_good"
    router_redis_timeout_seconds: float = 0.2
    router_rules_cache_ttl_seconds: float = 2.0
    router_seed_defaults: bool = True
    provider_probe_timeout_seconds: float = 1.0
    provider_policy_path: Path
    data_sources_path: Path
    provider_policy_overrides: dict[str, str] = Field(default_factory=dict)
    default_timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"
    llm_request_timeout_seconds: int = 25
    artifacts_retention_days: int = 30
    max_artifacts_size_mb: int = 1024
    run_timeout_seconds: int = 120
    external_wait_timeout_seconds: int = 30
    retrieval_policy_path: Path
    methodology_manifest_path: Path
    router_defaults_path: Path
    taxonomy_dimensions_path: Path
    taxonomy_dimension_aliases_path: Path
    taxonomy_dimension_patterns_path: Path
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
        env_values: dict[str, str] = {
            key: value
            for key, value in os.environ.items()
        }
        if env_path.exists():
            for key, value in dotenv_values(env_path).items():
                if key not in env_values and value is not None:
                    env_values[key] = value

        workspace_root = root / "workspace"
        config_root = workspace_root / "config"
        taxonomy_root = workspace_root / "taxonomy"

        return cls(
            provider_name=env_values.get("PROVIDER_NAME", "openai"),
            default_model=env_values.get("OPENAI_MODEL", "GPT-5.5"),
            openai_api_key=env_values.get("OPENAI_API_KEY"),
            openai_base_url=env_values.get("OPENAI_BASE_URL"),
            qdrant_url=env_values.get("QDRANT_URL"),
            qdrant_api_key=env_values.get("QDRANT_API_KEY"),
            qdrant_collection_name=env_values.get("QDRANT_COLLECTION_NAME", "research_evidence"),
            mem0_api_key=env_values.get("MEM0_API_KEY"),
            mem0_base_url=env_values.get("MEM0_BASE_URL"),
            router_redis_url=env_values.get("ROUTER_REDIS_URL"),
            router_redis_active_key=env_values.get("ROUTER_REDIS_ACTIVE_KEY", "cognitrade:router:intent_rules:active"),
            router_redis_key_prefix=env_values.get("ROUTER_REDIS_KEY_PREFIX", "cognitrade:router:intent_rules:"),
            router_redis_last_good_key=env_values.get("ROUTER_REDIS_LAST_GOOD_KEY", "cognitrade:router:intent_rules:last_good"),
            router_redis_timeout_seconds=float(env_values.get("ROUTER_REDIS_TIMEOUT_SECONDS", "0.2")),
            router_rules_cache_ttl_seconds=float(env_values.get("ROUTER_RULES_CACHE_TTL_SECONDS", "2.0")),
            router_seed_defaults=env_values.get("ROUTER_SEED_DEFAULTS", "true").strip().lower() not in {"0", "false", "no"},
            provider_probe_timeout_seconds=float(env_values.get("PROVIDER_PROBE_TIMEOUT_SECONDS", "1.0")),
            provider_policy_path=config_root / "provider_policy.yml",
            data_sources_path=config_root / "data_sources.yml",
            provider_policy_overrides={
                "market": env_values.get("COGNITRADE_PROVIDERS_MARKET", ""),
                "fundamental": env_values.get("COGNITRADE_PROVIDERS_FUNDAMENTAL", ""),
                "notice": env_values.get("COGNITRADE_PROVIDERS_NOTICE", ""),
                "sector": env_values.get("COGNITRADE_PROVIDERS_SECTOR", ""),
                "capital_flow": env_values.get("COGNITRADE_PROVIDERS_CAPITAL_FLOW", ""),
                "sentiment": env_values.get("COGNITRADE_PROVIDERS_SENTIMENT", ""),
            },
            default_timezone=env_values.get("DEFAULT_TIMEZONE", "Asia/Shanghai"),
            log_level=env_values.get("LOG_LEVEL", "INFO"),
            llm_request_timeout_seconds=int(env_values.get("LLM_REQUEST_TIMEOUT_SECONDS", "25")),
            artifacts_retention_days=int(env_values.get("ARTIFACTS_RETENTION_DAYS", "30")),
            max_artifacts_size_mb=int(env_values.get("MAX_ARTIFACTS_SIZE_MB", "1024")),
            run_timeout_seconds=int(env_values.get("RUN_TIMEOUT_SECONDS", "120")),
            external_wait_timeout_seconds=int(env_values.get("EXTERNAL_WAIT_TIMEOUT_SECONDS", "30")),
            retrieval_policy_path=config_root / "retrieval_policy.yml",
            methodology_manifest_path=config_root / "methodology_manifest.yml",
            router_defaults_path=config_root / "router_defaults.yml",
            taxonomy_dimensions_path=taxonomy_root / "dimensions.yml",
            taxonomy_dimension_aliases_path=taxonomy_root / "dimension_aliases.yml",
            taxonomy_dimension_patterns_path=taxonomy_root / "dimension_patterns.yml",
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
            "router_defaults": self.router_defaults_path,
            "provider_policy": self.provider_policy_path,
            "data_sources": self.data_sources_path,
            "taxonomy_dimensions": self.taxonomy_dimensions_path,
            "taxonomy_dimension_aliases": self.taxonomy_dimension_aliases_path,
            "taxonomy_dimension_patterns": self.taxonomy_dimension_patterns_path,
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
        if self.router_redis_timeout_seconds <= 0:
            raise ValueError("router_redis_timeout_seconds must be positive")
        if self.router_rules_cache_ttl_seconds < 0:
            raise ValueError("router_rules_cache_ttl_seconds must be non-negative")
        if self.provider_probe_timeout_seconds <= 0:
            raise ValueError("provider_probe_timeout_seconds must be positive")


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

    template_root = Path(__file__).resolve().parents[2]
    template_candidates = [
        (
            [
                template_root / "workspace" / "config" / "retrieval_policy.yml",
                template_root / "research_system" / "config" / "retrieval_policy.yml",
            ],
            settings.retrieval_policy_path,
        ),
        (
            [
                template_root / "workspace" / "config" / "methodology_manifest.yml",
                template_root / "research_system" / "config" / "methodology_manifest.yml",
            ],
            settings.methodology_manifest_path,
        ),
        (
            [
                template_root / "workspace" / "config" / "review_policy.yml",
                template_root / "research_system" / "config" / "review_policy.yml",
            ],
            settings.review_policy_path,
        ),
        (
            [template_root / "workspace" / "config" / "router_defaults.yml"],
            settings.router_defaults_path,
        ),
        (
            [template_root / "workspace" / "config" / "provider_policy.yml"],
            settings.provider_policy_path,
        ),
        (
            [template_root / "workspace" / "config" / "data_sources.yml"],
            settings.data_sources_path,
        ),
        (
            [
                template_root / "workspace" / "taxonomy" / "dimensions.yml",
                template_root / "research_system" / "taxonomy" / "dimensions.yml",
            ],
            settings.taxonomy_dimensions_path,
        ),
        (
            [template_root / "workspace" / "taxonomy" / "dimension_aliases.yml"],
            settings.taxonomy_dimension_aliases_path,
        ),
        (
            [template_root / "workspace" / "taxonomy" / "dimension_patterns.yml"],
            settings.taxonomy_dimension_patterns_path,
        ),
    ]
    for source_candidates, target_path in template_candidates:
        if target_path.exists():
            continue
        source_path = next((path for path in source_candidates if path.exists()), None)
        if source_path is None:
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)
