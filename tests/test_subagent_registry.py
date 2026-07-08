from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict

from research_system.runtime.core.agent_control import AgentControl
from research_system.runtime.core.execution_policy import RuntimeDegradeReason
from research_system.runtime.orchestration.orchestrator_context import build_runtime_context
from research_system.runtime.orchestration.subagent_discovery_admin_support import OrchestratorSubagentDiscoveryAdminSupport
from research_system.runtime.orchestration.subagent_discovery_store import SubagentDiscoveryStore
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.schemas.subagents import SubagentCapabilityGap, SubagentToolDiscoveryRequest
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.subagents import SubagentInvocation
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy
from research_system.subagents.registry import SubagentRegistry


class _SuccessSubagent:
    subagent_type = "success_subagent"

    def run(self, **kwargs):
        return {"result": "ok", "notes": ["done"]}


class _FailureSubagent:
    subagent_type = "failure_subagent"

    def run(self, **kwargs):
        raise RuntimeError("boom")


class _DegradedSubagent:
    subagent_type = "degraded_subagent"

    def run(self, **kwargs):
        return {
            "result": "fallback",
            "degraded": True,
            "degrade_reason": RuntimeDegradeReason.QDRANT_UNAVAILABLE.value,
        }


class _TypedInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


class _TypedOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doubled: int


class _TypedSubagent:
    subagent_type = "typed_subagent"
    input_model = _TypedInputModel
    output_model = _TypedOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Double an integer input.",
        capabilities=("transform", "math"),
        stage="analysis",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("stock_check", "stock_screen"),
        tags=("deterministic",),
    )

    def run(self, *, value: int):
        return {"doubled": value * 2}


class _ParallelOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: int
    thread_name: str


class _ParallelSubagent:
    subagent_type = "parallel_subagent"
    input_model = _TypedInputModel
    output_model = _ParallelOutputModel
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=4)

    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)
        self.lock = threading.Lock()
        self.thread_names: list[str] = []

    def run(self, *, value: int):
        thread_name = threading.current_thread().name
        with self.lock:
            self.thread_names.append(thread_name)
        self.barrier.wait(timeout=0.5)
        return {"result": value * 2, "thread_name": thread_name}


class _RegisteredRuntimeSubagent:
    input_model = _TypedInputModel
    output_model = _TypedOutputModel
    runtime_policy = SubagentRuntimePolicy()

    def __init__(self, subagent_type: str) -> None:
        self.subagent_type = subagent_type
        self.catalog_metadata = SubagentCatalogMetadata(
            summary="Runtime-registered typed alias.",
            capabilities=("sector_signal_enrichment",),
            stage="collect",
            suitable_intents=("stock_check",),
            tags=("runtime_registered",),
        )

    def run(self, *, value: int):
        return {"doubled": value + 1}


class _GenericSkillRuntimeStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def run(self, skill_name: str, **kwargs) -> dict[str, object]:
        self.calls.append((skill_name, dict(kwargs)))
        return {
            "summary": f"{skill_name}:{kwargs.get('query')}",
            "score": float(kwargs.get("score") or 0.0),
        }


class _HttpJsonResponseStub:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _RuntimeRegisteredService(RuntimeBoundSubagentInvoker):
    def __init__(self, *, store: SubagentDiscoveryStore, skill_runtime) -> None:
        self.skill_runtime = skill_runtime
        self.registry = SubagentRegistry(
            [_TypedSubagent()],
            activation_provider=store.activated_alias_entries,
            activation_factory_provider=self._activated_discovery_runtime_subagent,
        )


