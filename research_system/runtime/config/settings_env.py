from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, TypeVar

from dotenv import dotenv_values

SettingsT = TypeVar("SettingsT")


def load_settings_env_values(root: Path) -> dict[str, str]:
    env_path = root / ".env"
    env_values: dict[str, str] = dict(os.environ)
    if env_path.exists():
        for key, value in dotenv_values(env_path).items():
            if key not in env_values and value is not None:
                env_values[key] = value
    return env_values


def build_settings_kwargs(root: Path, env_values: dict[str, str]) -> dict[str, Any]:
    workspace_root = root / "workspace"
    config_root = workspace_root / "config"
    return {
        "provider_name": env_values.get("PROVIDER_NAME", "openai"),
        "default_model": env_values.get("OPENAI_MODEL", "GPT-5.5"),
        "review_model": env_values.get("OPENAI_REVIEW_MODEL") or env_values.get("OPENAI_MODEL", "GPT-5.5"),
        "openai_reasoning_effort": env_values.get("OPENAI_REASONING_EFFORT") or None,
        "openai_disable_response_storage": env_values.get("OPENAI_DISABLE_RESPONSE_STORAGE", "true").strip().lower()
        in {"1", "true", "yes", "on"},
        "embedding_model": env_values.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        "embedding_dimensions": int(env_values.get("OPENAI_EMBEDDING_DIMENSIONS", "1536")),
        "openai_api_key": env_values.get("OPENAI_API_KEY"),
        "openai_base_url": env_values.get("OPENAI_BASE_URL"),
        "tushare_token": env_values.get("TUSHARE_TOKEN"),
        "mx_api_key": env_values.get("MX_APIKEY"),
        "qdrant_url": env_values.get("QDRANT_URL"),
        "qdrant_api_key": env_values.get("QDRANT_API_KEY"),
        "qdrant_collection_name": env_values.get("QDRANT_COLLECTION_NAME", "research_evidence"),
        "qdrant_hybrid_candidate_limit": int(env_values.get("QDRANT_HYBRID_CANDIDATE_LIMIT", "50")),
        "qdrant_vector_weight": float(env_values.get("QDRANT_VECTOR_WEIGHT", "0.55")),
        "qdrant_lexical_weight": float(env_values.get("QDRANT_LEXICAL_WEIGHT", "0.45")),
        "memory_vector_search_enabled": env_values.get("MEMORY_VECTOR_SEARCH_ENABLED", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        "tool_resource_lease_backend": env_values.get("TOOL_RESOURCE_LEASE_BACKEND", "file").strip().lower(),
        "tool_resource_lease_redis_url": env_values.get("TOOL_RESOURCE_LEASE_REDIS_URL"),
        "tool_resource_lease_key_prefix": env_values.get("TOOL_RESOURCE_LEASE_KEY_PREFIX", "cognitrade:tool:lease:"),
        "tool_resource_lease_redis_timeout_seconds": float(env_values.get("TOOL_RESOURCE_LEASE_REDIS_TIMEOUT_SECONDS", "0.5")),
        "tool_resource_lease_root": Path(env_values["TOOL_RESOURCE_LEASE_ROOT"]).resolve()
        if env_values.get("TOOL_RESOURCE_LEASE_ROOT")
        else None,
        "domain_pack_require_integrity": env_values.get("DOMAIN_PACK_REQUIRE_INTEGRITY", "false").strip().lower()
        in {"1", "true", "yes", "on"},
        "domain_pack_trusted_digests": _parse_json_mapping(
            env_values.get("DOMAIN_PACK_TRUSTED_DIGESTS", "{}"),
            field_name="DOMAIN_PACK_TRUSTED_DIGESTS",
        ),
        "provider_probe_timeout_seconds": float(env_values.get("PROVIDER_PROBE_TIMEOUT_SECONDS", "1.0")),
        "baostock_socket_timeout_seconds": float(
            env_values.get("COGNITRADE_BAOSTOCK_SOCKET_TIMEOUT_SECONDS", "15")
        ),
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
            "lhb": env_values.get("COGNITRADE_PROVIDERS_LHB", ""),
            "events": env_values.get("COGNITRADE_PROVIDERS_EVENTS", ""),
            "policy": env_values.get("COGNITRADE_PROVIDERS_POLICY", ""),
            "kline": env_values.get("COGNITRADE_PROVIDERS_KLINE", ""),
            "macro": env_values.get("COGNITRADE_PROVIDERS_MACRO", ""),
            "peers": env_values.get("COGNITRADE_PROVIDERS_PEERS", ""),
            "chain": env_values.get("COGNITRADE_PROVIDERS_CHAIN", ""),
            "research": env_values.get("COGNITRADE_PROVIDERS_RESEARCH", ""),
            "industry": env_values.get("COGNITRADE_PROVIDERS_INDUSTRY", ""),
            "materials": env_values.get("COGNITRADE_PROVIDERS_MATERIALS", ""),
            "futures": env_values.get("COGNITRADE_PROVIDERS_FUTURES", ""),
            "valuation": env_values.get("COGNITRADE_PROVIDERS_VALUATION", ""),
            "governance": env_values.get("COGNITRADE_PROVIDERS_GOVERNANCE", ""),
            "moat": env_values.get("COGNITRADE_PROVIDERS_MOAT", ""),
            "trap": env_values.get("COGNITRADE_PROVIDERS_TRAP", ""),
            "contests": env_values.get("COGNITRADE_PROVIDERS_CONTESTS", ""),
            "fund_holders": env_values.get("COGNITRADE_PROVIDERS_FUND_HOLDERS", ""),
            "similar_stocks": env_values.get("COGNITRADE_PROVIDERS_SIMILAR_STOCKS", ""),
            "fund": env_values.get("COGNITRADE_PROVIDERS_FUND", ""),
        },
        "sensitive_sources_enabled": env_values.get(
            "COGNITRADE_SENSITIVE_SOURCES_ENABLED", "false"
        ).strip().lower()
        in {"1", "true", "yes", "on"},
        "sensitive_source_allowlist": [
            item.strip().lower()
            for item in env_values.get("COGNITRADE_SENSITIVE_SOURCE_ALLOWLIST", "").split(",")
            if item.strip()
        ],
        "xueqiu_cookie": env_values.get("COGNITRADE_XUEQIU_COOKIE") or None,
        "browser_sources_enabled": env_values.get(
            "COGNITRADE_BROWSER_SOURCES_ENABLED", "false"
        ).strip().lower()
        in {"1", "true", "yes", "on"},
        "default_timezone": env_values.get("DEFAULT_TIMEZONE", "Asia/Shanghai"),
        "paper_trading_enforce_market_hours": env_values.get(
            "COGNITRADE_PAPER_ENFORCE_MARKET_HOURS", "true"
        ).strip().lower()
        in {"1", "true", "yes", "on"},
        "log_level": env_values.get("LOG_LEVEL", "INFO"),
        "llm_request_timeout_seconds": int(env_values.get("LLM_REQUEST_TIMEOUT_SECONDS", "25")),
        "uzi_llm_request_timeout_seconds": int(
            env_values.get("UZI_LLM_REQUEST_TIMEOUT_SECONDS", "480")
        ),
        "uzi_llm_max_concurrency": int(env_values.get("UZI_LLM_MAX_CONCURRENCY", "5")),
        "max_llm_calls_per_turn": int(env_values.get("MAX_LLM_CALLS_PER_TURN", "1")),
        "max_subagent_calls_per_turn": int(env_values.get("MAX_SUBAGENT_CALLS_PER_TURN", "32")),
        "max_child_threads_per_turn": int(env_values.get("MAX_CHILD_THREADS_PER_TURN", "8")),
        "fund_holding_report_concurrency": int(
            env_values.get("COGNITRADE_FUND_HOLDING_REPORT_CONCURRENCY", "5")
        ),
        "artifacts_retention_days": int(env_values.get("ARTIFACTS_RETENTION_DAYS", "30")),
        "max_artifacts_size_mb": int(env_values.get("MAX_ARTIFACTS_SIZE_MB", "1024")),
        "memory_expiration_interval_seconds": float(
            env_values.get("MEMORY_EXPIRATION_INTERVAL_SECONDS", "300")
        ),
        "run_timeout_seconds": int(env_values.get("RUN_TIMEOUT_SECONDS", "120")),
        "external_wait_timeout_seconds": int(env_values.get("EXTERNAL_WAIT_TIMEOUT_SECONDS", "30")),
        "workspace_root": workspace_root,
        "runs_root": workspace_root / "runs",
        "reports_root": workspace_root / "reports",
        "memory_root": workspace_root / "memory",
        "config_root": config_root,
        "scraper_root": root / "scraper",
        "domain_packs_root": root / "domain_packs",
    }


def _parse_json_mapping(value: str, *, field_name: str) -> dict[str, list[str]]:
    raw = str(value or "{}").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    normalized: dict[str, list[str]] = {}
    for key, item in payload.items():
        if isinstance(item, str):
            values = [item]
        elif isinstance(item, list):
            values = [str(value) for value in item]
        else:
            raise ValueError(f"{field_name}.{key} must be a digest string or list")
        normalized[str(key)] = values
    return normalized


def build_settings_from_env(settings_type: type[SettingsT], repo_root: Path | None = None) -> SettingsT:
    root = (repo_root or Path.cwd()).resolve()
    env_values = load_settings_env_values(root)
    return settings_type(**build_settings_kwargs(root, env_values))
