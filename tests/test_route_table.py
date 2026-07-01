from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.services.route_table import (
    InMemoryRouteTableBackend,
    IntentRouteRule,
    IntentRouteTable,
    IntentRouteTableStore,
    build_intent_route_table_store,
)
from research_system.services.task_router import TaskRouter


class RouteTableStoreTests(unittest.TestCase):
    def test_store_seeds_active_version_and_uses_active_payload(self) -> None:
        backend = InMemoryRouteTableBackend()
        default_table = IntentRouteTable(
            version="seed-v1",
            rules=[
                IntentRouteRule(
                    rule_id="screen_cn",
                    intent="stock_screen",
                    match_type="contains_any",
                    keywords=["值得"],
                    priority=10,
                    target="query",
                )
            ],
        )
        store = IntentRouteTableStore(
            backend=backend,
            active_key="test:active",
            key_prefix="test:",
            last_good_key="test:last_good",
            cache_ttl_seconds=0.0,
            seed_defaults=True,
            default_table=default_table,
        )

        loaded = store.load_table()

        self.assertEqual(loaded.version, "seed-v1")
        self.assertEqual(backend.get_payload("test:active"), "seed-v1")
        self.assertIsNotNone(backend.get_payload("test:seed-v1"))
        self.assertIsNotNone(backend.get_payload("test:last_good"))

    def test_store_falls_back_to_last_good_when_active_payload_is_invalid(self) -> None:
        backend = InMemoryRouteTableBackend(
            {
                "test:active": "broken-v2",
                "test:broken-v2": "{not-json}",
                "test:last_good": IntentRouteTable(
                    version="good-v1",
                    rules=[
                        IntentRouteRule(
                            rule_id="review_cn",
                            intent="review_thesis",
                            match_type="contains_any",
                            keywords=["复核"],
                            priority=10,
                            target="query",
                        )
                    ],
                ).model_dump_json(indent=2),
            }
        )
        store = IntentRouteTableStore(
            backend=backend,
            active_key="test:active",
            key_prefix="test:",
            last_good_key="test:last_good",
            cache_ttl_seconds=0.0,
            seed_defaults=False,
            default_table=IntentRouteTable(version="default-v1", rules=[]),
        )

        loaded = store.load_table()

        self.assertEqual(loaded.version, "good-v1")
        runtime = store.describe_runtime_state()
        self.assertEqual(runtime["refresh_source"], "redis_last_good")

    def test_store_refreshes_after_cache_ttl(self) -> None:
        backend = InMemoryRouteTableBackend(
            {
                "test:active": "v1",
                "test:v1": IntentRouteTable(
                    version="v1",
                    rules=[
                        IntentRouteRule(
                            rule_id="stock_cn",
                            intent="stock_check",
                            match_type="contains_any",
                            keywords=["股票"],
                            priority=10,
                            target="query",
                        )
                    ],
                ).model_dump_json(indent=2),
            }
        )
        store = IntentRouteTableStore(
            backend=backend,
            active_key="test:active",
            key_prefix="test:",
            last_good_key="test:last_good",
            cache_ttl_seconds=0.05,
            seed_defaults=False,
        )

        first = store.load_table()
        backend.set_payload(
            "test:v2",
            IntentRouteTable(
                version="v2",
                rules=[
                    IntentRouteRule(
                        rule_id="sector_cn",
                        intent="sector_research",
                        match_type="contains_any",
                        keywords=["板块"],
                        priority=10,
                        target="query",
                    )
                ],
            ).model_dump_json(indent=2),
            nx=False,
        )
        backend.set_payload("test:active", "v2", nx=False)
        second = store.load_table()
        time.sleep(0.06)
        third = store.load_table()

        self.assertEqual(first.version, "v1")
        self.assertEqual(second.version, "v1")
        self.assertEqual(third.version, "v2")

    def test_two_store_instances_converge_after_cache_ttl(self) -> None:
        backend = InMemoryRouteTableBackend(
            {
                "test:active": "v1",
                "test:v1": IntentRouteTable(
                    version="v1",
                    rules=[
                        IntentRouteRule(
                            rule_id="screen_rule",
                            intent="stock_screen",
                            match_type="contains_any",
                            keywords=["机会"],
                            priority=10,
                            target="query",
                        )
                    ],
                ).model_dump_json(indent=2),
            }
        )
        first_store = IntentRouteTableStore(
            backend=backend,
            active_key="test:active",
            key_prefix="test:",
            last_good_key="test:last_good",
            cache_ttl_seconds=0.01,
            seed_defaults=False,
        )
        second_store = IntentRouteTableStore(
            backend=backend,
            active_key="test:active",
            key_prefix="test:",
            last_good_key="test:last_good",
            cache_ttl_seconds=0.01,
            seed_defaults=False,
        )

        self.assertEqual(first_store.load_table().version, "v1")
        self.assertEqual(second_store.load_table().version, "v1")

        backend.set_payload(
            "test:v2",
            IntentRouteTable(
                version="v2",
                rules=[
                    IntentRouteRule(
                        rule_id="stock_rule",
                        intent="stock_check",
                        match_type="contains_any",
                        keywords=["机会"],
                        priority=10,
                        target="query",
                    )
                ],
            ).model_dump_json(indent=2),
            nx=False,
        )
        backend.set_payload("test:active", "v2", nx=False)
        time.sleep(0.02)

        self.assertEqual(first_store.load_table().version, "v2")
        self.assertEqual(second_store.load_table().version, "v2")

    def test_backend_health_reports_not_configured_for_local_store(self) -> None:
        store = IntentRouteTableStore(default_table=IntentRouteTable(version="v1", rules=[]), cache_ttl_seconds=0.0)

        health = store.probe_backend_health()

        self.assertFalse(health["enabled"])
        self.assertFalse(health["reachable"])
        self.assertEqual(health["detail"], "not_configured")