class SubagentRegistryTests(unittest.TestCase):
    def _build_runtime(self):
        temp_dir = tempfile.TemporaryDirectory()
        run_store = RunStore(Path(temp_dir.name) / "runs")
        request = RequestEnvelope(user_query="test subagent runtime")
        run_id, paths, snapshot = run_store.create_run(request, "test_intent")
        context = build_runtime_context(
            run_id=run_id,
            paths=paths,
            state=snapshot,
            run_timeout_seconds=120,
        )
        return temp_dir, context, request

    def test_agent_control_invokes_subagent_via_registry_success_case(self) -> None:
        temp_dir, context, request = self._build_runtime()
        try:
            registry = SubagentRegistry([_SuccessSubagent()])

            outputs, record = context.agent_control.invoke(
                registry=registry,
                invocation=SubagentInvocation(
                    subagent_type="success_subagent",
                    objective="Run success case.",
                    inputs={},
                ),
                thread_id=context.thread.thread_id,
                turn_id=context.turn.turn_id,
                request_id=request.request_id,
                agent="tester",
                phase="test",
                execution_policy=context.execution_policy,
            )

            self.assertEqual(outputs["result"], "ok")
            self.assertEqual(record.status, "completed")
        finally:
            temp_dir.cleanup()

    def test_agent_control_notifies_started_and_completed(self) -> None:
        temp_dir, context, request = self._build_runtime()
        try:
            registry = SubagentRegistry([_SuccessSubagent()])
            seen: list[tuple[str, str, str | None, str | None]] = []

            registry.add_observer(
                lambda *, stage, invocation, record, outputs, error: seen.append(
                    (stage, invocation.subagent_type, record.status if record is not None else None, invocation.invocation_id)
                )
            )

            outputs, record = context.agent_control.invoke(
                registry=registry,
                invocation=SubagentInvocation(
                    subagent_type="success_subagent",
                    objective="Run success case.",
                    inputs={},
                ),
                thread_id=context.thread.thread_id,
                turn_id=context.turn.turn_id,
                request_id=request.request_id,
                agent="tester",
                phase="test",
                execution_policy=context.execution_policy,
            )

            self.assertEqual(outputs["result"], "ok")
            self.assertEqual(record.status, "completed")
            self.assertEqual(
                [(stage, subagent_type, status) for stage, subagent_type, status, _ in seen],
                [
                    ("started", "success_subagent", None),
                    ("completed", "success_subagent", "completed"),
                ],
            )
            self.assertTrue(all(isinstance(item[3], str) and item[3] for item in seen))
        finally:
            temp_dir.cleanup()

    def test_agent_control_notifies_failed_before_reraising(self) -> None:
        temp_dir, context, request = self._build_runtime()
        try:
            registry = SubagentRegistry([_FailureSubagent()])
            seen: list[tuple[str, str, str | None, str | None]] = []

            registry.add_observer(
                lambda *, stage, invocation, record, outputs, error: seen.append(
                    (stage, invocation.subagent_type, record.status if record is not None else None, invocation.invocation_id)
                )
            )

            with self.assertRaisesRegex(RuntimeError, "boom"):
                context.agent_control.invoke(
                    registry=registry,
                    invocation=SubagentInvocation(
                        subagent_type="failure_subagent",
                        objective="Run failure case.",
                        inputs={},
                    ),
                    thread_id=context.thread.thread_id,
                    turn_id=context.turn.turn_id,
                    request_id=request.request_id,
                    agent="tester",
                    phase="test",
                    execution_policy=context.execution_policy,
                )

            self.assertEqual(
                [(stage, subagent_type, status) for stage, subagent_type, status, _ in seen],
                [
                    ("started", "failure_subagent", None),
                    ("failed", "failure_subagent", "failed"),
                ],
            )
            self.assertTrue(all(isinstance(item[3], str) and item[3] for item in seen))
        finally:
            temp_dir.cleanup()

    def test_agent_control_propagates_degrade_reason_into_record(self) -> None:
        temp_dir, context, request = self._build_runtime()
        try:
            registry = SubagentRegistry([_DegradedSubagent()])

            outputs, record = context.agent_control.invoke(
                registry=registry,
                invocation=SubagentInvocation(
                    subagent_type="degraded_subagent",
                    objective="Run degraded case.",
                    inputs={},
                ),
                thread_id=context.thread.thread_id,
                turn_id=context.turn.turn_id,
                request_id=request.request_id,
                agent="tester",
                phase="test",
                execution_policy=context.execution_policy,
            )

            self.assertEqual(outputs["degrade_reason"], RuntimeDegradeReason.QDRANT_UNAVAILABLE.value)
            self.assertEqual(record.status, "degraded")
            self.assertEqual(record.degrade_reason, RuntimeDegradeReason.QDRANT_UNAVAILABLE.value)
            self.assertEqual(record.degrade_reasons, [RuntimeDegradeReason.QDRANT_UNAVAILABLE.value])
        finally:
            temp_dir.cleanup()

    def test_registry_exposes_typed_subagent_definition(self) -> None:
        registry = SubagentRegistry([_TypedSubagent()])

        definition = registry.definition("typed_subagent")

        self.assertIs(definition.input_model, _TypedInputModel)
        self.assertIs(definition.output_model, _TypedOutputModel)
        self.assertFalse(definition.runtime_policy.allow_parallel)
        self.assertEqual(definition.catalog_metadata.stage, "analysis")
        self.assertEqual(definition.catalog_metadata.capabilities, ("transform", "math"))

    def test_registry_catalog_entries_include_metadata_and_schema(self) -> None:
        registry = SubagentRegistry([_TypedSubagent()])

        entries = registry.catalog_entries()

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.subagent_type, "typed_subagent")
        self.assertEqual(entry.summary, "Double an integer input.")
        self.assertEqual(entry.capabilities, ["transform", "math"])
        self.assertEqual(entry.stage, "analysis")
        self.assertEqual(entry.suitable_intents, ["stock_check", "stock_screen"])
        self.assertEqual(entry.input_schema_name, "_TypedInputModel")
        self.assertEqual(entry.output_schema_name, "_TypedOutputModel")
        self.assertIn("properties", entry.input_json_schema or {})
        self.assertIn("value", (entry.input_json_schema or {}).get("properties", {}))

    def test_registry_catalog_entries_support_filters(self) -> None:
        registry = SubagentRegistry([_TypedSubagent(), _ParallelSubagent()])

        analysis_entries = registry.catalog_entries(stage="analysis")
        stock_screen_entries = registry.catalog_entries(intent="stock_screen")
        transform_entries = registry.catalog_entries(capability="transform")
        llm_entries = registry.catalog_entries(requires_llm=True)

        self.assertEqual([entry.subagent_type for entry in analysis_entries], ["typed_subagent"])
        self.assertEqual([entry.subagent_type for entry in stock_screen_entries], ["typed_subagent"])
        self.assertEqual([entry.subagent_type for entry in transform_entries], ["typed_subagent"])
        self.assertEqual(llm_entries, [])

    def test_registry_can_expose_approved_discovery_overlay_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="analysis",
                capability="sector_signal_enrichment",
                reason="missing sector signal enrichment capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="analysis",
                capability="sector_signal_enrichment",
                search_query="stock_check analysis sector_signal_enrichment",
                reason=gap.reason,
            )
            ticket = store.create_request(gap=gap, request=request)
            host = type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(ticket.ticket_id, decision="approve")

            registry = SubagentRegistry(
                [_TypedSubagent()],
                catalog_overlay_provider=store.approved_catalog_entries,
            )

            default_entries = registry.catalog_entries(stage="analysis", capability="sector_signal_enrichment")
            candidate_entries = registry.catalog_entries(
                stage="analysis",
                capability="sector_signal_enrichment",
                include_candidates=True,
            )

            self.assertEqual(default_entries, [])
            self.assertEqual(len(candidate_entries), 1)
            self.assertEqual(candidate_entries[0].capabilities, ["sector_signal_enrichment"])
            self.assertIn("approved_discovery_candidate", candidate_entries[0].tags)

    def test_registry_can_activate_discovery_alias_and_execute_via_delegate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="analysis",
                capability="sector_signal_enrichment",
                reason="missing sector signal enrichment capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="analysis",
                capability="sector_signal_enrichment",
                search_query="stock_check analysis sector_signal_enrichment",
                reason=gap.reason,
            )
            ticket = store.create_request(gap=gap, request=request)
            host = type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(ticket.ticket_id, decision="approve")
            admin.activate_request(
                ticket.ticket_id,
                delegate_subagent_type="typed_subagent",
                activated_by="tester",
                reason="activate controlled alias",
            )

            registry = SubagentRegistry(
                [_TypedSubagent()],
                catalog_overlay_provider=store.approved_catalog_entries,
                activation_provider=store.activated_alias_entries,
            )

            entries = registry.catalog_entries(stage="analysis", capability="sector_signal_enrichment")
            self.assertEqual([entry.subagent_type for entry in entries], ["candidate_analysis_sector_signal_enrichment"])
            self.assertIn("activated_runtime_alias", entries[0].tags)

            definition = registry.definition("candidate_analysis_sector_signal_enrichment")
            self.assertIs(definition.input_model, _TypedInputModel)
            self.assertIs(definition.output_model, _TypedOutputModel)

            temp_dir_runtime, context, request_envelope = self._build_runtime()
            try:
                outputs, record = context.agent_control.invoke(
                    registry=registry,
                    invocation=SubagentInvocation(
                        subagent_type="candidate_analysis_sector_signal_enrichment",
                        objective="Run activated alias.",
                        inputs={"value": 5},
                    ),
                    thread_id=context.thread.thread_id,
                    turn_id=context.turn.turn_id,
                    request_id=request_envelope.request_id,
                    agent="tester",
                    phase="test",
                    execution_policy=context.execution_policy,
                )

                self.assertEqual(outputs["doubled"], 10)
                self.assertEqual(record.subagent_type, "candidate_analysis_sector_signal_enrichment")
            finally:
                temp_dir_runtime.cleanup()

    def test_registry_can_activate_runtime_registered_alias_via_factory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="sector_signal_enrichment",
                preferred_subagent_type="candidate_collect_sector_signal_enrichment",
                reason="missing sector signal enrichment capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="collect",
                capability="sector_signal_enrichment",
                search_query="stock_check collect sector_signal_enrichment",
                reason=gap.reason,
            )
            ticket = store.create_request(gap=gap, request=request)
            host = type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                    "require_skill_runtime": lambda self: type(
                        "_SkillRuntime",
                        (),
                        {"backend": type("_Backend", (), {"registry": {"analysis_skill": object()}})()},
                    )(),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(ticket.ticket_id, decision="approve")
            admin.activate_request(
                ticket.ticket_id,
                registration_type="snapshot_skill_runner",
                skill_name="analysis_skill",
                snapshot_type="analysis",
                activated_by="tester",
                reason="activate runtime registration",
            )

            registry = SubagentRegistry(
                [_TypedSubagent()],
                activation_provider=store.activated_alias_entries,
                activation_factory_provider=lambda entry: _RegisteredRuntimeSubagent(entry.subagent_type)
                if entry.runtime_registration is not None
                else None,
            )

            entries = registry.catalog_entries(stage="collect", capability="sector_signal_enrichment")
            self.assertEqual([entry.subagent_type for entry in entries], ["candidate_collect_sector_signal_enrichment"])

            definition = registry.definition("candidate_collect_sector_signal_enrichment")
            self.assertIs(definition.input_model, _TypedInputModel)
            self.assertIs(definition.output_model, _TypedOutputModel)

            temp_dir_runtime, context, request_envelope = self._build_runtime()
            try:
                outputs, record = context.agent_control.invoke(
                    registry=registry,
                    invocation=SubagentInvocation(
                        subagent_type="candidate_collect_sector_signal_enrichment",
                        objective="Run runtime-registered alias.",
                        inputs={"value": 5},
                    ),
                    thread_id=context.thread.thread_id,
                    turn_id=context.turn.turn_id,
                    request_id=request_envelope.request_id,
                    agent="tester",
                    phase="test",
                    execution_policy=context.execution_policy,
                )

                self.assertEqual(outputs["doubled"], 6)
                self.assertEqual(record.subagent_type, "candidate_collect_sector_signal_enrichment")
            finally:
                temp_dir_runtime.cleanup()

    def test_shared_runtime_registration_factory_supports_generic_skill_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="review_thesis",
                stage="report",
                capability="report_signal_extension",
                preferred_subagent_type="candidate_report_signal_extension",
                reason="missing report signal extension capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="review_thesis",
                stage="report",
                capability="report_signal_extension",
                search_query="review_thesis report report_signal_extension",
                reason=gap.reason,
            )
            ticket = store.create_request(gap=gap, request=request)
            host = type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                    "require_skill_runtime": lambda self: type(
                        "_SkillRuntime",
                        (),
                        {"backend": type("_Backend", (), {"registry": {"analysis_skill": object()}})()},
                    )(),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(
                ticket.ticket_id,
                decision="approve",
                candidate_catalog_entries=[
                    {
                        "subagent_type": "candidate_report_signal_extension",
                        "summary": "Approved report-stage analysis skill candidate.",
                        "capabilities": ["report_signal_extension"],
                        "stage": "report",
                        "suitable_intents": ["review_thesis"],
                        "tags": ["approved_discovery_candidate"],
                        "input_schema_name": "ReportSignalInput",
                        "output_schema_name": "ReportSignalOutput",
                        "input_json_schema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "score": {"type": "number"},
                            },
                            "required": ["query"],
                        },
                        "output_json_schema": {
                            "type": "object",
                            "properties": {
                                "summary": {"type": "string"},
                                "score": {"type": "number"},
                            },
                            "required": ["summary"],
                        },
                    }
                ],
            )
            admin.activate_request(
                ticket.ticket_id,
                registration_type="skill_runner",
                skill_name="analysis_skill",
                activated_by="tester",
                reason="activate generic runtime registration",
            )

            skill_runtime = _GenericSkillRuntimeStub()
            service = _RuntimeRegisteredService(store=store, skill_runtime=skill_runtime)
            registry = service.registry

            entries = registry.catalog_entries(stage="report", capability="report_signal_extension")
            self.assertEqual([entry.subagent_type for entry in entries], ["candidate_report_signal_extension"])

            definition = registry.definition("candidate_report_signal_extension")
            self.assertIsNotNone(definition.input_model)
            self.assertIsNotNone(definition.output_model)
            self.assertIn("query", (definition.input_model.model_json_schema().get("properties") or {}))
            self.assertIn("summary", (definition.output_model.model_json_schema().get("properties") or {}))

            temp_dir_runtime, context, request_envelope = self._build_runtime()
            try:
                outputs, record = context.agent_control.invoke(
                    registry=registry,
                    invocation=SubagentInvocation(
                        subagent_type="candidate_report_signal_extension",
                        objective="Run generic runtime-registered alias.",
                        inputs={"query": "banks", "score": 0.8},
                    ),
                    thread_id=context.thread.thread_id,
                    turn_id=context.turn.turn_id,
                    request_id=request_envelope.request_id,
                    agent="tester",
                    phase="report",
                    execution_policy=context.execution_policy,
                )

                self.assertEqual(outputs["summary"], "analysis_skill:banks")
                self.assertEqual(outputs["score"], 0.8)
                self.assertEqual(record.subagent_type, "candidate_report_signal_extension")
                self.assertEqual(skill_runtime.calls, [("analysis_skill", {"query": "banks", "score": 0.8})])
            finally:
                temp_dir_runtime.cleanup()

    def test_shared_runtime_registration_factory_supports_http_json_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_http_extension",
                preferred_subagent_type="candidate_collect_market_signal_http_extension",
                reason="missing http-backed market signal extension capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="collect",
                capability="market_signal_http_extension",
                search_query="stock_check collect market_signal_http_extension",
                reason=gap.reason,
            )
            ticket = store.create_request(gap=gap, request=request)
            host = type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                    "require_skill_runtime": lambda self: type(
                        "_SkillRuntime",
                        (),
                        {"backend": type("_Backend", (), {"registry": {"analysis_skill": object()}})()},
                    )(),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(
                ticket.ticket_id,
                decision="approve",
                candidate_catalog_entries=[
                    {
                        "subagent_type": "candidate_collect_market_signal_http_extension",
                        "summary": "Approved HTTP-backed market signal candidate.",
                        "capabilities": ["market_signal_http_extension"],
                        "stage": "collect",
                        "side_effect_level": "read_only",
                        "suitable_intents": ["stock_check"],
                        "tags": ["approved_discovery_candidate"],
                        "input_schema_name": "HttpSignalInput",
                        "output_schema_name": "HttpSignalOutput",
                        "input_json_schema": {
                            "type": "object",
                            "properties": {
                                "symbol": {"type": "string"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["symbol"],
                        },
                        "output_json_schema": {
                            "type": "object",
                            "properties": {
                                "signal": {"type": "string"},
                                "score": {"type": "number"},
                            },
                            "required": ["signal"],
                        },
                    }
                ],
            )
            admin.activate_request(
                ticket.ticket_id,
                registration_type="http_json_runner",
                source_id="eastmoney",
                endpoint_path="/api/market-signal",
                http_method="GET",
                timeout_seconds=9.5,
                activated_by="tester",
                reason="activate http runtime registration",
            )

            service = _RuntimeRegisteredService(store=store, skill_runtime=None)
            registry = service.registry

            definition = registry.definition("candidate_collect_market_signal_http_extension")
            self.assertIsNotNone(definition.input_model)
            self.assertIsNotNone(definition.output_model)
            self.assertIn("symbol", (definition.input_model.model_json_schema().get("properties") or {}))
            self.assertIn("signal", (definition.output_model.model_json_schema().get("properties") or {}))

            temp_dir_runtime, context, request_envelope = self._build_runtime()
            try:
                with patch(
                    "research_system.subagents.runtime_registered.requests.request",
                    return_value=_HttpJsonResponseStub({"signal": "bullish", "score": 0.91}),
                ) as mock_request:
                    outputs, record = context.agent_control.invoke(
                        registry=registry,
                        invocation=SubagentInvocation(
                            subagent_type="candidate_collect_market_signal_http_extension",
                            objective="Run http runtime-registered alias.",
                            inputs={"symbol": "600036", "limit": 3},
                        ),
                        thread_id=context.thread.thread_id,
                        turn_id=context.turn.turn_id,
                        request_id=request_envelope.request_id,
                        agent="tester",
                        phase="collect",
                        execution_policy=context.execution_policy,
                    )

                self.assertEqual(outputs["signal"], "bullish")
                self.assertEqual(outputs["score"], 0.91)
                self.assertEqual(record.subagent_type, "candidate_collect_market_signal_http_extension")
                mock_request.assert_called_once_with(
                    "GET",
                    "https://www.eastmoney.com/api/market-signal",
                    params={"symbol": "600036", "limit": 3},
                    timeout=9.5,
                )
            finally:
                temp_dir_runtime.cleanup()

    def test_shared_runtime_registration_factory_supports_openapi_http_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_openapi_extension",
                preferred_subagent_type="candidate_collect_market_signal_openapi_extension",
                reason="missing openapi-backed market signal extension capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="collect",
                capability="market_signal_openapi_extension",
                search_query="stock_check collect market_signal_openapi_extension",
                reason=gap.reason,
            )
            ticket = store.create_request(gap=gap, request=request)
            host = type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                    "require_skill_runtime": lambda self: type(
                        "_SkillRuntime",
                        (),
                        {"backend": type("_Backend", (), {"registry": {"analysis_skill": object()}})()},
                    )(),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(
                ticket.ticket_id,
                decision="approve",
                candidate_catalog_entries=[
                    {
                        "subagent_type": "candidate_collect_market_signal_openapi_extension",
                        "summary": "Approved OpenAPI-backed market signal candidate.",
                        "capabilities": ["market_signal_openapi_extension"],
                        "stage": "collect",
                        "side_effect_level": "read_only",
                        "suitable_intents": ["stock_check"],
                        "tags": ["approved_discovery_candidate"],
                        "input_schema_name": "OpenApiSignalInput",
                        "output_schema_name": "OpenApiSignalOutput",
                        "input_json_schema": {
                            "type": "object",
                            "properties": {
                                "symbol": {"type": "string"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["symbol"],
                        },
                        "output_json_schema": {
                            "type": "object",
                            "properties": {
                                "signal": {"type": "string"},
                                "score": {"type": "number"},
                            },
                            "required": ["signal"],
                        },
                        "runtime_registration_hints": {
                            "openapi": {
                                "source_id": "eastmoney",
                                "spec": {
                                    "openapi": "3.0.0",
                                    "paths": {
                                        "/api/market-signal": {
                                            "get": {
                                                "operationId": "getMarketSignal",
                                            }
                                        }
                                    },
                                }
                            }
                        },
                    }
                ],
            )
            admin.activate_request(
                ticket.ticket_id,
                registration_template_id="openapi:getMarketSignal",
                activated_by="tester",
                reason="activate openapi runtime registration",
            )

            service = _RuntimeRegisteredService(store=store, skill_runtime=None)
            registry = service.registry

            definition = registry.definition("candidate_collect_market_signal_openapi_extension")
            self.assertIsNotNone(definition.input_model)
            self.assertIsNotNone(definition.output_model)
            self.assertIn("symbol", (definition.input_model.model_json_schema().get("properties") or {}))
            self.assertIn("signal", (definition.output_model.model_json_schema().get("properties") or {}))

            temp_dir_runtime, context, request_envelope = self._build_runtime()
            try:
                with patch(
                    "research_system.subagents.runtime_registered.requests.request",
                    return_value=_HttpJsonResponseStub({"signal": "bullish", "score": 0.91}),
                ) as mock_request:
                    outputs, record = context.agent_control.invoke(
                        registry=registry,
                        invocation=SubagentInvocation(
                            subagent_type="candidate_collect_market_signal_openapi_extension",
                            objective="Run openapi runtime-registered alias.",
                            inputs={"symbol": "600036", "limit": 3},
                        ),
                        thread_id=context.thread.thread_id,
                        turn_id=context.turn.turn_id,
                        request_id=request_envelope.request_id,
                        agent="tester",
                        phase="collect",
                        execution_policy=context.execution_policy,
                    )

                self.assertEqual(outputs["signal"], "bullish")
                self.assertEqual(outputs["score"], 0.91)
                self.assertEqual(record.subagent_type, "candidate_collect_market_signal_openapi_extension")
                mock_request.assert_called_once_with(
                    "GET",
                    "https://www.eastmoney.com/api/market-signal",
                    params={"symbol": "600036", "limit": 3},
                    timeout=20.0,
                )
            finally:
                temp_dir_runtime.cleanup()

    def test_agent_control_validates_typed_subagent_inputs_and_outputs(self) -> None:
        temp_dir, context, request = self._build_runtime()
        try:
            registry = SubagentRegistry([_TypedSubagent()])

            outputs, record = context.agent_control.invoke(
                registry=registry,
                invocation=SubagentInvocation(
                    subagent_type="typed_subagent",
                    objective="Run typed case.",
                    inputs={"value": "3"},
                ),
                thread_id=context.thread.thread_id,
                turn_id=context.turn.turn_id,
                request_id=request.request_id,
                agent="tester",
                phase="test",
                execution_policy=context.execution_policy,
            )

            self.assertEqual(outputs["doubled"], 6)
            self.assertEqual(record.output_keys, ["doubled"])
        finally:
            temp_dir.cleanup()

    def test_agent_control_run_many_bound_executes_parallel_subagents_in_input_order(self) -> None:
        temp_dir, context, request = self._build_runtime()
        try:
            subagent = _ParallelSubagent()
            registry = SubagentRegistry([subagent])

            results = context.agent_control.run_many_bound(
                registry=registry,
                invocations=[
                    SubagentInvocation(
                        subagent_type="parallel_subagent",
                        objective="Run parallel item 1.",
                        inputs={"value": 1},
                    ),
                    SubagentInvocation(
                        subagent_type="parallel_subagent",
                        objective="Run parallel item 2.",
                        inputs={"value": 2},
                    ),
                ],
                binding={
                    "thread_id": context.thread.thread_id,
                    "turn_id": context.turn.turn_id,
                    "request_id": request.request_id,
                    "agent": "tester",
                    "phase": "test",
                    "execution_policy": context.execution_policy,
                },
                max_workers=2,
            )

            self.assertEqual([outputs["result"] for outputs, _ in results], [2, 4])
            self.assertGreaterEqual(len(set(subagent.thread_names)), 2)
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
