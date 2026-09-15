from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch

from research_system.adapters.storage.resource_lease import (
    FileResourceLeaseStore,
    RedisResourceLeaseStore,
)
from research_system.runtime.core.tool_registry import (
    CoreToolContext,
    CoreToolRegistry,
    build_core_tool_registry,
    register_domain_pack_tools,
)
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.schemas.tools import ToolDefinition
from research_system.schemas.turn_runtime import FollowupTaskResult


class ToolDefinitionContractTests(unittest.TestCase):
    def test_unknown_identifier_field_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ToolDefinition(
                name="core.example",
                tool_id="other.example",
                description="Invalid tool.",
            )

    def test_registry_normalizes_core_namespace(self) -> None:
        core = build_core_tool_registry().definition("core.run.status")
        self.assertEqual(core.namespace, "core")
        self.assertEqual(core.name, "core.run.status")

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
        self.assertEqual(definition.name, "financial_data_provider")

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
            self.assertEqual(blocked.status, "rejected", blocked.error)
            self.assertTrue(blocked.retryable)
            release.set()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result[0].status, "completed")

    def test_file_lease_renewal_cannot_overwrite_a_reclaimed_owner(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            from research_system.adapters.storage import (
                resource_lease as resource_lease_module,
            )

            store = FileResourceLeaseStore(Path(temporary_directory))
            clock = [datetime(2026, 8, 18, tzinfo=timezone.utc)]
            renewal_entered = Event()
            allow_renewal = Event()
            acquire_finished = Event()
            renewal_result: list = []
            acquire_result: list = []
            original_write_json = resource_lease_module.write_json

            def paused_write_json(path, payload):
                renewal_entered.set()
                allow_renewal.wait(timeout=2)
                original_write_json(path, payload)

            with patch.object(resource_lease_module, "utc_now", side_effect=lambda: clock[0]):
                lease = store.acquire(
                    resource_key="market:600000",
                    metadata={"worker_id": "worker-1"},
                    lease_seconds=1,
                )
                self.assertIsNotNone(lease)

                def renew_lease():
                    renewal_result.append(store.renew(lease, lease_seconds=5))

                def acquire_lease():
                    acquire_result.append(
                        store.acquire(
                            resource_key="market:600000",
                            metadata={"worker_id": "worker-2"},
                            lease_seconds=5,
                        )
                    )
                    acquire_finished.set()

                with patch.object(resource_lease_module, "write_json", paused_write_json):
                    renewal_thread = Thread(target=renew_lease)
                    renewal_thread.start()
                    self.assertTrue(renewal_entered.wait(timeout=2))
                    clock[0] += timedelta(seconds=2)

                    acquire_thread = Thread(target=acquire_lease)
                    acquire_thread.start()
                    self.assertFalse(acquire_finished.wait(timeout=0.2))

                    allow_renewal.set()
                    renewal_thread.join(timeout=2)
                    acquire_thread.join(timeout=2)

            self.assertFalse(renewal_thread.is_alive())
            self.assertFalse(acquire_thread.is_alive())
            self.assertIsNotNone(renewal_result[0])
            self.assertIsNone(acquire_result[0])

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
