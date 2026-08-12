from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.config.settings_env import build_settings_from_env
from research_system.runtime.workspace.layout import (
    ensure_workspace_layout as ensure_workspace_layout_impl,
)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_name: str = "openai"
    default_model: str = "gpt-5.6-luna"
    openai_reasoning_effort: str | None = None
    openai_service_tier: str = "default"
    openai_disable_response_storage: bool = True
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    tushare_token: str | None = None
    mx_api_key: str | None = None
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "research_evidence"
    qdrant_hybrid_candidate_limit: int = 50
    qdrant_vector_weight: float = 0.55
    qdrant_lexical_weight: float = 0.45
    memory_vector_search_enabled: bool = False
    tool_resource_lease_backend: str = "file"
    tool_resource_lease_redis_url: str | None = None
    tool_resource_lease_key_prefix: str = "cognitrade:tool:lease:"
    tool_resource_lease_redis_timeout_seconds: float = 0.5
    tool_resource_lease_root: Path | None = None
    domain_pack_require_integrity: bool = False
    domain_pack_trusted_digests: dict[str, list[str]] = Field(default_factory=dict)
    provider_probe_timeout_seconds: float = 1.0
    baostock_socket_timeout_seconds: float = 15.0
    provider_policy_path: Path
    data_sources_path: Path
    mcp_servers_path: Path
    provider_policy_overrides: dict[str, str] = Field(default_factory=dict)
    sensitive_sources_enabled: bool = False
    sensitive_source_allowlist: list[str] = Field(default_factory=list)
    xueqiu_cookie: str | None = None
    browser_sources_enabled: bool = False
    paper_trading_enforce_market_hours: bool = True
    cognitrade_database_url: str | None = None
    cognitrade_database_auto_bootstrap: bool = True
    strategy_object_store_root: Path | None = None
    llm_request_timeout_seconds: int = 25
    uzi_llm_request_timeout_seconds: int = 480
    uzi_llm_max_concurrency: int = 2
    uzi_llm_transient_retries: int = 6
    uzi_llm_retry_base_seconds: float = 2.0
    uzi_llm_retry_max_seconds: float = 60.0
    max_agent_tool_loop_turns: int = 4
    max_child_threads_per_turn: int = 8
    fund_holding_report_concurrency: int = 2
    artifacts_retention_days: int = 30
    max_artifacts_size_mb: int = 1024
    memory_expiration_interval_seconds: float = 300.0
    run_timeout_seconds: int = 120
    workspace_root: Path
    runs_root: Path
    reports_root: Path
    memory_root: Path
    config_root: Path
    domain_packs_root: Path

    @classmethod
    def from_env(cls, repo_root: Path | None = None) -> "Settings":
        return build_settings_from_env(cls, repo_root)

    def validate(self) -> None:
        if self.provider_name != "openai":
            raise ValueError("provider_name must default to 'openai' for the current phase")
        if not self.default_model:
            raise ValueError("default_model must be configured")
        if self.openai_service_tier not in {"auto", "default", "flex", "priority"}:
            raise ValueError("openai_service_tier must be one of: auto, default, flex, priority")
        if self.run_timeout_seconds <= 0:
            raise ValueError("run_timeout_seconds must be positive")
        if self.embedding_dimensions <= 0:
            raise ValueError("embedding_dimensions must be positive")
        if self.memory_expiration_interval_seconds <= 0:
            raise ValueError("memory_expiration_interval_seconds must be positive")
        if self.llm_request_timeout_seconds <= 0:
            raise ValueError("llm_request_timeout_seconds must be positive")
        if self.uzi_llm_request_timeout_seconds <= 0:
            raise ValueError("uzi_llm_request_timeout_seconds must be positive")
        if self.uzi_llm_max_concurrency <= 0:
            raise ValueError("uzi_llm_max_concurrency must be positive")
        if self.uzi_llm_transient_retries < 0:
            raise ValueError("uzi_llm_transient_retries must not be negative")
        if (
            self.uzi_llm_retry_base_seconds <= 0
            or self.uzi_llm_retry_max_seconds < self.uzi_llm_retry_base_seconds
        ):
            raise ValueError(
                "UZI retry delays require 0 < base_seconds <= max_seconds"
            )
        if self.qdrant_hybrid_candidate_limit <= 0:
            raise ValueError("qdrant_hybrid_candidate_limit must be positive")
        if self.max_agent_tool_loop_turns <= 0:
            raise ValueError("max_agent_tool_loop_turns must be positive")
        if self.max_child_threads_per_turn <= 0:
            raise ValueError("max_child_threads_per_turn must be positive")
        if not 1 <= self.fund_holding_report_concurrency <= 10:
            raise ValueError("fund_holding_report_concurrency must be between 1 and 10")
        if self.tool_resource_lease_backend not in {"file", "redis"}:
            raise ValueError("tool_resource_lease_backend must be 'file' or 'redis'")
        if self.tool_resource_lease_backend == "redis" and not self.tool_resource_lease_redis_url:
            raise ValueError(
                "tool_resource_lease_redis_url is required for the Redis lease backend"
            )
        if self.tool_resource_lease_redis_timeout_seconds <= 0:
            raise ValueError("tool_resource_lease_redis_timeout_seconds must be positive")
        for domain_id, digests in self.domain_pack_trusted_digests.items():
            if not str(domain_id).strip():
                raise ValueError("domain_pack_trusted_digests contains an empty domain id")
            for digest in digests:
                normalized = str(digest).strip().lower()
                if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
                    raise ValueError(
                        f"domain_pack_trusted_digests contains an invalid SHA-256 digest: {domain_id}"
                    )
        if self.provider_probe_timeout_seconds <= 0:
            raise ValueError("provider_probe_timeout_seconds must be positive")
        if self.baostock_socket_timeout_seconds <= 0:
            raise ValueError("baostock_socket_timeout_seconds must be positive")
        if self.cognitrade_database_url is not None and not self.cognitrade_database_url.startswith(
            ("sqlite:", "postgresql:", "postgresql+psycopg:")
        ):
            raise ValueError("cognitrade_database_url must use sqlite or postgresql")
        if self.qdrant_vector_weight < 0 or self.qdrant_lexical_weight < 0:
            raise ValueError("qdrant hybrid weights must be non-negative")
        if self.qdrant_vector_weight == 0 and self.qdrant_lexical_weight == 0:
            raise ValueError("at least one qdrant hybrid weight must be positive")


def ensure_workspace_layout(settings: Settings, template_root: Path | None = None) -> None:
    ensure_workspace_layout_impl(settings, template_root=template_root)
