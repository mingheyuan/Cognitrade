from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from research_system.runtime.orchestration.subagent_discovery_admin_support import (
    OrchestratorSubagentDiscoveryAdminSupport,
)
from research_system.runtime.orchestration.subagent_discovery_store import SubagentDiscoveryStore
from research_system.schemas.subagents import (
    SubagentCapabilityGap,
    SubagentCatalogEntry,
    SubagentToolDiscoveryRequest,
)


class SubagentDiscoveryStoreTests(unittest.TestCase):
    def test_store_creates_and_reviews_discovery_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="review_thesis",
                stage="review",
                capability="peer_review",
                reason="missing peer review capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="review_thesis",
                stage="review",
                capability="peer_review",
                search_query="review_thesis review peer_review",
                reason=gap.reason,
            )

            ticket = store.create_request(gap=gap, request=request)

            self.assertEqual(ticket.ticket_id, request.discovery_request_id)
            self.assertEqual(ticket.status, "pending")
            self.assertEqual(len(store.list("pending")), 1)

            ticket.status = "approved"
            ticket.reviewer = "tester"
            ticket.review_reason = "approved for manual registration"
            ticket.reviewed_at = datetime.now(timezone.utc)
            ticket.updated_at = ticket.reviewed_at
            store.save(ticket, previous_status="pending")

            self.assertEqual(len(store.list("pending")), 0)
            self.assertEqual(len(store.list("approved")), 1)
            found, status = store.find(ticket.ticket_id) or (None, None)
            self.assertEqual(status, "approved")
            self.assertIsNotNone(found)
            self.assertEqual(found.review_reason, "approved for manual registration")

    def test_admin_support_lists_and_reviews_tickets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="retrieve",
                capability="news_sentiment_enrichment",
                reason="missing sentiment enrichment capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="retrieve",
                capability="news_sentiment_enrichment",
                search_query="stock_check retrieve news_sentiment_enrichment",
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

            rows = admin.list_requests()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["ticket_id"], ticket.ticket_id)
            self.assertEqual(rows[0]["capability"], "news_sentiment_enrichment")

            result = admin.review_request(
                ticket.ticket_id,
                decision="approve",
                reviewer="tester",
                reason="approved for follow-up packaging",
            )
            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["result"]["review_reason"], "approved for follow-up packaging")
            self.assertEqual(len(result["result"]["candidate_catalog_entries"]), 1)
            self.assertEqual(
                result["result"]["candidate_catalog_entries"][0]["capabilities"],
                ["news_sentiment_enrichment"],
            )
            approved_entries = store.approved_catalog_entries()
            self.assertEqual(len(approved_entries), 1)
            self.assertIn("approved_discovery_candidate", approved_entries[0].tags)

            activation = admin.activate_request(
                ticket.ticket_id,
                delegate_subagent_type="retrieval_decider",
                activated_by="tester",
                reason="delegate to retrieval decider for controlled execution",
            )
            self.assertIn("activated discovery alias", activation["message"])
            self.assertEqual(len(activation["result"]["activated_entries"]), 1)
            self.assertEqual(
                activation["result"]["activated_entries"][0]["delegate_subagent_type"],
                "retrieval_decider",
            )
            self.assertEqual(store.approved_catalog_entries(), [])
            activated_aliases = store.activated_alias_entries()
            self.assertEqual(len(activated_aliases), 1)
            self.assertEqual(activated_aliases[0].subagent_type, "candidate_retrieve_news_sentiment_enrichment")
            self.assertIn("activated_runtime_alias", activated_aliases[0].catalog_entry.tags)

    def test_admin_support_can_activate_runtime_registered_skill_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                preferred_subagent_type="candidate_collect_market_signal_extension",
                reason="missing market signal extension capability",
            )
            request = SubagentToolDiscoveryRequest(
                intent="stock_check",
                stage="collect",
                capability="market_signal_extension",
                search_query="stock_check collect market_signal_extension",
                reason=gap.reason,
            )
            ticket = store.create_request(gap=gap, request=request)
            host = type(
                "_Host",
                (),
                {
                    "orchestrator_error_cls": RuntimeError,
                    "require_subagent_discovery_store": lambda self: store,
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"market_snapshot_skill": object()})
                    ),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(
                ticket.ticket_id,
                decision="approve",
                candidate_catalog_entries=[
                    SubagentCatalogEntry(
                        subagent_type="candidate_collect_market_signal_extension",
                        summary="Approved discovery candidate for market signal extension.",
                        capabilities=["market_signal_extension"],
                        stage="collect",
                        tags=["approved_discovery_candidate"],
                        suitable_intents=["stock_check"],
                        allow_parallel=True,
                        max_concurrency=4,
                    ).model_dump(mode="json")
                ],
            )

            activation = admin.activate_request(
                ticket.ticket_id,
                registration_type="snapshot_skill_runner",
                skill_name="market_snapshot_skill",
                snapshot_type="market",
                activated_by="tester",
                reason="activate validated market skill",
            )

            self.assertIn("runtime alias", activation["message"])
            self.assertEqual(len(activation["result"]["activated_entries"]), 1)
            activated_entry = activation["result"]["activated_entries"][0]
            self.assertIsNone(activated_entry["delegate_subagent_type"])
            self.assertEqual(activated_entry["runtime_registration"]["registration_type"], "snapshot_skill_runner")
            self.assertEqual(activated_entry["runtime_registration"]["skill_name"], "market_snapshot_skill")
            self.assertEqual(store.approved_catalog_entries(), [])
            activated_aliases = store.activated_alias_entries()
            self.assertEqual(len(activated_aliases), 1)
            self.assertEqual(
                activated_aliases[0].runtime_registration.skill_name,
                "market_snapshot_skill",
            )

    def test_admin_support_can_activate_generic_skill_runner_with_validated_contract(self) -> None:
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
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"analysis_skill": object()})
                    ),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(
                ticket.ticket_id,
                decision="approve",
                candidate_catalog_entries=[
                    SubagentCatalogEntry(
                        subagent_type="candidate_report_signal_extension",
                        summary="Approved discovery candidate for report-stage analysis skill.",
                        capabilities=["report_signal_extension"],
                        stage="report",
                        tags=["approved_discovery_candidate"],
                        suitable_intents=["review_thesis"],
                        allow_parallel=False,
                        input_schema_name="ReportSignalInput",
                        output_schema_name="ReportSignalOutput",
                        input_json_schema={
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "focus": {"type": "string"},
                            },
                            "required": ["query"],
                        },
                        output_json_schema={
                            "type": "object",
                            "properties": {
                                "summary": {"type": "string"},
                                "score": {"type": "number"},
                            },
                            "required": ["summary"],
                        },
                    ).model_dump(mode="json")
                ],
            )

            activation = admin.activate_request(
                ticket.ticket_id,
                registration_type="skill_runner",
                skill_name="analysis_skill",
                activated_by="tester",
                reason="activate validated generic skill runner",
            )

            self.assertIn("runtime alias", activation["message"])
            activated_entry = activation["result"]["activated_entries"][0]
            self.assertEqual(activated_entry["runtime_registration"]["registration_type"], "skill_runner")
            self.assertEqual(activated_entry["runtime_registration"]["skill_name"], "analysis_skill")
            self.assertIsNone(activated_entry["runtime_registration"]["snapshot_type"])
            activated_aliases = store.activated_alias_entries()
            self.assertEqual(len(activated_aliases), 1)
            self.assertEqual(activated_aliases[0].subagent_type, "candidate_report_signal_extension")
            self.assertEqual(activated_aliases[0].runtime_registration.registration_type, "skill_runner")

    def test_admin_support_can_activate_http_json_runner_with_validated_contract(self) -> None:
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
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"analysis_skill": object()})
                    ),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(
                ticket.ticket_id,
                decision="approve",
                candidate_catalog_entries=[
                    SubagentCatalogEntry(
                        subagent_type="candidate_collect_market_signal_http_extension",
                        summary="Approved discovery candidate for HTTP-backed market signals.",
                        capabilities=["market_signal_http_extension"],
                        stage="collect",
                        side_effect_level="read_only",
                        tags=["approved_discovery_candidate"],
                        suitable_intents=["stock_check"],
                        allow_parallel=True,
                        input_schema_name="HttpSignalInput",
                        output_schema_name="HttpSignalOutput",
                        input_json_schema={
                            "type": "object",
                            "properties": {
                                "symbol": {"type": "string"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["symbol"],
                        },
                        output_json_schema={
                            "type": "object",
                            "properties": {
                                "signal": {"type": "string"},
                                "score": {"type": "number"},
                            },
                            "required": ["signal"],
                        },
                    ).model_dump(mode="json")
                ],
            )

            activation = admin.activate_request(
                ticket.ticket_id,
                registration_type="http_json_runner",
                source_id="eastmoney",
                endpoint_path="/api/market-signal",
                http_method="GET",
                timeout_seconds=12.5,
                activated_by="tester",
                reason="activate validated http json runner",
            )

            self.assertIn("runtime alias", activation["message"])
            activated_entry = activation["result"]["activated_entries"][0]
            self.assertEqual(activated_entry["runtime_registration"]["registration_type"], "http_json_runner")
            self.assertEqual(activated_entry["runtime_registration"]["source_id"], "eastmoney")
            self.assertEqual(activated_entry["runtime_registration"]["endpoint_path"], "/api/market-signal")
            self.assertEqual(activated_entry["runtime_registration"]["http_method"], "GET")
            self.assertEqual(activated_entry["runtime_registration"]["timeout_seconds"], 12.5)
            activated_aliases = store.activated_alias_entries()
            self.assertEqual(len(activated_aliases), 1)
            self.assertEqual(
                activated_aliases[0].runtime_registration.registration_type,
                "http_json_runner",
            )

    def test_admin_support_can_activate_openapi_http_runner_from_candidate_hints(self) -> None:
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
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"analysis_skill": object()})
                    ),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(
                ticket.ticket_id,
                decision="approve",
                candidate_catalog_entries=[
                    SubagentCatalogEntry(
                        subagent_type="candidate_collect_market_signal_openapi_extension",
                        summary="Approved discovery candidate for OpenAPI-backed market signals.",
                        capabilities=["market_signal_openapi_extension"],
                        stage="collect",
                        side_effect_level="read_only",
                        tags=["approved_discovery_candidate"],
                        suitable_intents=["stock_check"],
                        input_schema_name="OpenApiSignalInput",
                        output_schema_name="OpenApiSignalOutput",
                        input_json_schema={
                            "type": "object",
                            "properties": {
                                "symbol": {"type": "string"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["symbol"],
                        },
                        output_json_schema={
                            "type": "object",
                            "properties": {
                                "signal": {"type": "string"},
                                "score": {"type": "number"},
                            },
                            "required": ["signal"],
                        },
                        runtime_registration_hints={
                            "openapi": {
                                "source_id": "eastmoney",
                                "operation_id": "getMarketSignal",
                                "spec": {
                                    "openapi": "3.0.0",
                                    "paths": {
                                        "/api/market-signal": {
                                            "get": {
                                                "operationId": "getMarketSignal",
                                            }
                                        }
                                    },
                                },
                            }
                        },
                    ).model_dump(mode="json")
                ],
            )
            approved_entries = store.approved_catalog_entries()
            self.assertEqual(
                approved_entries[0].runtime_registration_templates[0].template_id,
                "openapi:getMarketSignal",
            )

            activation = admin.activate_request(
                ticket.ticket_id,
                registration_template_id="openapi:getMarketSignal",
                activated_by="tester",
                reason="activate validated openapi runner",
            )

            self.assertIn("runtime alias", activation["message"])
            activated_entry = activation["result"]["activated_entries"][0]
            self.assertEqual(activated_entry["runtime_registration"]["registration_type"], "openapi_http_runner")
            self.assertEqual(activated_entry["runtime_registration"]["source_id"], "eastmoney")
            self.assertEqual(activated_entry["runtime_registration"]["endpoint_path"], "/api/market-signal")
            self.assertEqual(activated_entry["runtime_registration"]["http_method"], "GET")
            self.assertEqual(activated_entry["runtime_registration"]["registration_template_id"], "openapi:getMarketSignal")
            self.assertIsNone(activated_entry["runtime_registration"]["openapi_spec_uri"])
            self.assertEqual(activated_entry["runtime_registration"]["openapi_operation_id"], "getMarketSignal")
            activated_aliases = store.activated_alias_entries()
            self.assertEqual(len(activated_aliases), 1)
            self.assertEqual(
                activated_aliases[0].runtime_registration.registration_type,
                "openapi_http_runner",
            )

    def test_admin_support_rejects_snapshot_skill_activation_for_non_collect_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SubagentDiscoveryStore(Path(temp_dir))
            gap = SubagentCapabilityGap(
                intent="review_thesis",
                stage="report",
                capability="report_signal_extension",
                preferred_subagent_type="candidate_report_report_signal_extension",
                reason="missing report-stage signal extension capability",
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
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"market_snapshot_skill": object()})
                    ),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(ticket.ticket_id, decision="approve")

            with self.assertRaisesRegex(RuntimeError, "collect-stage candidates"):
                admin.activate_request(
                    ticket.ticket_id,
                    registration_type="snapshot_skill_runner",
                    skill_name="market_snapshot_skill",
                    snapshot_type="market",
                    activated_by="tester",
                    reason="invalid stage activation",
                )

    def test_admin_support_rejects_generic_skill_runner_without_contract_schema(self) -> None:
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
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"analysis_skill": object()})
                    ),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(ticket.ticket_id, decision="approve")

            with self.assertRaisesRegex(RuntimeError, "input_json_schema"):
                admin.activate_request(
                    ticket.ticket_id,
                    registration_type="skill_runner",
                    skill_name="analysis_skill",
                    activated_by="tester",
                    reason="missing contract schema",
                )

    def test_admin_support_rejects_http_json_runner_for_non_http_source(self) -> None:
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
                    "require_skill_runtime": lambda self: SimpleNamespace(
                        backend=SimpleNamespace(registry={"analysis_skill": object()})
                    ),
                },
            )()
            admin = OrchestratorSubagentDiscoveryAdminSupport(host)
            admin.review_request(
                ticket.ticket_id,
                decision="approve",
                candidate_catalog_entries=[
                    SubagentCatalogEntry(
                        subagent_type="candidate_collect_market_signal_http_extension",
                        summary="Approved discovery candidate for HTTP-backed market signals.",
                        capabilities=["market_signal_http_extension"],
                        stage="collect",
                        side_effect_level="read_only",
                        tags=["approved_discovery_candidate"],
                        suitable_intents=["stock_check"],
                        input_json_schema={
                            "type": "object",
                            "properties": {"symbol": {"type": "string"}},
                            "required": ["symbol"],
                        },
                        output_json_schema={
                            "type": "object",
                            "properties": {"signal": {"type": "string"}},
                            "required": ["signal"],
                        },
                    ).model_dump(mode="json")
                ],
            )

            with self.assertRaisesRegex(RuntimeError, "http-access data source"):
                admin.activate_request(
                    ticket.ticket_id,
                    registration_type="http_json_runner",
                    source_id="akshare",
                    endpoint_path="/api/market-signal",
                    http_method="GET",
                    activated_by="tester",
                    reason="invalid source access",
                )


if __name__ == "__main__":
    unittest.main()
