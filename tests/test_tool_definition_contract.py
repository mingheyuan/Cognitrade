from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time

from research_system.schemas.core_runtime import CoreToolDefinition
from research_system.schemas.domain_pack import PackToolDefinition
from research_system.schemas.tools import ToolDefinition
from research_system.runtime.core.tool_registry import (
    CoreToolContext,
    CoreToolRegistry,
    build_core_tool_registry,
    register_domain_pack_tools,
)
from research_system.adapters.storage.resource_lease import (
    FileResourceLeaseStore,
    RedisResourceLeaseStore,
)
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.schemas.turn_runtime import FollowupTaskResult


class ToolDefinitionContractTests(unittest.TestCase):
    def test_core_and_pack_names_are_compatibility_aliases_of_one_contract(self) -> None:
        self.assertIs(CoreToolDefinition, ToolDefinition)
        self.assertIs(PackToolDefinition, ToolDefinition)

        core = CoreToolDefinition(
            name="core.example",
            description="Core example tool.",
        )
        pack = PackToolDefinition(
            tool_id="pack.example",
            description="Pack example tool.",
            side_effect_level="read_only",
        )

        self.assertEqual(core.name, "core.example")
        self.assertEqual(core.tool_id, "core.example")
        self.assertEqual(pack.name, "pack.example")
        self.assertEqual(pack.tool_id, "pack.example")

    def test_mismatched_identifiers_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ToolDefinition(
                name="core.example",
                tool_id="other.example",
                description="Invalid tool.",
            )

    def test_registry_normalizes_core_namespace(self) -> None:
        core = build_core_tool_registry().definition("core.run.status")
        self.assertEqual(core.namespace, "core")
        self.assertEqual(core.name, core.tool_id)

    def test_followup_task_is_governed_by_the_core_tool_registry(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            received: list[dict] = []
            registry = build_core_tool_registry()
            context = CoreToolContext(
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                turn_id="turn-1",
                run_root=root,
                actor="main_agent",
                allowed_tools=frozenset({"core.followup.task"}),
                followup_task_handler=lambda request: (
                    received.append(request.model_dump(mode="json"))
                    or FollowupTaskResult(
                        accepted=True,
                        delivery="queued",
                        target_child_turn_id=request.target_child_turn_id,
                    )
                ),
                idempotency_key="run-1:followup:core.followup.task",
            )

            result = registry.invoke(
                tool_name="core.followup.task",
                arguments={
                    "parent_turn_id": "turn-1",
                    "target_child_turn_id": "child-1",
                    "target_agent_id": "analyst",
                    "message": "Recheck the evidence.",
                    "operation_id": "followup-1",
                },
                context=context,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.output["delivery"], "queued")
            self.assertEqual(received[0]["operation_id"], "followup-1")

    def test_registry_preserves_pack_namespace_when_adapting_a_pack(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        packs = DomainPackRegistry.load(repository_root / "domain_packs")
        registry = register_domain_pack_tools(build_core_tool_registry(), packs)
        definition = registry.definition("financial_data_provider")
        self.assertEqual(definition.namespace, "pack")
        self.assertEqual(definition.name, definition.tool_id)

    def test_resource_lease_coordinates_two_worker_registries(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            entered = Event()
            release = Event()
            calls: list[str] = []

            definition = ToolDefinition(
                name="external.market.lookup",
                namespace="external",
                description="Serialized market lookup.",
                input_schema={
                    "type": "object",
                    "required": ["symbol"],
                },
                resource_key_fields=["symbol"],
                resource_lease_seconds=5,
            )

            def handler(arguments, _context):
                calls.append(str(arguments["symbol"]))
                entered.set()
                release.wait(timeout=2)
                return {"ok": True}

            lease_root = root / "leases"
            first_registry = CoreToolRegistry(resource_lease_root=lease_root)
            second_registry = CoreToolRegistry(resource_lease_root=lease_root)
            first_registry.register(definition, handler)
            second_registry.register(definition, handler)

            first_context = CoreToolContext(
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                run_root=root / "run-1",
                actor="subagent:one",
                worker_id="worker-1",
                allowed_tools=frozenset({definition.name}),
            )
            second_context = CoreToolContext(
                run_id="run-2",
                thread_id="thread-2",
                request_id="request-2",
                run_root=root / "run-2",
                actor="subagent:two",
                worker_id="worker-2",
                allowed_tools=frozenset({definition.name}),
            )
            first_context.run_root.mkdir(parents=True)
            second_context.run_root.mkdir(parents=True)
            first_result: list = []
            thread = Thread(
                target=lambda: first_result.append(
                    first_registry.invoke(
                        tool_name=definition.name,
                        arguments={"symbol": "600000"},
                        context=first_context,
                    )
                )
            )
            thread.start()
            self.assertTrue(entered.wait(timeout=2))

            blocked = second_registry.invoke(
                tool_name=definition.name,
                arguments={"symbol": "600000"},
                context=second_context,
            )
            self.assertEqual(blocked.status, "rejected")
            self.assertTrue(blocked.retryable)

            release.set()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(first_result[0].status, "completed")

            retried = second_registry.invoke(
                tool_name=definition.name,
                arguments={"symbol": "600000"},
                context=second_context,
            )
            self.assertEqual(retried.status, "completed")
            self.assertEqual(calls, ["600000", "600000"])

    def test_file_resource_lease_can_renew_and_release(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            store = FileResourceLeaseStore(Path(temporary_directory))
            lease = store.acquire(
                resource_key="market:600000",
                metadata={"worker_id": "worker-1"},
                lease_seconds=0.1,
            )
            self.assertIsNotNone(lease)
            assert lease is not None
            renewed = store.renew(lease, lease_seconds=0.3)
            self.assertIsNotNone(renewed)
            self.assertIsNone(
                store.acquire(
                    resource_key="market:600000",
                    metadata={"worker_id": "worker-2"},
                    lease_seconds=0.1,
                )
            )
            self.assertTrue(store.release(renewed or lease))
            self.assertIsNotNone(
                store.acquire(
                    resource_key="market:600000",
                    metadata={"worker_id": "worker-2"},
                    lease_seconds=0.1,
                )
            )

    def test_registry_heartbeat_keeps_short_lease_during_long_tool(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            entered = Event()
            release = Event()
            definition = ToolDefinition(
                name="external.long_lookup",
                namespace="external",
                description="Long-running serialized lookup.",
                input_schema={"type": "object"},
                resource_key_fields=["symbol"],
                resource_lease_seconds=0.12,
            )

            def handler(_arguments, _context):
                entered.set()
                release.wait(timeout=2)
                return {"ok": True}

            lease_root = root / "leases"
            first_registry = CoreToolRegistry(resource_lease_root=lease_root)
            second_registry = CoreToolRegistry(resource_lease_root=lease_root)
            first_registry.register(definition, handler)
            second_registry.register(definition, handler)
            first_context = CoreToolContext(
                run_id="run-1",
                thread_id="thread-1",
                request_id="request-1",
                run_root=root / "run-1",
                actor="subagent:one",
                worker_id="worker-1",
                allowed_tools=frozenset({definition.name}),
            )
            second_context = CoreToolContext(
                run_id="run-2",
                thread_id="thread-2",
                request_id="request-2",
                run_root=root / "run-2",
                actor="subagent:two",
                worker_id="worker-2",
                allowed_tools=frozenset({definition.name}),
            )
            first_context.run_root.mkdir(parents=True)
            second_context.run_root.mkdir(parents=True)
            result: list = []
            thread = Thread(
                target=lambda: result.append(
                    first_registry.invoke(
                        tool_name=definition.name,
                        arguments={"symbol": "600000"},
                        context=first_context,
                    )
                )
            )
            thread.start()
            self.assertTrue(entered.wait(timeout=2))
            time.sleep(0.25)
            blocked = second_registry.invoke(
                tool_name=definition.name,
                arguments={"symbol": "600000"},
                context=second_context,
            )
            self.assertEqual(blocked.status, "rejected")
            self.assertTrue(blocked.retryable)
            release.set()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result[0].status, "completed")

    def test_redis_resource_lease_uses_owner_checked_operations(self) -> None:
        class FakeRedis:
            def __init__(self) -> None:
                self.values: dict[str, tuple[str, float]] = {}

            def _purge(self, key: str) -> None:
                current = self.values.get(key)
                if current is not None and current[1] <= time.monotonic():
                    self.values.pop(key, None)

            def set(self, *, name, value, nx=False, px=None):
                self._purge(name)
                if nx and name in self.values:
                    return False
                self.values[name] = (str(value), time.monotonic() + (int(px) / 1000))
                return True

            def get(self, key):
                self._purge(key)
                current = self.values.get(key)
                return current[0] if current else None

            def eval(self, script, _numkeys, key, token, *args):
                self._purge(key)
                current = self.values.get(key)
                if current is None or current[0] != token:
                    return 0
                if "pexpire" in script:
                    self.values[key] = (current[0], time.monotonic() + (int(args[0]) / 1000))
                    return 1
                self.values.pop(key, None)
                return 1

        store = RedisResourceLeaseStore("redis://unused", client=FakeRedis())
        lease = store.acquire(
            resource_key="market:600000",
            metadata={"worker_id": "worker-1"},
            lease_seconds=0.1,
        )
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertIsNone(
            store.acquire(
                resource_key="market:600000",
                metadata={"worker_id": "worker-2"},
                lease_seconds=0.1,
            )
        )
        renewed = store.renew(lease, lease_seconds=0.3)
        self.assertIsNotNone(renewed)
        self.assertTrue(store.release(renewed or lease))
        self.assertIsNotNone(
            store.acquire(
                resource_key="market:600000",
                metadata={"worker_id": "worker-2"},
                lease_seconds=0.1,
            )
        )


if __name__ == "__main__":
    unittest.main()
