from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeVar

from dotenv import dotenv_values

SettingsT = TypeVar("SettingsT")


def load_settings_env_values(root: Path) -> dict[str, str]:
    env_path = root / ".env"
    env_values: dict[str, str] = {key: value for key, value in os.environ.items()}
    if env_path.exists():
        for key, value in dotenv_values(env_path).items():
            if key not in env_values and value is not None:
                env_values[key] = value
    return env_values


def build_settings_kwargs(root: Path, env_values: dict[str, str]) -> dict[str, Any]:
    workspace_root = root / "workspace"
    config_root = workspace_root / "config"
    taxonomy_root = workspace_root / "taxonomy"
    return {
        "provider_name": env_values.get("PROVIDER_NAME", "openai"),
        "default_model": env_values.get("OPENAI_MODEL", "GPT-5.5"),
        "embedding_model": env_values.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        "embedding_dimensions": int(env_values.get("OPENAI_EMBEDDING_DIMENSIONS", "1536")),
        "openai_api_key": env_values.get("OPENAI_API_KEY"),
        "openai_base_url": env_values.get("OPENAI_BASE_URL"),
        "qdrant_url": env_values.get("QDRANT_URL"),
        "qdrant_api_key": env_values.get("QDRANT_API_KEY"),
        "qdrant_collection_name": env_values.get("QDRANT_COLLECTION_NAME", "research_evidence"),
        "qdrant_hybrid_candidate_limit": int(env_values.get("QDRANT_HYBRID_CANDIDATE_LIMIT", "50")),
        "qdrant_vector_weight": float(env_values.get("QDRANT_VECTOR_WEIGHT", "0.55")),
        "qdrant_lexical_weight": float(env_values.get("QDRANT_LEXICAL_WEIGHT", "0.45")),
        "router_redis_url": env_values.get("ROUTER_REDIS_URL"),
        "router_redis_active_key": env_values.get("ROUTER_REDIS_ACTIVE_KEY", "cognitrade:router:intent_rules:active"),
        "router_redis_key_prefix": env_values.get("ROUTER_REDIS_KEY_PREFIX", "cognitrade:router:intent_rules:"),
        "router_redis_last_good_key": env_values.get("ROUTER_REDIS_LAST_GOOD_KEY", "cognitrade:router:intent_rules:last_good"),
        "router_redis_timeout_seconds": float(env_values.get("ROUTER_REDIS_TIMEOUT_SECONDS", "0.2")),
        "router_rules_cache_ttl_seconds": float(env_values.get("ROUTER_RULES_CACHE_TTL_SECONDS", "2.0")),
        "router_seed_defaults": env_values.get("ROUTER_SEED_DEFAULTS", "true").strip().lower() not in {"0", "false", "no"},
        "provider_probe_timeout_seconds": float(env_values.get("PROVIDER_PROBE_TIMEOUT_SECONDS", "1.0")),
        "provider_policy_path": config_root / "provider_policy.yml",
        "data_sources_path": config_root / "data_sources.yml",
        "http_json_sources_path": config_root / "http_json_sources.yml",
        "mcp_servers_path": config_root / "mcp_servers.yml",
        "openapi_sources_path": config_root / "openapi_sources.yml",
        "skill_sources_path": config_root / "skill_sources.yml",
        "provider_policy_overrides": {
            "market": env_values.get("COGNITRADE_PROVIDERS_MARKET", ""),
            "fundamental": env_values.get("COGNITRADE_PROVIDERS_FUNDAMENTAL", ""),
            "notice": env_values.get("COGNITRADE_PROVIDERS_NOTICE", ""),
            "sector": env_values.get("COGNITRADE_PROVIDERS_SECTOR", ""),
            "capital_flow": env_values.get("COGNITRADE_PROVIDERS_CAPITAL_FLOW", ""),
            "sentiment": env_values.get("COGNITRADE_PROVIDERS_SENTIMENT", ""),
        },
        "default_timezone": env_values.get("DEFAULT_TIMEZONE", "Asia/Shanghai"),
        "log_level": env_values.get("LOG_LEVEL", "INFO"),
        "llm_request_timeout_seconds": int(env_values.get("LLM_REQUEST_TIMEOUT_SECONDS", "25")),
        "max_llm_calls_per_turn": int(env_values.get("MAX_LLM_CALLS_PER_TURN", "1")),
        "max_subagent_calls_per_turn": int(env_values.get("MAX_SUBAGENT_CALLS_PER_TURN", "32")),
        "max_child_threads_per_turn": int(env_values.get("MAX_CHILD_THREADS_PER_TURN", "8")),
        "supervisor_shadow_llm_enabled": env_values.get("SUPERVISOR_SHADOW_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        "supervisor_shadow_llm_reasoning_effort": env_values.get("SUPERVISOR_SHADOW_LLM_REASONING_EFFORT", "low"),
        "supervisor_llm_policy_enabled": env_values.get("SUPERVISOR_LLM_POLICY_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        "supervisor_llm_policy_min_confidence": float(env_values.get("SUPERVISOR_LLM_POLICY_MIN_CONFIDENCE", "0.7")),
        "supervisor_hybrid_llm_enabled": env_values.get("SUPERVISOR_HYBRID_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        "supervisor_hybrid_llm_min_confidence": float(env_values.get("SUPERVISOR_HYBRID_LLM_MIN_CONFIDENCE", "0.7")),
        "subagent_phase_shadow_llm_enabled": env_values.get("SUBAGENT_PHASE_SHADOW_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        "subagent_phase_shadow_llm_reasoning_effort": env_values.get("SUBAGENT_PHASE_SHADOW_LLM_REASONING_EFFORT", "low"),
        "subagent_phase_llm_policy_enabled": env_values.get("SUBAGENT_PHASE_LLM_POLICY_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        "subagent_phase_llm_policy_min_confidence": float(env_values.get("SUBAGENT_PHASE_LLM_POLICY_MIN_CONFIDENCE", "0.7")),
        "subagent_phase_hybrid_llm_enabled": env_values.get("SUBAGENT_PHASE_HYBRID_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        "subagent_phase_hybrid_llm_min_confidence": float(env_values.get("SUBAGENT_PHASE_HYBRID_LLM_MIN_CONFIDENCE", "0.7")),
        "subagent_discovery_shadow_llm_enabled": env_values.get("SUBAGENT_DISCOVERY_SHADOW_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        "subagent_discovery_shadow_llm_reasoning_effort": env_values.get("SUBAGENT_DISCOVERY_SHADOW_LLM_REASONING_EFFORT", "low"),
        "subagent_discovery_llm_policy_enabled": env_values.get("SUBAGENT_DISCOVERY_LLM_POLICY_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        "subagent_discovery_llm_policy_min_confidence": float(env_values.get("SUBAGENT_DISCOVERY_LLM_POLICY_MIN_CONFIDENCE", "0.7")),
        "subagent_discovery_hybrid_llm_enabled": env_values.get("SUBAGENT_DISCOVERY_HYBRID_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes"},
        "subagent_discovery_hybrid_llm_min_confidence": float(env_values.get("SUBAGENT_DISCOVERY_HYBRID_LLM_MIN_CONFIDENCE", "0.7")),
        "artifacts_retention_days": int(env_values.get("ARTIFACTS_RETENTION_DAYS", "30")),
        "max_artifacts_size_mb": int(env_values.get("MAX_ARTIFACTS_SIZE_MB", "1024")),
        "run_timeout_seconds": int(env_values.get("RUN_TIMEOUT_SECONDS", "120")),
        "external_wait_timeout_seconds": int(env_values.get("EXTERNAL_WAIT_TIMEOUT_SECONDS", "30")),
        "retrieval_policy_path": config_root / "retrieval_policy.yml",
        "methodology_manifest_path": config_root / "methodology_manifest.yml",
        "router_defaults_path": config_root / "router_defaults.yml",
        "subagent_discovery_policy_path": config_root / "subagent_discovery_policy.yml",
        "subagent_discovery_approval_policy_path": config_root / "subagent_discovery_approval_policy.yml",
        "taxonomy_dimensions_path": taxonomy_root / "dimensions.yml",
        "taxonomy_dimension_aliases_path": taxonomy_root / "dimension_aliases.yml",
        "taxonomy_dimension_patterns_path": taxonomy_root / "dimension_patterns.yml",
        "review_policy_path": config_root / "review_policy.yml",
        "workspace_root": workspace_root,
        "runs_root": workspace_root / "runs",
        "proposals_root": workspace_root / "proposals",
        "subagent_discovery_root": workspace_root / "subagent_discovery",
        "reports_root": workspace_root / "reports",
        "memory_root": workspace_root / "memory",
        "config_root": config_root,
        "taxonomy_root": taxonomy_root,
        "scraper_root": root / "scraper",
    }


def build_settings_from_env(settings_type: type[SettingsT], repo_root: Path | None = None) -> SettingsT:
    root = (repo_root or Path.cwd()).resolve()
    env_values = load_settings_env_values(root)
    return settings_type(**build_settings_kwargs(root, env_values))
