from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
import yaml

from research_system.adapters.storage.redis_route_store import (
    InMemoryRouteTableBackend,
    RedisRouteTableBackend,
    RouteTableBackendError,
)
from research_system.runtime.settings import Settings


RouteIntent = Literal[
    "stock_check",
    "stock_compare",
    "stock_screen",
    "sector_research",
    "distill_kol_logic",
    "review_thesis",
    "methodology_evolution",
]
RouteMatchType = Literal["contains_any", "regex", "semantic"]
RouteTarget = Literal["query", "lowered"]


class IntentRouteRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str | None = None
    intent: RouteIntent
    match_type: RouteMatchType
    priority: int = 100
    target: RouteTarget = "query"
    enabled: bool = True
    pattern: str | None = None
    keywords: list[str] = Field(default_factory=list)
    negative_terms: list[str] = Field(default_factory=list)
    semantic_threshold: float | None = None

    def haystack(self, *, query: str, lowered: str) -> str:
        return query if self.target == "query" else lowered

    def blocked_by_negative_terms(self, *, query: str, lowered: str) -> bool:
        haystack = self.haystack(query=query, lowered=lowered)
        if self.target == "lowered":
            return any(term.lower() in haystack for term in self.negative_terms if term)
        return any(term in haystack for term in self.negative_terms if term)

    def matches(self, *, query: str, lowered: str) -> bool:
        if not self.enabled:
            return False

        if self.blocked_by_negative_terms(query=query, lowered=lowered):
            return False

        haystack = self.haystack(query=query, lowered=lowered)
        if self.match_type == "regex":
            if not self.pattern:
                return False
            try:
                return re.search(self.pattern, haystack) is not None
            except re.error:
                return False

        if self.match_type == "semantic":
            return False

        if not self.keywords:
            return False
        if self.target == "lowered":
            return any(keyword.lower() in haystack for keyword in self.keywords if keyword)
        return any(keyword in haystack for keyword in self.keywords if keyword)


class IntentRouteTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "default-v1"
    rules: list[IntentRouteRule] = Field(default_factory=list)

    def ordered_rules(self) -> list[IntentRouteRule]:
        return sorted((rule for rule in self.rules if rule.enabled), key=lambda rule: rule.priority)


