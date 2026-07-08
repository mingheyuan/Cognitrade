from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.config.settings_env import build_settings_from_env
from research_system.runtime.config.settings_yaml_loader import load_settings_yaml_configs
from research_system.runtime.workspace.layout import ensure_workspace_layout as ensure_workspace_layout_impl


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_name: str = "openai"
    default_model: str = "GPT-5.5"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "research_evidence"
    qdrant_hybrid_candidate_limit: int = 50
    qdrant_vector_weight: float = 0.55
    qdrant_lexical_weight: float = 0.45
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
    max_llm_calls_per_turn: int = 1
    max_subagent_calls_per_turn: int = 32
    max_child_threads_per_turn: int = 8
    supervisor_shadow_llm_enabled: bool = False
    supervisor_shadow_llm_reasoning_effort: str = "low"
    supervisor_llm_policy_enabled: bool = False
    supervisor_llm_policy_min_confidence: float = 0.7
    supervisor_hybrid_llm_enabled: bool = False
    supervisor_hybrid_llm_min_confidence: float = 0.7
    subagent_phase_shadow_llm_enabled: bool = False
    subagent_phase_shadow_llm_reasoning_effort: str = "low"
    subagent_phase_llm_policy_enabled: bool = False
    subagent_phase_llm_policy_min_confidence: float = 0.7
    subagent_phase_hybrid_llm_enabled: bool = False
    subagent_phase_hybrid_llm_min_confidence: float = 0.7
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
        return build_settings_from_env(cls, repo_root)

    def yaml_config_paths(self) -> dict[str, Path]:
        return {
            "retrieval_policy": self.retrieval_policy_path,
            "methodology_manifest": self.methodology_manifest_path,
            "router_defaults": self.router_defaults_path,
            "provider_policy": self.provider_policy_path,
            "data_sources": self.data_sources_path,
            "taxonomy_dimensions": self.taxonomy_dimensions_path,
            "taxonomy_dimension_aliases": self.taxonomy_dimension_aliases_path,
            "taxonomy_dimension_patterns": self.taxonomy_dimension_patterns_path,
            "review_policy": self.review_policy_path,
        }

    def load_yaml_configs(self) -> dict[str, Any]:
        return load_settings_yaml_configs(self.yaml_config_paths())

    def validate(self) -> None:
        if self.provider_name != "openai":
            raise ValueError("provider_name must default to 'openai' for the current phase")
        if not self.default_model:
            raise ValueError("default_model must be configured")
        if self.run_timeout_seconds <= 0:
            raise ValueError("run_timeout_seconds must be positive")
        if self.embedding_dimensions <= 0:
            raise ValueError("embedding_dimensions must be positive")
        if self.external_wait_timeout_seconds <= 0:
            raise ValueError("external_wait_timeout_seconds must be positive")
        if self.llm_request_timeout_seconds <= 0:
            raise ValueError("llm_request_timeout_seconds must be positive")
        if self.qdrant_hybrid_candidate_limit <= 0:
            raise ValueError("qdrant_hybrid_candidate_limit must be positive")
        if self.max_llm_calls_per_turn <= 0:
            raise ValueError("max_llm_calls_per_turn must be positive")
        if self.max_subagent_calls_per_turn <= 0:
            raise ValueError("max_subagent_calls_per_turn must be positive")
        if self.max_child_threads_per_turn <= 0:
            raise ValueError("max_child_threads_per_turn must be positive")
        if self.supervisor_hybrid_llm_min_confidence < 0 or self.supervisor_hybrid_llm_min_confidence > 1:
            raise ValueError("supervisor_hybrid_llm_min_confidence must be between 0 and 1")
        if self.supervisor_llm_policy_min_confidence < 0 or self.supervisor_llm_policy_min_confidence > 1:
            raise ValueError("supervisor_llm_policy_min_confidence must be between 0 and 1")
        if self.subagent_phase_hybrid_llm_min_confidence < 0 or self.subagent_phase_hybrid_llm_min_confidence > 1:
            raise ValueError("subagent_phase_hybrid_llm_min_confidence must be between 0 and 1")
        if self.subagent_phase_llm_policy_min_confidence < 0 or self.subagent_phase_llm_policy_min_confidence > 1:
            raise ValueError("subagent_phase_llm_policy_min_confidence must be between 0 and 1")
        if self.router_redis_timeout_seconds <= 0:
            raise ValueError("router_redis_timeout_seconds must be positive")
        if self.router_rules_cache_ttl_seconds < 0:
            raise ValueError("router_rules_cache_ttl_seconds must be non-negative")
        if self.provider_probe_timeout_seconds <= 0:
            raise ValueError("provider_probe_timeout_seconds must be positive")
        if self.qdrant_vector_weight < 0 or self.qdrant_lexical_weight < 0:
            raise ValueError("qdrant hybrid weights must be non-negative")
        if self.qdrant_vector_weight == 0 and self.qdrant_lexical_weight == 0:
            raise ValueError("at least one qdrant hybrid weight must be positive")


def ensure_workspace_layout(settings: Settings, template_root: Path | None = None) -> None:
    ensure_workspace_layout_impl(settings, template_root=template_root)
