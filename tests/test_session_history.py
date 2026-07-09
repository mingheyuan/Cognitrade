from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.storage.jsonl import append_jsonl, write_json
from research_system.runtime.history.session.trace.run_trace_row_builder import RunTraceRowBuilder
from research_system.runtime.history.session.trace.subagent_payload_builder import (
    SessionHistorySubagentPayloadBuilder,
)
from research_system.runtime.history.session.steps.step_builder import SessionHistoryStepBuilder
from research_system.runtime.history.session.trace.trace_rebuilder import (
    SessionHistoryTraceRebuilder,
)
from research_system.runtime.history.session.session_history import SessionHistory


class SessionHistoryTests(unittest.TestCase):
    def test_session_history_wires_trace_rebuilder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01WIRE"
            run_root.mkdir(parents=True, exist_ok=True)
            history = SessionHistory(run_root)
            self.assertIsInstance(history.trace_rebuilder, SessionHistoryTraceRebuilder)
            self.assertIsInstance(history.trace_rebuilder.row_builder, RunTraceRowBuilder)
            self.assertIsInstance(
                history.trace_rebuilder.subagent_payload_builder,
                SessionHistorySubagentPayloadBuilder,
            )
            self.assertIsInstance(history.step_builder, SessionHistoryStepBuilder)

    def test_events_builds_typed_event_timeline_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01EVENTS"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_compare", "user_query": "compare banks"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt1",
                    "request_id": "req1",
                    "run_id": "01EVENTS",
                    "intent": "stock_compare",
                    "event_type": "subagent_completed",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector completed",
                    "payload": {
                        "action_id": "act_collect",
                        "node_id": "collect",
                        "task_name": "collect:stock_snapshot_collector",
                        "agent_path": "collector/stock_snapshot_collector",
                        "agent_label": "Stock Snapshot Collector",
                        "subagent_type": "stock_snapshot_collector",
                        "invocation_id": "01INVOCATION",
                        "status": "completed",
                        "objective": "Collect stock-level verified snapshots.",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-02T10:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).events()

        self.assertEqual(payload["run_id"], "01EVENTS")
        self.assertEqual(payload["request"]["intent"], "stock_compare")
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["event_type"], "subagent_completed")
        self.assertEqual(payload["events"][0]["task_name"], "collect:stock_snapshot_collector")
        self.assertEqual(payload["events"][0]["agent_path"], "collector/stock_snapshot_collector")
        self.assertEqual(payload["events"][0]["agent_label"], "Stock Snapshot Collector")
        self.assertEqual(payload["events"][0]["invocation_id"], "01INVOCATION")
        self.assertEqual(payload["event_counts"]["events"], 1)
        self.assertEqual(payload["event_counts"]["distinct_event_types"], 1)

    def test_trace_aggregates_debug_and_wait_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACE"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_compare", "user_query": "compare banks"})
            write_json(
                run_root / "result.json",
                {
                    "report": {
                        "debug": {
                            "subagent_trace": [
                                {
                                    "action_id": "act_retrieve",
                                    "node_id": "retrieve",
                                    "action_objective": "Retrieve evidence.",
                                    "task_name": "retrieve:retrieval_decider",
                                    "agent_path": "retriever/retrieval_decider",
                                    "agent_label": "Retrieval Decider",
                                    "invocation_id": "01INVOCATION",
                                    "subagent_type": "retrieval_decider",
                                    "status": "completed",
                                    "phase": "retrieve",
                                    "agent": "retriever",
                                    "objective": "Select final evidence set and summarize retrieval.",
                                }
                            ],
                            "dynamic_trace": [
                                {
                                    "event": "dynamic_action_inserted",
                                    "action_id": "act_screen_review",
                                    "node_id": "screen_review",
                                    "phase": "review",
                                    "agent": "screen_reviewer",
                                    "objective": "Review screened candidates.",
                                }
                            ],
                            "action_plan_patches": [
                                {
                                    "patch_id": "patch-screen-enrich",
                                    "plan_id": "plan-screen",
                                    "intent": "stock_screen",
                                    "decision_type": "continue",
                                    "thread_id": "thread-root",
                                    "turn_id": "turn-root",
                                    "operations": [
                                        {
                                            "op_type": "rewire_dependency",
                                            "target_node_id": "retrieve",
                                            "new_depends_on": ["screen_enrich"],
                                        }
                                    ],
                                    "notes": ["rewire_retrieve_to_screen_enrich"],
                                    "policy_source": "rule",
                                }
                            ],
                        }
                    }
                },
            )
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt1",
                    "request_id": "req1",
                    "run_id": "01TRACE",
                    "intent": "stock_compare",
                    "event_type": "phase_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "Entered waiting_external state",
                    "payload": {
                        "action_id": "act_collect",
                        "node_id": "collect",
                        "objective": "Load source records.",
                        "wait_timeout_seconds": 30,
                    },
                    "artifacts": [],
                    "created_at": "2026-07-01T12:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(payload["run_id"], "01TRACE")
        self.assertEqual(payload["request"]["intent"], "stock_compare")
        self.assertEqual(len(payload["subagent_trace"]), 1)
        self.assertEqual(payload["subagent_trace"][0]["subagent_type"], "retrieval_decider")
        self.assertEqual(payload["subagent_trace"][0]["node_id"], "retrieve")
        self.assertEqual(payload["subagent_trace"][0]["task_name"], "retrieve:retrieval_decider")
        self.assertEqual(payload["subagent_trace"][0]["agent_path"], "retriever/retrieval_decider")
        self.assertEqual(payload["subagent_trace"][0]["agent_label"], "Retrieval Decider")
        self.assertEqual(payload["subagent_trace"][0]["invocation_id"], "01INVOCATION")
        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["node_id"], "screen_review")
        self.assertEqual(len(payload["action_plan_patches"]), 1)
        self.assertEqual(payload["action_plan_patches"][0]["patch_id"], "patch-screen-enrich")
        self.assertEqual(len(payload["wait_trace"]), 1)
        self.assertEqual(payload["wait_trace"][0]["event"], "waiting_external_started")
        self.assertEqual(payload["wait_trace"][0]["node_id"], "collect")
        self.assertEqual(payload["event_counts"]["subagent_trace"], 1)
        self.assertEqual(payload["event_counts"]["dynamic_trace"], 1)
        self.assertEqual(payload["event_counts"]["action_plan_patches"], 1)
        self.assertEqual(payload["event_counts"]["wait_trace"], 1)

    def test_trace_rebuilds_subagent_dag_dynamic_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACEDAG"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "distill_kol_logic", "user_query": "distill records"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-dag-1",
                    "request_id": "req-dag",
                    "run_id": "01TRACEDAG",
                    "intent": "distill_kol_logic",
                    "event_type": "subagent_dag_initialized",
                    "phase": "distill",
                    "agent": "distiller",
                    "message": "Initialized subagent DAG execution",
                    "payload": {
                        "task_name": "distill:preprocess:source_normalizer",
                        "agent_path": "distiller/preprocess/source_normalizer",
                        "objective": "Normalize raw distillation source records.",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-07T10:00:00+00:00",
                },
            )
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-dag-2",
                    "request_id": "req-dag",
                    "run_id": "01TRACEDAG",
                    "intent": "distill_kol_logic",
                    "event_type": "subagent_dag_node_completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "message": "Completed DAG node: source_normalizer",
                    "payload": {
                        "task_name": "distill:preprocess:source_normalizer",
                        "agent_path": "distiller/preprocess/source_normalizer",
                        "subagent_type": "distill_source_normalizer",
                        "status": "completed",
                        "objective": "Normalize raw distillation source records.",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-07T10:00:01+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 2)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_dag_initialized")
        self.assertEqual(payload["dynamic_trace"][1]["event"], "subagent_dag_node_completed")
        self.assertEqual(
            payload["dynamic_trace"][1]["task_name"],
            "distill:preprocess:source_normalizer",
        )

    def test_trace_rebuilds_action_phase_shadow_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACESHADOW"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_screen", "user_query": "screen growth stocks"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-shadow-1",
                    "request_id": "req-shadow",
                    "run_id": "01TRACESHADOW",
                    "intent": "stock_screen",
                    "event_type": "action_phase_shadow_suggested",
                    "phase": "supervision",
                    "agent": "supervisor",
                    "message": "Shadow action-phase policy evaluated the current frontier",
                    "payload": {
                        "selected_action_node_id": "screen_review",
                        "request_replan": True,
                        "actual_action_node_id": "screen_review",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-08T10:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "action_phase_shadow_suggested")
        self.assertEqual(payload["dynamic_trace"][0]["selected_action_node_id"], "screen_review")
        self.assertEqual(payload["dynamic_trace"][0]["actual_action_node_id"], "screen_review")

    def test_trace_rebuilds_action_phase_decision_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACEACTIONDECIDE"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_screen", "user_query": "screen growth stocks"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-action-decide-1",
                    "request_id": "req-action-decide",
                    "run_id": "01TRACEACTIONDECIDE",
                    "intent": "stock_screen",
                    "event_type": "action_phase_decided",
                    "phase": "supervision",
                    "agent": "supervisor",
                    "message": "action_phase_decided resolved continue for intent stock_screen",
                    "payload": {
                        "control_domain": "action",
                        "control_stage": "action_phase",
                        "decision_stage": "action_phase",
                        "final_action_node_id": "screen_review",
                        "decision_type": "continue",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-10T10:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "action_phase_decided")
        self.assertEqual(payload["dynamic_trace"][0]["final_action_node_id"], "screen_review")

    def test_trace_rebuilds_subagent_phase_shadow_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACESUBSHADOW"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_check", "user_query": "check bank resilience"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-subshadow-1",
                    "request_id": "req-subshadow",
                    "run_id": "01TRACESUBSHADOW",
                    "intent": "stock_check",
                    "event_type": "subagent_phase_shadow_suggested",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "message": "Shadow subagent-phase policy evaluated DAG phase_two",
                    "payload": {
                        "dag_id": "phase_two",
                        "selected_node_ids": ["retrieval_decider"],
                        "request_replan": True,
                        "actual_final_node_ids": ["retrieval_decider"],
                    },
                    "artifacts": [],
                    "created_at": "2026-07-08T12:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_phase_shadow_suggested")
        self.assertEqual(payload["dynamic_trace"][0]["dag_id"], "phase_two")
        self.assertEqual(payload["dynamic_trace"][0]["selected_node_ids"], ["retrieval_decider"])

    def test_trace_rebuilds_subagent_phase_execution_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACESUBEXEC"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "review_thesis", "user_query": "review this thesis"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-subexec-1",
                    "request_id": "req-subexec",
                    "run_id": "01TRACESUBEXEC",
                    "intent": "review_thesis",
                    "event_type": "subagent_phase_execution_decided",
                    "phase": "review",
                    "agent": "reviewer",
                    "message": "subagent_phase_execution_decided resolved keep_current for DAG review_execution",
                    "payload": {
                        "control_stage": "phase_patch_cycle",
                        "decision_stage": "phase_execution",
                        "dag_id": "review_execution",
                        "decision_type": "keep_current",
                        "final_plan_variant": "current",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-10T10:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_phase_execution_decided")
        self.assertEqual(payload["dynamic_trace"][0]["dag_id"], "review_execution")

    def test_trace_rebuilds_subagent_capability_gap_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACECAPGAP"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "review_thesis", "user_query": "review this thesis"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-gap-1",
                    "request_id": "req-gap",
                    "run_id": "01TRACECAPGAP",
                    "intent": "review_thesis",
                    "event_type": "subagent_capability_gap_detected",
                    "phase": "review",
                    "agent": "reviewer",
                    "message": "Detected capability gap for peer_review in stage review",
                    "payload": {
                        "stage": "review",
                        "intent": "review_thesis",
                        "capability": "peer_review",
                        "reason": "no catalog entry for stage='review' intent='review_thesis' capability='peer_review'",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-08T15:00:00+00:00",
                },
            )
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-gap-2",
                    "request_id": "req-gap",
                    "run_id": "01TRACECAPGAP",
                    "intent": "review_thesis",
                    "event_type": "subagent_tool_discovery_requested",
                    "phase": "review",
                    "agent": "reviewer",
                    "message": "Requested tool discovery for capability peer_review",
                    "payload": {
                        "stage": "review",
                        "intent": "review_thesis",
                        "capability": "peer_review",
                        "search_query": "review_thesis review peer_review",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-08T15:00:01+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 2)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_capability_gap_detected")
        self.assertEqual(payload["dynamic_trace"][0]["capability"], "peer_review")
        self.assertEqual(payload["dynamic_trace"][1]["event"], "subagent_tool_discovery_requested")
        self.assertEqual(payload["dynamic_trace"][1]["search_query"], "review_thesis review peer_review")

    def test_trace_rebuilds_subagent_catalog_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACECATALOG"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_check", "user_query": "check this stock"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-catalog-1",
                    "request_id": "req-catalog",
                    "run_id": "01TRACECATALOG",
                    "intent": "stock_check",
                    "event_type": "subagent_catalog_selection_decided",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "subagent_catalog_selection_decided resolved escalate_capability_gap for capability market_signal_extension",
                    "payload": {
                        "decision_stage": "catalog_selection",
                        "stage": "collect",
                        "intent": "stock_check",
                        "capability": "market_signal_extension",
                        "decision_type": "escalate_capability_gap",
                        "selected_subagent_type": None,
                        "selection_source": None,
                    },
                    "artifacts": [],
                    "created_at": "2026-07-09T15:30:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(
            payload["dynamic_trace"][0]["event"],
            "subagent_catalog_selection_decided",
        )
        self.assertEqual(
            payload["dynamic_trace"][0]["decision_type"],
            "escalate_capability_gap",
        )

    def test_trace_rebuilds_subagent_discovery_shadow_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACEDISCOVERYSHADOW"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "review_thesis", "user_query": "review this thesis"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-discovery-shadow-1",
                    "request_id": "req-discovery-shadow",
                    "run_id": "01TRACEDISCOVERYSHADOW",
                    "intent": "review_thesis",
                    "event_type": "subagent_discovery_shadow_suggested",
                    "phase": "review",
                    "agent": "reviewer",
                    "message": "Shadow discovery policy evaluated capability peer_review",
                    "payload": {
                        "stage": "review",
                        "capability": "peer_review",
                        "selected_protocols": ["skill", "mcp", "openapi"],
                        "actual_protocols": ["skill", "mcp", "openapi"],
                    },
                    "artifacts": [],
                    "created_at": "2026-07-09T10:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_discovery_shadow_suggested")
        self.assertEqual(payload["dynamic_trace"][0]["selected_protocols"], ["skill", "mcp", "openapi"])

    def test_trace_rebuilds_subagent_discovery_llm_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACEDISCOVERYLLM"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "review_thesis", "user_query": "review this thesis"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-discovery-llm-1",
                    "request_id": "req-discovery-llm",
                    "run_id": "01TRACEDISCOVERYLLM",
                    "intent": "review_thesis",
                    "event_type": "subagent_discovery_llm_applied",
                    "phase": "review",
                    "agent": "reviewer",
                    "message": "subagent_discovery_llm_applied selected protocols for capability peer_review",
                    "payload": {
                        "stage": "review",
                        "capability": "peer_review",
                        "applied_protocols": ["mcp", "skill", "openapi"],
                        "override_rule_protocols": True,
                    },
                    "artifacts": [],
                    "created_at": "2026-07-09T10:30:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_discovery_llm_applied")

    def test_trace_rebuilds_subagent_discovery_candidate_llm_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACEDISCOVERYCANDIDATELLM"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_check", "user_query": "check this stock"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-discovery-candidate-llm-1",
                    "request_id": "req-discovery-candidate-llm",
                    "run_id": "01TRACEDISCOVERYCANDIDATELLM",
                    "intent": "stock_check",
                    "event_type": "subagent_discovery_candidate_llm_applied",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "subagent_discovery_candidate_llm_applied ranked discovery candidates for capability market_signal_extension",
                    "payload": {
                        "stage": "collect",
                        "capability": "market_signal_extension",
                        "applied_candidate_subagent_type": "candidate_collect_market_snapshot_skill_b",
                        "override_rule_candidate": True,
                    },
                    "artifacts": [],
                    "created_at": "2026-07-09T11:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_discovery_candidate_llm_applied")
        self.assertEqual(
            payload["dynamic_trace"][0]["applied_candidate_subagent_type"],
            "candidate_collect_market_snapshot_skill_b",
        )

    def test_trace_rebuilds_subagent_discovery_hybrid_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACEDISCOVERYHYBRID"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "review_thesis", "user_query": "review this thesis"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-discovery-hybrid-1",
                    "request_id": "req-discovery-hybrid",
                    "run_id": "01TRACEDISCOVERYHYBRID",
                    "intent": "review_thesis",
                    "event_type": "subagent_discovery_hybrid_applied",
                    "phase": "review",
                    "agent": "reviewer",
                    "message": "subagent_discovery_hybrid_applied selected protocols for capability peer_review",
                    "payload": {
                        "stage": "review",
                        "capability": "peer_review",
                        "applied_protocols": ["mcp", "skill", "openapi"],
                        "override_rule_protocols": True,
                    },
                    "artifacts": [],
                    "created_at": "2026-07-09T11:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_discovery_hybrid_applied")
        self.assertEqual(payload["dynamic_trace"][0]["applied_protocols"], ["mcp", "skill", "openapi"])

    def test_trace_rebuilds_subagent_phase_hybrid_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACESUBHYBRID"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_screen", "user_query": "screen growth stocks"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-subhybrid-1",
                    "request_id": "req-subhybrid",
                    "run_id": "01TRACESUBHYBRID",
                    "intent": "stock_screen",
                    "event_type": "subagent_phase_hybrid_applied",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "message": "Hybrid subagent-phase policy applied variant current for DAG phase_one",
                    "payload": {
                        "dag_id": "phase_one",
                        "applied_plan_variant": "current",
                        "override_rule_variant": True,
                    },
                    "artifacts": [],
                    "created_at": "2026-07-08T14:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_phase_hybrid_applied")
        self.assertEqual(payload["dynamic_trace"][0]["applied_plan_variant"], "current")

    def test_trace_rebuilds_subagent_phase_llm_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACESUBLLM"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_screen", "user_query": "screen growth stocks"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-subllm-1",
                    "request_id": "req-subllm",
                    "run_id": "01TRACESUBLLM",
                    "intent": "stock_screen",
                    "event_type": "subagent_phase_llm_applied",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "message": "subagent_phase_llm_applied selected variant fallback_retrieval_decider for DAG phase_two",
                    "payload": {
                        "dag_id": "phase_two",
                        "applied_plan_variant": "fallback_retrieval_decider",
                        "override_rule_variant": True,
                    },
                    "artifacts": [],
                    "created_at": "2026-07-08T14:30:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_phase_llm_applied")
        self.assertEqual(payload["dynamic_trace"][0]["applied_plan_variant"], "fallback_retrieval_decider")

    def test_trace_rebuilds_action_phase_hybrid_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACEHYBRID"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "methodology_evolution", "user_query": "decide next phase"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-hybrid-1",
                    "request_id": "req-hybrid",
                    "run_id": "01TRACEHYBRID",
                    "intent": "methodology_evolution",
                    "event_type": "action_phase_hybrid_applied",
                    "phase": "supervision",
                    "agent": "supervisor",
                    "message": "Hybrid action-phase policy applied a model suggestion",
                    "payload": {
                        "rule_action_node_id": "retrieve",
                        "final_action_node_id": "review",
                        "override_action": True,
                        "request_replan": True,
                    },
                    "artifacts": [],
                    "created_at": "2026-07-08T13:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "action_phase_hybrid_applied")
        self.assertEqual(payload["dynamic_trace"][0]["final_action_node_id"], "review")

    def test_trace_rebuilds_action_phase_llm_rows_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACELLM"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_screen", "user_query": "screen growth stocks"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-llm-1",
                    "request_id": "req-llm",
                    "run_id": "01TRACELLM",
                    "intent": "stock_screen",
                    "event_type": "action_phase_llm_applied",
                    "phase": "supervision",
                    "agent": "supervisor",
                    "message": "action_phase_llm_applied applied a model suggestion",
                    "payload": {
                        "rule_action_node_id": "screen_review",
                        "final_action_node_id": "retrieve",
                        "override_action": True,
                        "request_replan": False,
                    },
                    "artifacts": [],
                    "created_at": "2026-07-08T15:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "action_phase_llm_applied")
        self.assertEqual(payload["dynamic_trace"][0]["final_action_node_id"], "retrieve")

    def test_trace_rebuilds_action_plan_patches_from_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACEPATCH"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_screen", "user_query": "screen growth stocks"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-patch-1",
                    "request_id": "req-patch",
                    "run_id": "01TRACEPATCH",
                    "intent": "stock_screen",
                    "event_type": "action_plan_patch_applied",
                    "phase": "supervisor",
                    "agent": "supervisor",
                    "message": "Applied action plan patch: patch-screen-enrich",
                    "payload": {
                        "patch_id": "patch-screen-enrich",
                        "plan_id": "plan-screen",
                        "intent": "stock_screen",
                        "thread_id": "thread-root",
                        "turn_id": "turn-root",
                        "operations": [
                            {
                                "op_type": "rewire_dependency",
                                "target_node_id": "retrieve",
                                "new_depends_on": ["screen_enrich"],
                            }
                        ],
                        "notes": ["rewire_retrieve_to_screen_enrich"],
                        "policy_source": "rule",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-07T10:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(len(payload["action_plan_patches"]), 1)
        self.assertEqual(payload["action_plan_patches"][0]["patch_id"], "patch-screen-enrich")
        self.assertEqual(payload["action_plan_patches"][0]["thread_id"], "thread-root")
        self.assertEqual(payload["event_counts"]["action_plan_patches"], 1)

    def test_subagent_builds_call_scoped_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01SUBAGENT"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_compare", "user_query": "compare banks"})
            write_json(
                run_root / "result.json",
                {
                    "report": {
                        "debug": {
                            "subagent_trace": [
                                {
                                    "action_id": "act_collect",
                                    "node_id": "collect",
                                    "task_name": "collect:stock_snapshot_collector",
                                    "agent_path": "collector/stock_snapshot_collector",
                                    "agent_label": "Stock Snapshot Collector",
                                    "invocation_id": "01INVOCATION",
                                    "subagent_type": "stock_snapshot_collector",
                                    "status": "completed",
                                    "phase": "collect",
                                    "agent": "collector",
                                    "objective": "Collect stock-level verified snapshots.",
                                    "notes": ["symbol=600519"],
                                }
                            ]
                        }
                    }
                },
            )
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "items.jsonl",
                {
                    "item_id": "item-subagent",
                    "thread_id": "thread-root",
                    "turn_id": "turn-root",
                    "run_id": "01SUBAGENT",
                    "request_id": "req1",
                    "item_type": "subagent",
                    "status": "completed",
                    "agent": "collector",
                    "phase": "collect",
                    "title": "stock_snapshot_collector",
                    "objective": "Collect stock-level verified snapshots.",
                    "payload": {
                        "invocation_id": "01INVOCATION",
                        "llm_used": False,
                        "output_keys": ["snapshots", "sources"],
                    },
                    "created_at": "2026-07-02T10:00:00+00:00",
                    "updated_at": "2026-07-02T10:00:02+00:00",
                },
            )
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt1",
                    "request_id": "req1",
                    "run_id": "01SUBAGENT",
                    "intent": "stock_compare",
                    "event_type": "subagent_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector started",
                    "payload": {
                        "action_id": "act_collect",
                        "node_id": "collect",
                        "task_name": "collect:stock_snapshot_collector",
                        "agent_path": "collector/stock_snapshot_collector",
                        "agent_label": "Stock Snapshot Collector",
                        "subagent_type": "stock_snapshot_collector",
                        "invocation_id": "01INVOCATION",
                        "status": "running",
                        "objective": "Collect stock-level verified snapshots.",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-02T10:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).subagent("01INVOCATION")

        self.assertEqual(payload["run_id"], "01SUBAGENT")
        self.assertEqual(payload["invocation_id"], "01INVOCATION")
        self.assertEqual(payload["task_name"], "collect:stock_snapshot_collector")
        self.assertEqual(payload["agent_path"], "collector/stock_snapshot_collector")
        self.assertEqual(payload["subagent_type"], "stock_snapshot_collector")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["thread_id"], "thread-root")
        self.assertEqual(payload["turn_id"], "turn-root")
        self.assertEqual(payload["phase"], "collect")
        self.assertEqual(payload["agent"], "collector")
        self.assertEqual(payload["duration_ms"], 2000)
        self.assertFalse(payload["llm_used"])
        self.assertEqual(payload["output_keys"], ["snapshots", "sources"])
        self.assertEqual(len(payload["trace_rows"]), 1)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["event_counts"]["trace_rows"], 1)
        self.assertEqual(payload["event_counts"]["events"], 1)

    def test_steps_returns_plan_and_supervisor_active_step_view(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir) / "01STEPS"
            run_root.mkdir(parents=True)
            write_json(run_root / "request.json", {"request_id": "req-steps", "intent": "stock_compare", "user_query": "compare banks"})
            write_json(
                run_root / "task_plan.json",
                {
                    "plan_id": "01PLAN",
                    "intent": "stock_compare",
                    "strategy": "dynamic",
                    "entry_strategy": "llm",
                    "entry_reason": "dynamic compare flow",
                    "subagent_required": True,
                    "dispatch_trace": {"source": "route_table", "intent": "stock_compare"},
                    "notes": ["compare flow activated"],
                    "actions": [
                        {
                            "action_id": "act_collect",
                            "node_id": "collect",
                            "phase": "collect",
                            "agent": "collector",
                            "objective": "Collect comparison snapshots.",
                            "inputs": {"symbols": ["600036", "601166"]},
                            "status": "pending",
                            "tags": ["compare"],
                        },
                        {
                            "action_id": "act_retrieve",
                            "node_id": "retrieve",
                            "phase": "retrieve",
                            "agent": "retriever",
                            "objective": "Retrieve final evidence.",
                            "inputs": {},
                            "status": "pending",
                            "tags": [],
                        },
                    ],
                },
            )
            write_json(run_root / "summary.json", {"status": "running", "message": "Run in progress"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "request_id": "req-steps",
                    "run_id": "01STEPS",
                    "intent": "stock_compare",
                    "event_type": "supervisor_decision",
                    "phase": "collect",
                    "agent": "supervisor",
                    "message": "Supervisor evaluated next actions",
                    "payload": {
                        "completed_action_ids": ["act_collect"],
                        "next_actions": [
                            {
                                "action_id": "act_retrieve",
                                "node_id": "retrieve",
                                "phase": "retrieve",
                                "agent": "retriever",
                                "objective": "Retrieve final evidence.",
                            }
                        ],
                        "notes": ["advance to retrieval"],
                    },
                    "artifacts": [],
                    "created_at": "2026-07-02T11:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).steps()

        self.assertEqual(payload["run_id"], "01STEPS")
        self.assertEqual(payload["plan_id"], "01PLAN")
        self.assertEqual(payload["strategy"], "dynamic")
        self.assertEqual(payload["entry_strategy"], "llm")
        self.assertEqual(payload["dispatch_trace"]["source"], "route_table")
        self.assertEqual(payload["step_counts"]["total"], 2)
        self.assertEqual(payload["step_counts"]["completed"], 1)
        self.assertEqual(payload["step_counts"]["active"], 1)
        self.assertEqual(payload["status_counts"]["completed"], 1)
        self.assertEqual(payload["status_counts"]["active"], 1)
        self.assertEqual(payload["steps"][0]["node_id"], "collect")
        self.assertEqual(payload["steps"][0]["status"], "completed")
        self.assertEqual(payload["steps"][1]["node_id"], "retrieve")
        self.assertEqual(payload["steps"][1]["status"], "active")
        self.assertTrue(payload["steps"][1]["active_from_supervisor"])
        self.assertEqual(payload["steps"][1]["supervisor_note"], "advance to retrieval")

    def test_thread_states_include_root_thread_and_turn(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir) / "01THREADS"
            run_root.mkdir(parents=True)
            write_json(
                run_root / "thread_state.json",
                {
                    "thread_id": "thread-root",
                    "run_id": "01THREADS",
                    "request_id": "req-root",
                    "intent": "stock_compare",
                    "status": "completed",
                    "parent_thread_id": None,
                    "active_turn_id": "turn-root",
                    "total_turns": 1,
                },
            )
            write_json(
                run_root / "turn_state.json",
                {
                    "turn_id": "turn-root",
                    "thread_id": "thread-root",
                    "run_id": "01THREADS",
                    "request_id": "req-root",
                    "turn_index": 1,
                    "status": "completed",
                    "intent": "stock_compare",
                    "objective": "compare banks",
                },
            )

            history = SessionHistory(run_root)
            rows = history.thread_states()
            tree = history.thread_tree()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["thread_id"], "thread-root")
        self.assertEqual(rows[0]["turns"][0]["turn_id"], "turn-root")
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["thread_id"], "thread-root")
        self.assertEqual(tree[0]["children"], [])

    def test_metrics_expose_llm_qdrant_memory_and_turn_usage(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir) / "01METRICS"
            run_root.mkdir(parents=True)
            write_json(run_root / "request.json", {"request_id": "req-metrics", "intent": "stock_check", "user_query": "check bank"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            write_json(
                run_root / "thread_state.json",
                {
                    "thread_id": "thread-root",
                    "run_id": "01METRICS",
                    "request_id": "req-metrics",
                    "intent": "stock_check",
                    "status": "completed",
                    "parent_thread_id": None,
                    "active_turn_id": "turn-root",
                    "total_turns": 1,
                },
            )
            write_json(
                run_root / "turn_state.json",
                {
                    "turn_id": "turn-root",
                    "thread_id": "thread-root",
                    "run_id": "01METRICS",
                    "request_id": "req-metrics",
                    "turn_index": 1,
                    "status": "completed",
                    "intent": "stock_check",
                    "objective": "check bank",
                },
            )
            append_jsonl(
                run_root / "items.jsonl",
                {
                    "item_id": "item-1",
                    "thread_id": "thread-root",
                    "turn_id": "turn-root",
                    "run_id": "01METRICS",
                    "request_id": "req-metrics",
                    "item_type": "subagent",
                    "status": "completed",
                    "agent": "retriever",
                    "phase": "retrieve",
                    "title": "retrieval_decider",
                    "objective": "Select final evidence.",
                    "payload": {"llm_used": True},
                    "created_at": "2026-07-07T10:00:00+00:00",
                    "updated_at": "2026-07-07T10:00:01+00:00",
                },
            )
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-qdrant",
                    "request_id": "req-metrics",
                    "run_id": "01METRICS",
                    "intent": "stock_check",
                    "event_type": "subagent_completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "message": "qdrant completed",
                    "payload": {
                        "thread_id": "thread-root",
                        "turn_id": "turn-root",
                        "subagent_type": "qdrant_evidence_searcher",
                        "status": "completed",
                        "notes": ["hit_count=2"],
                    },
                    "artifacts": [],
                    "created_at": "2026-07-07T10:00:02+00:00",
                },
            )
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-memory",
                    "request_id": "req-metrics",
                    "run_id": "01METRICS",
                    "intent": "stock_check",
                    "event_type": "subagent_completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "message": "memory completed",
                    "payload": {
                        "thread_id": "thread-root",
                        "turn_id": "turn-root",
                        "subagent_type": "methodology_memory_recaller",
                        "status": "completed",
                        "notes": ["row_count=1"],
                    },
                    "artifacts": [],
                    "created_at": "2026-07-07T10:00:03+00:00",
                },
            )

            payload = SessionHistory(run_root).metrics()

        self.assertEqual(payload["counts"]["llm_calls"], 1)
        self.assertEqual(payload["rates"]["qdrant_hit_rate"], 1.0)
        self.assertEqual(payload["rates"]["memory_hit_rate"], 1.0)
        self.assertEqual(payload["turn_usage"]["turn-root"]["llm_calls"], 1)
        self.assertEqual(payload["turn_usage"]["turn-root"]["subagents"], 1)
        self.assertEqual(payload["turn_usage"]["turn-root"]["subagent_latency_ms"], 1000)
        self.assertEqual(payload["turn_usage"]["turn-root"]["degraded_subagents"], 0)
        self.assertEqual(payload["turn_usage"]["turn-root"]["failed_subagents"], 0)
        self.assertEqual(payload["turn_usage"]["turn-root"]["degrade_reason_counts"], {})

    def test_thread_payload_filters_child_thread_events_and_items(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir) / "01THREADPAYLOAD"
            run_root.mkdir(parents=True)
            threads_root = run_root / "threads"
            threads_root.mkdir(parents=True)
            write_json(run_root / "request.json", {"request_id": "req-thread", "intent": "stock_check", "user_query": "child replay"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            write_json(
                run_root / "thread_state.json",
                {
                    "thread_id": "thread-root",
                    "run_id": "01THREADPAYLOAD",
                    "request_id": "req-thread",
                    "intent": "stock_check",
                    "status": "completed",
                    "parent_thread_id": None,
                    "active_turn_id": "turn-root",
                    "total_turns": 1,
                },
            )
            write_json(
                run_root / "turn_state.json",
                {
                    "turn_id": "turn-root",
                    "thread_id": "thread-root",
                    "run_id": "01THREADPAYLOAD",
                    "request_id": "req-thread",
                    "turn_index": 1,
                    "status": "completed",
                    "intent": "stock_check",
                    "objective": "root",
                },
            )
            write_json(
                threads_root / "thread-child.thread.json",
                {
                    "thread_id": "thread-child",
                    "run_id": "01THREADPAYLOAD",
                    "request_id": "req-thread",
                    "intent": "stock_check",
                    "status": "completed",
                    "parent_thread_id": "thread-root",
                    "active_turn_id": "turn-child",
                    "total_turns": 1,
                },
            )
            write_json(
                threads_root / "thread-child.turn.turn-child.json",
                {
                    "turn_id": "turn-child",
                    "thread_id": "thread-child",
                    "run_id": "01THREADPAYLOAD",
                    "request_id": "req-thread",
                    "turn_index": 1,
                    "status": "completed",
                    "intent": "stock_check",
                    "objective": "child",
                },
            )
            append_jsonl(
                run_root / "items.jsonl",
                {
                    "item_id": "child-item",
                    "thread_id": "thread-child",
                    "turn_id": "turn-child",
                    "run_id": "01THREADPAYLOAD",
                    "request_id": "req-thread",
                    "item_type": "subagent",
                    "status": "completed",
                    "agent": "collector",
                    "phase": "collect_child",
                    "title": "stock_snapshot_collector",
                    "objective": "Collect child snapshot",
                    "payload": {},
                    "created_at": "2026-07-07T10:00:00+00:00",
                    "updated_at": "2026-07-07T10:00:01+00:00",
                },
            )
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-thread",
                    "request_id": "req-thread",
                    "run_id": "01THREADPAYLOAD",
                    "intent": "stock_check",
                    "event_type": "thread_completed",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "child_thread:thread-child completed",
                    "payload": {
                        "thread_id": "thread-root",
                        "turn_id": "turn-root",
                        "status": "completed",
                        "payload": {"child_thread_id": "thread-child"},
                    },
                    "artifacts": [],
                    "created_at": "2026-07-07T10:00:02+00:00",
                },
            )
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-subagent",
                    "request_id": "req-thread",
                    "run_id": "01THREADPAYLOAD",
                    "intent": "stock_check",
                    "event_type": "subagent_completed",
                    "phase": "collect_child",
                    "agent": "collector",
                    "message": "child subagent completed",
                    "payload": {
                        "thread_id": "thread-child",
                        "turn_id": "turn-child",
                        "subagent_type": "stock_snapshot_collector",
                        "status": "completed",
                        "objective": "Collect child snapshot",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-07T10:00:03+00:00",
                },
            )

            payload = SessionHistory(run_root).thread("thread-child")

        self.assertEqual(payload["thread"]["thread_id"], "thread-child")
        self.assertEqual(payload["event_counts"]["items"], 1)
        self.assertEqual(payload["items"][0]["thread_id"], "thread-child")
        self.assertEqual(len(payload["events"]), 2)
        self.assertEqual(payload["subagent_trace"][-1]["thread_id"], "thread-child")
        self.assertEqual(payload["metrics"]["counts"]["threads"], 1)
        self.assertEqual(payload["metrics"]["counts"]["subagents"], 1)
        self.assertEqual(payload["event_counts"]["dynamic_trace"], 0)

    def test_thread_replay_includes_descendants_blackboard_and_scoped_metrics(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir) / "01THREADREPLAY"
            run_root.mkdir(parents=True)
            threads_root = run_root / "threads"
            threads_root.mkdir(parents=True)
            write_json(run_root / "request.json", {"request_id": "req-thread-replay", "intent": "stock_compare", "user_query": "thread replay"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            write_json(
                run_root / "thread_state.json",
                {
                    "thread_id": "thread-root",
                    "run_id": "01THREADREPLAY",
                    "request_id": "req-thread-replay",
                    "intent": "stock_compare",
                    "status": "completed",
                    "parent_thread_id": None,
                    "active_turn_id": "turn-root",
                    "total_turns": 1,
                },
            )
            write_json(
                run_root / "turn_state.json",
                {
                    "turn_id": "turn-root",
                    "thread_id": "thread-root",
                    "run_id": "01THREADREPLAY",
                    "request_id": "req-thread-replay",
                    "turn_index": 1,
                    "status": "completed",
                    "intent": "stock_compare",
                    "objective": "root",
                },
            )
            write_json(
                threads_root / "thread-child.thread.json",
                {
                    "thread_id": "thread-child",
                    "run_id": "01THREADREPLAY",
                    "request_id": "req-thread-replay",
                    "intent": "stock_compare",
                    "status": "completed",
                    "parent_thread_id": "thread-root",
                    "active_turn_id": "turn-child",
                    "total_turns": 1,
                },
            )
            write_json(
                threads_root / "thread-child.turn.turn-child.json",
                {
                    "turn_id": "turn-child",
                    "thread_id": "thread-child",
                    "run_id": "01THREADREPLAY",
                    "request_id": "req-thread-replay",
                    "turn_index": 1,
                    "status": "completed",
                    "intent": "stock_compare",
                    "objective": "child",
                },
            )
            write_json(
                threads_root / "thread-grandchild.thread.json",
                {
                    "thread_id": "thread-grandchild",
                    "run_id": "01THREADREPLAY",
                    "request_id": "req-thread-replay",
                    "intent": "stock_compare",
                    "status": "completed",
                    "parent_thread_id": "thread-child",
                    "active_turn_id": "turn-grandchild",
                    "total_turns": 1,
                },
            )
            write_json(
                threads_root / "thread-grandchild.turn.turn-grandchild.json",
                {
                    "turn_id": "turn-grandchild",
                    "thread_id": "thread-grandchild",
                    "run_id": "01THREADREPLAY",
                    "request_id": "req-thread-replay",
                    "turn_index": 1,
                    "status": "completed",
                    "intent": "stock_compare",
                    "objective": "grandchild",
                },
            )
            append_jsonl(
                run_root / "items.jsonl",
                {
                    "item_id": "child-item",
                    "thread_id": "thread-child",
                    "turn_id": "turn-child",
                    "run_id": "01THREADREPLAY",
                    "request_id": "req-thread-replay",
                    "item_type": "subagent",
                    "status": "completed",
                    "agent": "collector",
                    "phase": "collect_child",
                    "title": "stock_snapshot_collector",
                    "objective": "Collect child snapshot",
                    "payload": {"llm_used": False},
                    "created_at": "2026-07-07T10:00:00+00:00",
                    "updated_at": "2026-07-07T10:00:01+00:00",
                },
            )
            append_jsonl(
                run_root / "items.jsonl",
                {
                    "item_id": "grandchild-item",
                    "thread_id": "thread-grandchild",
                    "turn_id": "turn-grandchild",
                    "run_id": "01THREADREPLAY",
                    "request_id": "req-thread-replay",
                    "item_type": "subagent",
                    "status": "degraded",
                    "agent": "retriever",
                    "phase": "retrieve_child",
                    "title": "qdrant_evidence_searcher",
                    "objective": "Search child evidence",
                    "payload": {"llm_used": True},
                    "degrade_reason": "runtime_fallback",
                    "created_at": "2026-07-07T10:00:02+00:00",
                    "updated_at": "2026-07-07T10:00:04+00:00",
                },
            )
            append_jsonl(
                run_root / "blackboard.jsonl",
                {
                    "entry_id": "entry-child",
                    "run_id": "01THREADREPLAY",
                    "thread_id": "thread-child",
                    "request_id": "req-thread-replay",
                    "kind": "verified_snapshot",
                    "agent": "collector",
                    "phase": "collect_child",
                    "status": "active",
                    "payload": {"symbol": "600036"},
                    "source_refs": [],
                    "created_at": "2026-07-07T10:00:00+00:00",
                },
            )
            append_jsonl(
                run_root / "blackboard.jsonl",
                {
                    "entry_id": "entry-grandchild",
                    "run_id": "01THREADREPLAY",
                    "thread_id": "thread-grandchild",
                    "request_id": "req-thread-replay",
                    "kind": "retrieved_evidence",
                    "agent": "retriever",
                    "phase": "retrieve_child",
                    "status": "active",
                    "payload": {"summary": "hit"},
                    "source_refs": [],
                    "created_at": "2026-07-07T10:00:02+00:00",
                },
            )
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-thread-start",
                    "request_id": "req-thread-replay",
                    "run_id": "01THREADREPLAY",
                    "intent": "stock_compare",
                    "event_type": "thread_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "child thread started",
                    "payload": {
                        "thread_id": "thread-root",
                        "turn_id": "turn-root",
                        "payload": {"child_thread_id": "thread-child"},
                    },
                    "artifacts": [],
                    "created_at": "2026-07-07T10:00:00+00:00",
                },
            )
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-grandchild-subagent",
                    "request_id": "req-thread-replay",
                    "run_id": "01THREADREPLAY",
                    "intent": "stock_compare",
                    "event_type": "subagent_completed",
                    "phase": "retrieve_child",
                    "agent": "retriever",
                    "message": "grandchild retrieval completed",
                    "payload": {
                        "thread_id": "thread-grandchild",
                        "turn_id": "turn-grandchild",
                        "subagent_type": "qdrant_evidence_searcher",
                        "status": "completed",
                        "notes": ["hit_count=1"],
                    },
                    "artifacts": [],
                    "created_at": "2026-07-07T10:00:04+00:00",
                },
            )
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt-grandchild-dag",
                    "request_id": "req-thread-replay",
                    "run_id": "01THREADREPLAY",
                    "intent": "stock_compare",
                    "event_type": "subagent_dag_node_completed",
                    "phase": "retrieve_child",
                    "agent": "retriever",
                    "message": "Completed DAG node: qdrant_evidence_searcher",
                    "payload": {
                        "thread_id": "thread-grandchild",
                        "turn_id": "turn-grandchild",
                        "task_name": "retrieve:qdrant_evidence_searcher",
                        "agent_path": "retriever/qdrant_evidence_searcher",
                        "subagent_type": "qdrant_evidence_searcher",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-07T10:00:05+00:00",
                },
            )

            payload = SessionHistory(run_root).thread_replay("thread-child")
            metrics = SessionHistory(run_root).thread_metrics("thread-child")

        self.assertEqual(payload["thread"]["thread_id"], "thread-child")
        self.assertEqual(payload["scope"], "thread_subtree")
        self.assertEqual(payload["scope_thread_ids"], ["thread-child", "thread-grandchild"])
        self.assertEqual(payload["event_counts"]["threads"], 2)
        self.assertEqual(payload["event_counts"]["blackboard"], 2)
        self.assertEqual(payload["dynamic_trace"][0]["event"], "subagent_dag_node_completed")
        self.assertEqual(payload["thread_tree"][0]["thread_id"], "thread-child")
        self.assertEqual(payload["thread_tree"][0]["children"][0]["thread_id"], "thread-grandchild")
        self.assertEqual(metrics["counts"]["threads"], 2)
        self.assertEqual(metrics["counts"]["subagents"], 2)
        self.assertEqual(metrics["counts"]["llm_calls"], 1)
        self.assertEqual(metrics["rates"]["qdrant_hit_rate"], 1.0)
        self.assertEqual(metrics["fanout"]["max_depth"], 1)
        self.assertEqual(metrics["degrade_reason_counts"]["runtime_fallback"], 1)


if __name__ == "__main__":
    unittest.main()