class IntentRouteTableStore:
    def __init__(
        self,
        *,
        backend=None,
        active_key: str = "cognitrade:router:intent_rules:active",
        key_prefix: str = "cognitrade:router:intent_rules:",
        last_good_key: str = "cognitrade:router:intent_rules:last_good",
        cache_ttl_seconds: float = 2.0,
        seed_defaults: bool = True,
        default_table: IntentRouteTable | None = None,
    ) -> None:
        self.backend = backend
        self.active_key = active_key
        self.key_prefix = key_prefix
        self.last_good_key = last_good_key
        self.cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self.seed_defaults = seed_defaults
        self._default_table = default_table or build_default_intent_route_table()
        self._cached_table: IntentRouteTable | None = None
        self._cached_at = 0.0
        self._cached_version: str | None = None
        self._last_refresh_source: str = "default"
        self._last_refresh_timestamp: str | None = None

    def load_table(self) -> IntentRouteTable:
        now = time.monotonic()
        if self._cached_table is not None and (self.cache_ttl_seconds <= 0 or now - self._cached_at < self.cache_ttl_seconds):
            return self._cached_table

        table = self._load_uncached()
        self._cached_table = table
        self._cached_at = now
        return table

    def replace_table(self, table: IntentRouteTable) -> None:
        payload = table.model_dump_json(indent=2)
        if self.backend is None:
            self._default_table = table
            self._cached_table = table
            self._cached_at = time.monotonic()
            self._cached_version = table.version
            self._last_refresh_source = "default"
            return
        version = table.version.strip() or "manual"
        self.backend.set_payload(f"{self.key_prefix}{version}", payload, nx=False)
        self.backend.set_payload(self.active_key, version, nx=False)
        self.backend.set_payload(self.last_good_key, payload, nx=False)
        self.invalidate_cache()

    def invalidate_cache(self) -> None:
        self._cached_table = None
        self._cached_at = 0.0
        self._cached_version = None
        self._last_refresh_source = "default"
        self._last_refresh_timestamp = None

    def describe_runtime_state(self) -> dict[str, object]:
        return {
            "cached_version": self._cached_version,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "refresh_source": self._last_refresh_source,
            "last_refresh_timestamp": self._last_refresh_timestamp,
            "has_backend": self.backend is not None,
        }

    def _load_uncached(self) -> IntentRouteTable:
        if self.backend is None:
            self._cached_version = self._default_table.version
            self._last_refresh_source = "default"
            self._last_refresh_timestamp = self._utc_timestamp()
            return self._default_table

        try:
            active_version = self.backend.get_payload(self.active_key)
            if active_version is None and self.seed_defaults:
                active_version = self._default_table.version
                self.backend.set_payload(f"{self.key_prefix}{active_version}", self._default_table.model_dump_json(indent=2), nx=True)
                self.backend.set_payload(self.active_key, active_version, nx=True)
                self.backend.set_payload(self.last_good_key, self._default_table.model_dump_json(indent=2), nx=True)
        except RouteTableBackendError:
            pass

        if active_version:
            try:
                payload = self.backend.get_payload(f"{self.key_prefix}{active_version}")
                if payload:
                    table = IntentRouteTable.model_validate(json.loads(payload))
                    self._cached_version = table.version
                    self._last_refresh_source = "redis_active"
                    self._last_refresh_timestamp = self._utc_timestamp()
                    self.backend.set_payload(self.last_good_key, payload, nx=False)
                    return table
            except (RouteTableBackendError, ValueError):
                pass

        try:
            last_good_payload = self.backend.get_payload(self.last_good_key)
            if last_good_payload:
                table = IntentRouteTable.model_validate(json.loads(last_good_payload))
                self._cached_version = table.version
                self._last_refresh_source = "redis_last_good"
                self._last_refresh_timestamp = self._utc_timestamp()
                return table
        except (RouteTableBackendError, ValueError):
            pass

        self._cached_version = self._default_table.version
        self._last_refresh_source = "default"
        self._last_refresh_timestamp = self._utc_timestamp()
        return self._default_table

    def probe_backend_health(self) -> dict[str, object]:
        if self.backend is None:
            return {
                "enabled": False,
                "reachable": False,
                "detail": "not_configured",
            }
        ping = getattr(self.backend, "ping", None)
        if not callable(ping):
            return {
                "enabled": True,
                "reachable": False,
                "detail": "ping_unsupported",
            }
        try:
            reachable = bool(ping())
        except RouteTableBackendError as exc:
            return {
                "enabled": True,
                "reachable": False,
                "detail": str(exc),
            }
        return {
            "enabled": True,
            "reachable": reachable,
            "detail": "ok" if reachable else "ping_failed",
        }

    def _utc_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()


def load_route_table_from_file(path: Path) -> IntentRouteTable:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return IntentRouteTable.model_validate(raw)