class TaskRouterRouteTableTests(unittest.TestCase):
    def test_router_uses_route_table_before_llm(self) -> None:
        store = IntentRouteTableStore(
            default_table=IntentRouteTable(
                version="route-v1",
                rules=[
                    IntentRouteRule(
                        rule_id="screen_cn",
                        intent="stock_screen",
                        match_type="contains_any",
                        keywords=["值得"],
                        priority=10,
                        target="query",
                    )
                ],
            ),
            cache_ttl_seconds=0.0,
        )
        router = TaskRouter(route_table_store=store)

        decision = router.identify(type("Req", (), {"intent": None, "user_query": "最近有什么值得关注的票", "constraints": {}})())

        self.assertEqual(decision.intent, "stock_screen")
        self.assertEqual((decision.trace or {}).get("source"), "route_table")
        self.assertEqual((decision.trace or {}).get("rule_id"), "screen_cn")

    def test_router_routes_multi_stock_comparison_to_stock_compare(self) -> None:
        router = TaskRouter()

        decision = router.identify(
            type(
                "Req",
                (),
                {
                    "intent": None,
                    "user_query": "分析茅台，宁德时代，比亚迪，谁更值得投资",
                    "constraints": {},
                },
            )()
        )

        self.assertEqual(decision.intent, "stock_compare")
        self.assertEqual((decision.trace or {}).get("source"), "route_table")
        self.assertEqual((decision.trace or {}).get("rule_id"), "stock_compare_cn")

    def test_router_dynamic_hot_reload_changes_result_without_restart(self) -> None:
        backend = InMemoryRouteTableBackend(
            {
                "test:active": "v1",
                "test:v1": IntentRouteTable(
                    version="v1",
                    rules=[
                        IntentRouteRule(
                            rule_id="stock_rule",
                            intent="stock_check",
                            match_type="contains_any",
                            keywords=["机会"],
                            priority=10,
                            target="query",
                        )
                    ],
                ).model_dump_json(indent=2),
            }
        )
        store = IntentRouteTableStore(
            backend=backend,
            active_key="test:active",
            key_prefix="test:",
            last_good_key="test:last_good",
            cache_ttl_seconds=0.01,
            seed_defaults=False,
        )
        router = TaskRouter(route_table_store=store)
        request = type("Req", (), {"intent": None, "user_query": "最近有什么机会", "constraints": {}})()

        first = router.identify(request)
        backend.set_payload(
            "test:v2",
            IntentRouteTable(
                version="v2",
                rules=[
                    IntentRouteRule(
                        rule_id="screen_rule",
                        intent="stock_screen",
                        match_type="contains_any",
                        keywords=["机会"],
                        priority=10,
                        target="query",
                    )
                ],
            ).model_dump_json(indent=2),
            nx=False,
        )
        backend.set_payload("test:active", "v2", nx=False)
        time.sleep(0.02)
        second = router.identify(request)

        self.assertEqual(first.intent, "stock_check")
        self.assertEqual(second.intent, "stock_screen")
        self.assertEqual((second.trace or {}).get("route_table_version"), "v2")

    def test_regex_rule_takes_precedence_over_semantic_rule(self) -> None:
        store = IntentRouteTableStore(
            default_table=IntentRouteTable(
                version="precedence-v1",
                rules=[
                    IntentRouteRule(
                        rule_id="stock_code_regex",
                        intent="stock_check",
                        match_type="regex",
                        pattern=r"\b\d{6}\b",
                        priority=10,
                        target="query",
                    ),
                    IntentRouteRule(
                        rule_id="screen_semantic",
                        intent="stock_screen",
                        match_type="semantic",
                        keywords=["worth follow", "watchlist", "opportunity"],
                        priority=20,
                        target="lowered",
                        semantic_threshold=0.2,
                    ),
                ],
            ),
            cache_ttl_seconds=0.0,
        )
        router = TaskRouter(route_table_store=store)

        decision = router.identify(type("Req", (), {"intent": None, "user_query": "000001 looks like a worth follow opportunity", "constraints": {}})())

        self.assertEqual(decision.intent, "stock_check")
        self.assertEqual((decision.trace or {}).get("match_type"), "regex")
        self.assertEqual((decision.trace or {}).get("source"), "route_table")

    def test_semantic_rule_routes_when_no_regex_or_keyword_rule_matches(self) -> None:
        store = IntentRouteTableStore(
            default_table=IntentRouteTable(
                version="semantic-v1",
                rules=[
                    IntentRouteRule(
                        rule_id="screen_semantic",
                        intent="stock_screen",
                        match_type="semantic",
                        keywords=["worth follow", "watchlist", "candidate", "opportunity"],
                        priority=20,
                        target="lowered",
                        semantic_threshold=0.25,
                    )
                ],
            ),
            cache_ttl_seconds=0.0,
        )
        router = TaskRouter(route_table_store=store)

        decision = router.identify(type("Req", (), {"intent": None, "user_query": "Any follow ideas for the next swing opportunity?", "constraints": {}})())

        self.assertEqual(decision.intent, "stock_screen")
        self.assertEqual((decision.trace or {}).get("source"), "route_table_semantic")
        self.assertEqual((decision.trace or {}).get("match_type"), "semantic")
        self.assertGreater((decision.trace or {}).get("semantic_score", 0.0), 0.0)


class RouteDefaultsFileTests(unittest.TestCase):
    def test_settings_build_route_store_from_router_defaults_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            settings.router_defaults_path.write_text(
                (
                    "version: file-v1\n"
                    "rules:\n"
                    "  - rule_id: custom_sector\n"
                    "    intent: sector_research\n"
                    "    match_type: contains_any\n"
                    "    priority: 1\n"
                    "    target: query\n"
                    "    keywords:\n"
                    "      - 赛道\n"
                    "    enabled: true\n"
                ),
                encoding="utf-8",
            )
            store = build_intent_route_table_store(settings)
            router = TaskRouter(route_table_store=store)

            decision = router.identify(type("Req", (), {"intent": None, "user_query": "这个赛道怎么看", "constraints": {}})())

            self.assertEqual(decision.intent, "sector_research")
            self.assertEqual((decision.trace or {}).get("route_table_version"), "file-v1")


if __name__ == "__main__":
    unittest.main()