def build_default_intent_route_table() -> IntentRouteTable:
    return IntentRouteTable(
        version="default-v1",
        rules=[
            IntentRouteRule(rule_id="stock_code_regex", intent="stock_check", match_type="regex", pattern=r"\b\d{6}\b", priority=10, target="query"),
            IntentRouteRule(
                rule_id="stock_compare_cn",
                intent="stock_compare",
                match_type="regex",
                pattern=r"((对比|比较|哪个好|谁更值得|谁更强|哪个更好).*(\d{6}|[一-龥A-Za-z]+).*(\d{6}|[一-龥A-Za-z]+))|((\d{6}|[一-龥A-Za-z]+).*(\d{6}|[一-龥A-Za-z]+).*(对比|比较|哪个好|谁更值得|谁更强|哪个更好))",
                priority=15,
                target="query",
            ),
            IntentRouteRule(
                rule_id="stock_compare_en",
                intent="stock_compare",
                match_type="contains_any",
                keywords=["compare", "comparison", "which is better", "which is best", "better investment"],
                priority=16,
                target="lowered",
            ),
            IntentRouteRule(
                rule_id="stock_screen_cn",
                intent="stock_screen",
                match_type="contains_any",
                keywords=["推荐", "值得", "关注", "选股", "什么股票", "哪些股票"],
                priority=20,
                target="query",
            ),
            IntentRouteRule(
                rule_id="stock_screen_en",
                intent="stock_screen",
                match_type="contains_any",
                keywords=["watchlist", "screen", "candidate", "idea", "worth", "follow", "what stock"],
                priority=21,
                target="lowered",
            ),
            IntentRouteRule(
                rule_id="stock_check_cn",
                intent="stock_check",
                match_type="contains_any",
                keywords=["股票", "个股"],
                priority=30,
                target="query",
            ),
            IntentRouteRule(
                rule_id="stock_check_en",
                intent="stock_check",
                match_type="contains_any",
                keywords=["stock", "ticker"],
                priority=31,
                target="lowered",
            ),
            IntentRouteRule(
                rule_id="sector_research_cn",
                intent="sector_research",
                match_type="contains_any",
                keywords=["行业", "板块", "主题", "指数", "大盘"],
                priority=40,
                target="query",
            ),
            IntentRouteRule(
                rule_id="sector_research_en",
                intent="sector_research",
                match_type="contains_any",
                keywords=["sector", "theme", "industry", "index", "market"],
                priority=41,
                target="lowered",
            ),
            IntentRouteRule(
                rule_id="distill_kol_logic_cn",
                intent="distill_kol_logic",
                match_type="contains_any",
                keywords=["蒸馏", "雪球"],
                priority=50,
                target="query",
            ),
            IntentRouteRule(
                rule_id="distill_kol_logic_en",
                intent="distill_kol_logic",
                match_type="contains_any",
                keywords=["kol", "xueqiu", "logic", "status", "jsonl"],
                priority=51,
                target="lowered",
            ),
            IntentRouteRule(
                rule_id="review_thesis_cn",
                intent="review_thesis",
                match_type="contains_any",
                keywords=["复核", "后验"],
                priority=60,
                target="query",
            ),
            IntentRouteRule(
                rule_id="review_thesis_en",
                intent="review_thesis",
                match_type="contains_any",
                keywords=["review", "review_thesis", "posterior", "validate"],
                priority=61,
                target="lowered",
            ),
        ],
    )


def build_default_intent_route_table_from_settings(settings: Settings | None) -> IntentRouteTable:
    if settings is not None and settings.router_defaults_path.exists():
        try:
            return load_route_table_from_file(settings.router_defaults_path)
        except Exception:
            pass
    return build_default_intent_route_table()


def build_intent_route_table_store(settings: Settings | None) -> IntentRouteTableStore:
    default_table = build_default_intent_route_table_from_settings(settings)
    if settings is None or not settings.router_redis_url:
        return IntentRouteTableStore(default_table=default_table, cache_ttl_seconds=0.0)
    backend = RedisRouteTableBackend(
        settings.router_redis_url,
        timeout_seconds=settings.router_redis_timeout_seconds,
    )
    return IntentRouteTableStore(
        backend=backend,
        active_key=settings.router_redis_active_key,
        key_prefix=settings.router_redis_key_prefix,
        last_good_key=settings.router_redis_last_good_key,
        cache_ttl_seconds=settings.router_rules_cache_ttl_seconds,
        seed_defaults=settings.router_seed_defaults,
        default_table=default_table,
    )
