from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from fastapi.testclient import TestClient

from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.api.agent_os_run_service import AgentOSRunService
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.jsonl import read_json, read_jsonl, write_json
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.execution_runtime import (
    ExecutionRunResult,
    ExecutionRunSnapshot,
)
from research_system.schemas.protocol import (
    ProgressState,
    RequestEnvelope,
    ResponseEnvelope,
)


class AgentOSApiTests(unittest.TestCase):
    def test_idempotent_response_reconciles_canonical_state_files(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            request = RequestEnvelope(
                intent="stock_check",
                user_query="分析 600000",
                idempotency_key="reconcile-response-state",
            )
            run_id, paths, _ = agent_os.run_store.create_run(request, "stock_check")
            response = ResponseEnvelope(
                request_id=request.request_id,
                run_id=run_id,
                thread_id="thread-reconcile",
                turn_id="turn-reconcile",
                mode="graph",
                intent="stock_check",
                status="completed",
                message="研究报告已完成",
                progress=ProgressState(
                    current_phase="terminal",
                    status_message="研究报告已完成",
                    percent=100,
                ),
                artifact_ids=["artifact-1"],
                final_artifact_ids=["artifact-1"],
            )
            write_json(paths.root / "response.json", response.model_dump(mode="json"))
            write_json(
                paths.root / "run_state.json",
                {"run_id": run_id, "status": "received"},
            )
            (paths.root / "summary.json").unlink(missing_ok=True)

            replay = agent_os.handle_request(request)
            run_state = read_json(paths.root / "run_state.json")
            summary = read_json(paths.root / "summary.json")

            self.assertEqual(replay.status, "completed")
            self.assertEqual(run_state["status"], "completed")
            self.assertEqual(run_state["active_turn_id"], "turn-reconcile")
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["message"], "研究报告已完成")
            self.assertEqual(summary["artifact_ids"], ["artifact-1"])

    def test_graph_delivery_uses_report_fallback_when_synthesis_is_unavailable(
        self,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            agent_os.main_agent.llm_provider = None
            request = RequestEnvelope(
                intent="stock_check",
                user_query="分析 600000",
            )
            run_id, paths, _ = agent_os.run_store.create_run(request, "stock_check")
            graph = ExecutionGraph(
                domain_id="finance_ashare",
                objective=request.user_query,
                metadata={"intent": "stock_check"},
            )
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                graph=graph,
                status="completed",
            )
            runner = agent_os._runner(run_id)
            runner.board.add(
                ArtifactRecord(
                    artifact_type="research_report",
                    run_id=run_id,
                    thread_id="thread-1",
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="uzi_report_composer",
                        capability="finance.uzi.native.report.compose",
                        node_id="report",
                        domain_id="finance_ashare",
                    ),
                    content={"conclusion": "Pack 原始摘要", "evidence": []},
                    source_refs=[{"type": "test", "id": "source-1"}],
                    confidence=0.5,
                )
            )

            result = agent_os._ensure_graph_assistant_message(ExecutionRunResult(snapshot=snapshot))
            assistant = next(artifact for artifact in runner.board.list_artifacts(run_id=run_id, status="active") if artifact.artifact_type == "assistant_message")
            synthesis = next(artifact for artifact in runner.board.list_artifacts(run_id=run_id, status="active") if artifact.artifact_type == "main_agent_synthesis")
            events = read_jsonl(paths.root / "execution_events.jsonl")

            self.assertIn(assistant.artifact_id, result.artifact_ids)
            self.assertEqual(result.snapshot.status, "completed")
            self.assertFalse(synthesis.content["model_generated"])
            self.assertEqual(
                synthesis.content["fallback_reason"],
                "model_error:RuntimeError",
            )
            self.assertEqual(synthesis.parent_artifact_ids, [synthesis.content["source_artifact_id"]])
            self.assertIn("deterministic_delivery_fallback", synthesis.risk_flags)
            self.assertEqual(assistant.content["delivery_status"], "completed")
            self.assertFalse(assistant.content["synthesis_unavailable"])
            self.assertTrue(any(event["event_type"] == "assistant_message_ready" for event in events))
            self.assertTrue(any(event["event_type"] == "main_agent_synthesis_failed" for event in events))
            self.assertTrue(any(event["event_type"] == "main_agent_synthesis_fallback" for event in events))
            fallback_event = next(event for event in events if event["event_type"] == "main_agent_synthesis_fallback")
            self.assertEqual(fallback_event["artifact_ids"], [synthesis.artifact_id])
            self.assertFalse(any(event["event_type"] == "graph_run_partial" for event in events))

            agent_os._write_summary(result, intent="stock_check")
            self.assertEqual(read_json(paths.root / "run_state.json")["status"], "completed")
            self.assertEqual(read_json(paths.root / "summary.json")["status"], "completed")

    def test_graph_delivery_supersedes_pack_message_with_report_fallback(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            agent_os.main_agent.llm_provider = None
            request = RequestEnvelope(intent="stock_check", user_query="分析 600000")
            run_id, paths, _ = agent_os.run_store.create_run(request, "stock_check")
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-existing-message",
                request_id=request.request_id,
                graph=ExecutionGraph(
                    domain_id="finance_ashare",
                    objective=request.user_query,
                    metadata={"intent": "stock_check"},
                ),
                status="completed",
            )
            runner = agent_os._runner(run_id)
            report = runner.board.add(
                ArtifactRecord(
                    artifact_type="research_report",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="uzi_report_composer",
                        capability="finance.uzi.native.report.compose",
                        domain_id="finance_ashare",
                    ),
                    content={"conclusion": "Pack 原始摘要"},
                    source_refs=[{"type": "test", "id": "source-1"}],
                    confidence=0.7,
                )
            )
            pack_message = runner.board.add(
                ArtifactRecord(
                    artifact_type="assistant_message",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="pack_reporter",
                        capability="finance.message.compose",
                        domain_id="finance_ashare",
                    ),
                    content={
                        "message": "Pack 已完成",
                        "delivery_status": "completed",
                        "synthesis_unavailable": False,
                    },
                    source_refs=list(report.source_refs),
                    parent_artifact_ids=[report.artifact_id],
                    confidence=0.7,
                )
            )

            result = agent_os._ensure_graph_assistant_message(
                ExecutionRunResult(
                    snapshot=snapshot,
                    artifact_ids=[report.artifact_id, pack_message.artifact_id],
                )
            )
            active = runner.board.list_artifacts(run_id=run_id, status="active")
            assistant = next(row for row in active if row.artifact_type == "assistant_message")
            synthesis = next(row for row in active if row.artifact_type == "main_agent_synthesis")
            events = read_jsonl(paths.root / "execution_events.jsonl")
            write_json(
                paths.root / "execution_snapshot.json",
                result.snapshot.model_dump(mode="json"),
            )
            response = agent_os._response_from_graph(request, result)

            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(assistant.supersedes_artifact_id, pack_message.artifact_id)
            self.assertEqual(assistant.producer.executor, "main_agent")
            self.assertEqual(response.message, "Pack 原始摘要")
            self.assertFalse(synthesis.content["model_generated"])
            self.assertFalse(any(event["event_type"] == "graph_run_partial" for event in events))

    def test_graph_delivery_bounds_model_attempt_to_remaining_turn_budget(self) -> None:
        class FailingProvider:
            available = True

            def __init__(self) -> None:
                self.calls: list[dict] = []

            def complete(self, **kwargs):
                self.calls.append(dict(kwargs))
                raise TimeoutError("provider timed out")

        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                    "llm_request_timeout_seconds": 25,
                }
            )
            agent_os = AgentOS(settings)
            provider = FailingProvider()
            agent_os.main_agent.llm_provider = provider
            request = RequestEnvelope(intent="stock_check", user_query="分析 600000")
            run_id, paths, _ = agent_os.run_store.create_run(request, "stock_check")
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-budget",
                request_id=request.request_id,
                graph=ExecutionGraph(
                    domain_id="finance_ashare",
                    objective=request.user_query,
                    metadata={"intent": "stock_check"},
                ),
                status="completed",
            )
            runner = agent_os._runner(run_id)
            runner.board.add(
                ArtifactRecord(
                    artifact_type="research_report",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="uzi_report_composer",
                        capability="finance.uzi.native.report.compose",
                        domain_id="finance_ashare",
                    ),
                    content={"conclusion": "报告结论", "risk": ["数据存在缺口"]},
                    source_refs=[{"type": "test", "id": "source-1"}],
                    confidence=0.7,
                )
            )

            result = agent_os._ensure_graph_assistant_message(
                ExecutionRunResult(snapshot=snapshot),
                deadline_at=monotonic() + 10,
            )
            artifacts = runner.board.list_artifacts(run_id=run_id, status="active")
            synthesis = next(row for row in artifacts if row.artifact_type == "main_agent_synthesis")
            events = read_jsonl(paths.root / "execution_events.jsonl")

            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(provider.calls[0]["max_retries"], 0)
            self.assertEqual(provider.calls[0]["max_continuation_attempts"], 0)
            self.assertGreater(provider.calls[0]["timeout_seconds"], 5)
            self.assertLessEqual(provider.calls[0]["timeout_seconds"], 8)
            self.assertFalse(synthesis.content["model_generated"])
            self.assertEqual(synthesis.content["fallback_reason"], "model_error:TimeoutError")
            self.assertTrue(any(event["event_type"] == "main_agent_synthesis_failed" for event in events))

    def test_graph_delivery_recovers_synthesis_written_before_assistant(self) -> None:
        class UnexpectedProvider:
            available = True

            def complete(self, **_kwargs):
                raise AssertionError("recovery must reuse the durable synthesis")

        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            agent_os.main_agent.llm_provider = UnexpectedProvider()
            request = RequestEnvelope(intent="stock_check", user_query="分析 600000")
            run_id, paths, _ = agent_os.run_store.create_run(request, "stock_check")
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-recover-delivery",
                request_id=request.request_id,
                graph=ExecutionGraph(
                    domain_id="finance_ashare",
                    objective=request.user_query,
                    metadata={"intent": "stock_check"},
                ),
                status="completed",
            )
            runner = agent_os._runner(run_id)
            report = runner.board.add(
                ArtifactRecord(
                    artifact_type="research_report",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="uzi_report_composer",
                        capability="finance.uzi.native.report.compose",
                        domain_id="finance_ashare",
                    ),
                    content={"conclusion": "持久化报告"},
                    source_refs=[{"type": "test", "id": "source-1"}],
                    confidence=0.7,
                )
            )
            pack_message = runner.board.add(
                ArtifactRecord(
                    artifact_type="assistant_message",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="pack_reporter",
                        capability="finance.message.compose",
                        domain_id="finance_ashare",
                    ),
                    content={
                        "message": "旧 Pack 消息",
                        "delivery_status": "completed",
                    },
                    source_refs=list(report.source_refs),
                    parent_artifact_ids=[report.artifact_id],
                    confidence=0.7,
                )
            )
            durable_synthesis = runner.board.add(
                ArtifactRecord(
                    artifact_type="main_agent_synthesis",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="main_agent",
                        capability="core.research.synthesize",
                        domain_id="finance_ashare",
                    ),
                    content={
                        "conclusion": "持久化报告",
                        "assistant_message": "恢复后的 Main Agent 消息",
                        "follow_up": [],
                        "source_artifact_id": report.artifact_id,
                        "source_artifact_ids": [report.artifact_id],
                        "model_generated": False,
                        "fallback_reason": "report_backed_fallback",
                    },
                    source_refs=list(report.source_refs),
                    parent_artifact_ids=[report.artifact_id],
                    confidence=0.7,
                )
            )

            result = agent_os._ensure_graph_assistant_message(
                ExecutionRunResult(
                    snapshot=snapshot,
                    artifact_ids=[
                        report.artifact_id,
                        pack_message.artifact_id,
                        durable_synthesis.artifact_id,
                    ],
                )
            )
            active = runner.board.list_artifacts(run_id=run_id, status="active")
            syntheses = [row for row in active if row.artifact_type == "main_agent_synthesis"]
            assistant = next(row for row in active if row.artifact_type == "assistant_message")
            events = read_jsonl(paths.root / "execution_events.jsonl")

            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual([row.artifact_id for row in syntheses], [durable_synthesis.artifact_id])
            self.assertEqual(assistant.supersedes_artifact_id, pack_message.artifact_id)
            self.assertEqual(assistant.content["message"], "恢复后的 Main Agent 消息")
            self.assertIn(durable_synthesis.artifact_id, assistant.content["source_artifact_ids"])
            self.assertNotIn("main_agent_synthesis_started", {row["event_type"] for row in events})

    def test_graph_delivery_replaces_synthesis_from_superseded_report(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            agent_os.main_agent.llm_provider = None
            request = RequestEnvelope(intent="stock_check", user_query="重新分析 600000")
            run_id, _, _ = agent_os.run_store.create_run(request, "stock_check")
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-rerun-delivery",
                request_id=request.request_id,
                graph=ExecutionGraph(
                    domain_id="finance_ashare",
                    objective=request.user_query,
                    metadata={"intent": "stock_check"},
                ),
                status="completed",
            )
            runner = agent_os._runner(run_id)
            old_report = runner.board.add(
                ArtifactRecord(
                    artifact_type="research_report",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="uzi_report_composer",
                        capability="finance.uzi.native.report.compose",
                        domain_id="finance_ashare",
                    ),
                    content={"conclusion": "旧报告结论"},
                    confidence=0.6,
                )
            )
            old_synthesis = runner.board.add(
                ArtifactRecord(
                    artifact_type="main_agent_synthesis",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="main_agent",
                        capability="core.research.synthesize",
                        domain_id="finance_ashare",
                    ),
                    content={
                        "conclusion": "旧报告结论",
                        "assistant_message": "旧答复",
                        "follow_up": [],
                        "source_artifact_id": old_report.artifact_id,
                        "source_artifact_ids": [old_report.artifact_id],
                        "model_generated": False,
                    },
                    parent_artifact_ids=[old_report.artifact_id],
                    confidence=0.6,
                )
            )
            old_assistant = runner.board.add(
                ArtifactRecord(
                    artifact_type="assistant_message",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="main_agent",
                        capability="core.message.compose",
                        domain_id="finance_ashare",
                    ),
                    content={
                        "message": "旧答复",
                        "delivery_status": "completed",
                        "source_artifact_ids": [
                            old_report.artifact_id,
                            old_synthesis.artifact_id,
                        ],
                    },
                    parent_artifact_ids=[
                        old_report.artifact_id,
                        old_synthesis.artifact_id,
                    ],
                    confidence=0.6,
                )
            )
            new_report = runner.board.add(
                old_report.model_copy(
                    update={
                        "artifact_id": "report-rerun-v2",
                        "artifact_version": old_report.artifact_version + 1,
                        "supersedes_artifact_id": old_report.artifact_id,
                        "content": {"conclusion": "新报告结论"},
                    }
                )
            )

            result = agent_os._ensure_graph_assistant_message(
                ExecutionRunResult(
                    snapshot=snapshot,
                    artifact_ids=[
                        old_report.artifact_id,
                        old_synthesis.artifact_id,
                        old_assistant.artifact_id,
                        new_report.artifact_id,
                    ],
                )
            )
            active = runner.board.list_artifacts(run_id=run_id, status="active")
            synthesis = next(row for row in active if row.artifact_type == "main_agent_synthesis")
            assistant = next(row for row in active if row.artifact_type == "assistant_message")

            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(synthesis.supersedes_artifact_id, old_synthesis.artifact_id)
            self.assertEqual(synthesis.content["source_artifact_id"], new_report.artifact_id)
            self.assertEqual(assistant.supersedes_artifact_id, old_assistant.artifact_id)
            self.assertEqual(assistant.content["message"], "新报告结论")

            failed_result = agent_os._ensure_graph_assistant_message(
                ExecutionRunResult(
                    snapshot=result.snapshot.model_copy(update={"status": "failed"}),
                    artifact_ids=result.artifact_ids,
                )
            )
            failed_assistant = next(
                row
                for row in runner.board.list_artifacts(run_id=run_id, status="active")
                if row.artifact_type == "assistant_message"
            )
            self.assertEqual(failed_result.snapshot.status, "failed")
            self.assertEqual(failed_assistant.supersedes_artifact_id, assistant.artifact_id)
            self.assertEqual(failed_assistant.content["delivery_status"], "failed")
            self.assertNotEqual(failed_assistant.content["message"], "新报告结论")

    def test_graph_delivery_replaces_method_synthesis_after_rerun(self) -> None:
        class Provider:
            available = True

            def __init__(self) -> None:
                self.calls = 0

            def complete(self, **_kwargs):
                self.calls += 1
                return (
                    '{"conclusion":"新方法结论",'
                    '"assistant_message":"新方法答复","follow_up":[]}'
                )

        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            provider = Provider()
            agent_os.main_agent.llm_provider = provider
            request = RequestEnvelope(
                intent="sector_research",
                user_query="重新运行行业方法",
            )
            run_id, _, _ = agent_os.run_store.create_run(request, "sector_research")
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-method-rerun",
                request_id=request.request_id,
                graph=ExecutionGraph(
                    domain_id="finance_ashare",
                    objective=request.user_query,
                    metadata={"intent": "sector_research"},
                ),
                status="completed_partial",
            )
            runner = agent_os._runner(run_id)
            old_output = runner.board.add(
                ArtifactRecord(
                    artifact_type="method_output",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="method_executor",
                        capability="finance.method.run",
                        domain_id="finance_ashare",
                    ),
                    content={"conclusion": "旧方法结论"},
                    confidence=0.6,
                )
            )
            old_synthesis = runner.board.add(
                ArtifactRecord(
                    artifact_type="main_agent_synthesis",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="main_agent",
                        capability="core.research.synthesize",
                        domain_id="finance_ashare",
                    ),
                    content={
                        "conclusion": "旧方法结论",
                        "assistant_message": "旧方法答复",
                        "follow_up": [],
                        "source_artifact_id": old_output.artifact_id,
                        "source_artifact_ids": [old_output.artifact_id],
                        "model_generated": True,
                    },
                    parent_artifact_ids=[old_output.artifact_id],
                    confidence=0.6,
                )
            )
            old_assistant = runner.board.add(
                ArtifactRecord(
                    artifact_type="assistant_message",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="main_agent",
                        capability="core.message.compose",
                        domain_id="finance_ashare",
                    ),
                    content={
                        "message": "旧方法答复",
                        "delivery_status": "completed",
                        "source_artifact_ids": [old_synthesis.artifact_id],
                    },
                    parent_artifact_ids=[old_synthesis.artifact_id],
                    confidence=0.6,
                )
            )
            new_output = runner.board.add(
                old_output.model_copy(
                    update={
                        "artifact_id": "method-output-rerun-v2",
                        "artifact_version": old_output.artifact_version + 1,
                        "supersedes_artifact_id": old_output.artifact_id,
                        "content": {"conclusion": "新方法原始结论"},
                    }
                )
            )

            result = agent_os._ensure_graph_assistant_message(
                ExecutionRunResult(
                    snapshot=snapshot,
                    artifact_ids=[
                        old_output.artifact_id,
                        old_synthesis.artifact_id,
                        old_assistant.artifact_id,
                        new_output.artifact_id,
                    ],
                )
            )
            active = runner.board.list_artifacts(run_id=run_id, status="active")
            synthesis = next(row for row in active if row.artifact_type == "main_agent_synthesis")
            assistant = next(row for row in active if row.artifact_type == "assistant_message")

            self.assertEqual(provider.calls, 1)
            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(synthesis.supersedes_artifact_id, old_synthesis.artifact_id)
            self.assertEqual(synthesis.content["source_artifact_ids"], [new_output.artifact_id])
            self.assertEqual(assistant.supersedes_artifact_id, old_assistant.artifact_id)
            self.assertEqual(assistant.content["message"], "新方法答复")

    def test_graph_delivery_repairs_historical_partial_from_report(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            agent_os.main_agent.llm_provider = None
            request = RequestEnvelope(intent="stock_check", user_query="恢复 600000 报告")
            run_id, paths, _ = agent_os.run_store.create_run(request, "stock_check")
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-partial-recovery",
                request_id=request.request_id,
                graph=ExecutionGraph(
                    domain_id="finance_ashare",
                    objective=request.user_query,
                    metadata={"intent": "stock_check"},
                ),
                status="completed_partial",
            )
            runner = agent_os._runner(run_id)
            runner._persist(snapshot)
            report = runner.board.add(
                ArtifactRecord(
                    artifact_type="research_report",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="uzi_report_composer",
                        capability="finance.uzi.native.report.compose",
                        domain_id="finance_ashare",
                    ),
                    content={"conclusion": "可恢复报告结论"},
                    confidence=0.7,
                )
            )
            invalid_synthesis = runner.board.add(
                ArtifactRecord(
                    artifact_type="main_agent_synthesis",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="main_agent",
                        capability="core.research.synthesize",
                        domain_id="finance_ashare",
                    ),
                    content={
                        "source_artifact_id": report.artifact_id,
                        "source_artifact_ids": [report.artifact_id],
                        "model_generated": False,
                    },
                    parent_artifact_ids=[report.artifact_id],
                    confidence=0.7,
                )
            )
            partial_assistant = runner.board.add(
                ArtifactRecord(
                    artifact_type="assistant_message",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="main_agent",
                        capability="core.message.compose",
                        domain_id="finance_ashare",
                    ),
                    content={
                        "message": "无效合成不应被视为完成",
                        "delivery_status": "completed",
                        "synthesis_unavailable": False,
                        "source_artifact_ids": [
                            report.artifact_id,
                            invalid_synthesis.artifact_id,
                        ],
                    },
                    parent_artifact_ids=[
                        report.artifact_id,
                        invalid_synthesis.artifact_id,
                    ],
                    confidence=0.0,
                )
            )

            result = agent_os._ensure_graph_assistant_message(
                ExecutionRunResult(
                    snapshot=snapshot,
                    artifact_ids=[
                        report.artifact_id,
                        invalid_synthesis.artifact_id,
                        partial_assistant.artifact_id,
                    ],
                )
            )
            active = runner.board.list_artifacts(run_id=run_id, status="active")
            assistant = next(row for row in active if row.artifact_type == "assistant_message")
            synthesis = next(row for row in active if row.artifact_type == "main_agent_synthesis")
            events = read_jsonl(paths.root / "execution_events.jsonl")

            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(read_json(paths.root / "execution_snapshot.json")["status"], "completed")
            self.assertEqual(assistant.supersedes_artifact_id, partial_assistant.artifact_id)
            self.assertEqual(assistant.content["delivery_status"], "completed")
            self.assertEqual(synthesis.supersedes_artifact_id, invalid_synthesis.artifact_id)
            self.assertEqual(synthesis.content["conclusion"], "可恢复报告结论")
            self.assertIn(
                "graph_run_delivery_recovered",
                {event["event_type"] for event in events},
            )

    def test_graph_delivery_restores_partial_reason_without_report(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            request = RequestEnvelope(intent="stock_check", user_query="恢复部分交付")
            run_id, _, _ = agent_os.run_store.create_run(request, "stock_check")
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-partial-reason",
                request_id=request.request_id,
                graph=ExecutionGraph(
                    domain_id="finance_ashare",
                    objective=request.user_query,
                    metadata={"intent": "stock_check"},
                ),
                status="completed_partial",
            )

            result = agent_os._ensure_graph_assistant_message(ExecutionRunResult(snapshot=snapshot))
            assistant = next(
                row
                for row in agent_os._runner(run_id).board.list_artifacts(
                    run_id=run_id,
                    status="active",
                )
                if row.artifact_type == "assistant_message"
            )

            self.assertEqual(result.snapshot.status, "completed_partial")
            self.assertEqual(assistant.content["delivery_status"], "synthesis_unavailable")
            self.assertTrue(assistant.content["synthesis_unavailable"])

    def test_graph_delivery_skips_model_when_turn_budget_is_exhausted(self) -> None:
        class UnexpectedProvider:
            available = True

            def complete(self, **_kwargs):
                raise AssertionError("provider must not run without delivery budget")

        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            agent_os.main_agent.llm_provider = UnexpectedProvider()
            request = RequestEnvelope(intent="stock_check", user_query="分析 600000")
            run_id, _, _ = agent_os.run_store.create_run(request, "stock_check")
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-no-budget",
                request_id=request.request_id,
                graph=ExecutionGraph(
                    domain_id="finance_ashare",
                    objective=request.user_query,
                    metadata={"intent": "stock_check"},
                ),
                status="completed",
            )
            runner = agent_os._runner(run_id)
            runner.board.add(
                ArtifactRecord(
                    artifact_type="research_report",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="uzi_report_composer",
                        capability="finance.uzi.native.report.compose",
                        domain_id="finance_ashare",
                    ),
                    content={"conclusion": "报告结论"},
                    source_refs=[{"type": "test", "id": "source-1"}],
                    confidence=0.7,
                )
            )

            result = agent_os._ensure_graph_assistant_message(
                ExecutionRunResult(snapshot=snapshot),
                deadline_at=monotonic() + 1,
            )
            synthesis = next(row for row in runner.board.list_artifacts(run_id=run_id, status="active") if row.artifact_type == "main_agent_synthesis")

            self.assertEqual(result.snapshot.status, "completed")
            self.assertFalse(synthesis.content["model_generated"])
            self.assertEqual(
                synthesis.content["fallback_reason"],
                "turn_deadline_budget_exhausted",
            )

    def test_holding_report_child_uses_durable_report_without_second_model_call(
        self,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            agent_os.main_agent.llm_provider = None
            request = RequestEnvelope(
                intent="stock_check",
                user_query="分析 600000",
                constraints={"fund_holding_report_child": True},
            )
            run_id, paths, _ = agent_os.run_store.create_run(request, "stock_check")
            graph = ExecutionGraph(
                domain_id="finance_ashare",
                objective=request.user_query,
                metadata={"intent": "stock_check"},
            )
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="holding:batch:600000:1",
                request_id=request.request_id,
                graph=graph,
                status="completed",
            )
            runner = agent_os._runner(run_id)
            runner.board.add(
                ArtifactRecord(
                    artifact_type="research_report",
                    run_id=run_id,
                    thread_id=snapshot.thread_id,
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="uzi_report_composer",
                        capability="finance.uzi.native.report.compose",
                        node_id="report",
                        domain_id="finance_ashare",
                    ),
                    content={"conclusion": "UZI 报告结论", "report_allowed": True},
                    source_refs=[{"type": "test", "id": "source-1"}],
                    confidence=0.8,
                )
            )

            result = agent_os._ensure_graph_assistant_message(
                ExecutionRunResult(snapshot=snapshot)
            )
            artifacts = runner.board.list_artifacts(run_id=run_id, status="active")
            synthesis = next(row for row in artifacts if row.artifact_type == "main_agent_synthesis")
            assistant = next(row for row in artifacts if row.artifact_type == "assistant_message")
            events = read_jsonl(paths.root / "execution_events.jsonl")

            self.assertEqual(result.snapshot.status, "completed")
            self.assertFalse(synthesis.content["model_generated"])
            self.assertEqual(assistant.content["delivery_status"], "completed")
            self.assertFalse(assistant.content["synthesis_unavailable"])
            self.assertTrue(any(event["event_type"] == "assistant_message_ready" for event in events))
            self.assertFalse(any(event["event_type"] == "graph_run_partial" for event in events))

    def test_gated_graph_response_preserves_gated_status(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            request = RequestEnvelope(
                intent="domain:legal_contract",
                user_query="Review the contract.",
            )
            run_id, paths, _ = agent_os.run_store.create_run(
                request,
                "domain:legal_contract",
            )
            graph = ExecutionGraph(
                domain_id="legal_contract",
                objective=request.user_query,
            )
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                graph=graph,
                status="gated",
            )
            write_json(
                paths.root / "execution_snapshot.json",
                snapshot.model_dump(mode="json"),
            )

            response = agent_os._response_from_graph(
                request,
                ExecutionRunResult(snapshot=snapshot),
            )

            self.assertEqual(response.status, "gated")

    def test_versioned_runs_use_agent_os_execution_state(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                    # No provider means UZI's critical quality gate must hold
                    # the run instead of producing a partial report.
                    "openai_api_key": None,
                }
            )
            with TestClient(
                create_app(
                    settings=settings,
                    api_config=ApiConfig(api_token="agent-os-api-test"),
                )
            ) as client:
                headers = {"Authorization": "Bearer agent-os-api-test"}
                response = client.post(
                    "/api/v1/runs",
                    headers=headers,
                    json={
                        "user_query": "Review supplied evidence for 600000.",
                        "constraints": {"research_profile": "lite"},
                        "slots": {
                            "symbols": ["600000"],
                            "snapshots": [
                                {
                                    "snapshot_type": "market",
                                    "symbol": "600000",
                                    "facts": {"market_overview": {"spot": {"latest_price": 10}}},
                                }
                            ],
                        },
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["result"]["protocol"], "agent_os")
                self.assertEqual(payload["intent"], "stock_check")
                self.assertTrue(payload["turn_id"])
                self.assertEqual(payload["task_type"], "regular")
                self.assertEqual(payload["mode"], "graph")
                run_id = payload["run_id"]

                steps = client.get(f"/api/v1/runs/{run_id}/steps", headers=headers)
                self.assertEqual(steps.status_code, 200, steps.text)
                self.assertEqual(steps.json()["domain_id"], "finance_ashare")
                self.assertTrue(steps.json()["steps"])
                self.assertEqual(steps.json()["turn"]["turn_id"], payload["turn_id"])
                self.assertEqual(steps.json()["task_selection"]["execution_mode"], "graph")

                events = client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
                self.assertEqual(events.status_code, 200, events.text)
                event_rows = events.json()["events"]
                self.assertTrue(event_rows)
                self.assertEqual(payload["status"], "gated")
                self.assertTrue(any(event["event_type"] == "assistant_message_status" for event in event_rows))
                self.assertTrue(any(event["event_type"] == "artifact_committed" for event in event_rows))
                self.assertTrue(all(event.get("run_id") == run_id for event in event_rows))
                self.assertTrue(all(event.get("thread_id") for event in event_rows))
                self.assertTrue(all(event.get("request_id") for event in event_rows))
                history = client.get("/api/v1/runs", headers=headers)
                self.assertEqual(history.status_code, 200, history.text)
                history_row = next(row for row in history.json() if row["run_id"] == run_id)
                self.assertTrue(history_row["message"])
                self.assertTrue(history_row["display_summary"])
                metrics = client.get(f"/api/v1/runs/{run_id}/metrics", headers=headers)
                self.assertEqual(metrics.status_code, 200, metrics.text)
                self.assertGreaterEqual(metrics.json()["counts"]["subagents"], 1)

    def test_chat_clarification_continues_the_same_agent_os_run(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )

            class ClarificationRunService:
                def __init__(self, agent_os: AgentOS) -> None:
                    self.agent_os = agent_os
                    self.run_ids: list[str | None] = []

                def run(
                    self,
                    request: RequestEnvelope,
                    *,
                    run_id: str | None = None,
                ) -> ResponseEnvelope:
                    self.run_ids.append(run_id)
                    if run_id is None:
                        return self.agent_os.handle_request(request)
                    return ResponseEnvelope(
                        request_id=request.request_id,
                        run_id=run_id,
                        mode="graph",
                        intent="stock_check",
                        status="completed_partial",
                        message="Clarification continued.",
                        progress=ProgressState(status_message="completed", percent=100),
                    )

            run_services: list[ClarificationRunService] = []

            def build_run_service(agent_os: AgentOS) -> ClarificationRunService:
                service = ClarificationRunService(agent_os)
                run_services.append(service)
                return service

            with TestClient(
                create_app(
                    settings=settings,
                    api_config=ApiConfig(api_token=None, allow_unauthenticated=True),
                    run_service_factory=build_run_service,
                )
            ) as client:
                thread_response = client.post("/api/v1/chat/threads")
                self.assertEqual(thread_response.status_code, 201, thread_response.text)
                thread_id = thread_response.json()["thread_id"]

                first = client.post(
                    f"/api/v1/chat/threads/{thread_id}/runs",
                    json={
                        "intent": "stock_check",
                        "user_query": "Analyze a stock.",
                        "constraints": {"research_profile": "lite"},
                    },
                )
                self.assertEqual(first.status_code, 200, first.text)
                first_payload = first.json()
                self.assertEqual(first_payload["status"], "needs_clarification")
                first_run_id = first_payload["run_id"]

                second = client.post(
                    f"/api/v1/chat/threads/{thread_id}/runs",
                    json={"user_query": "600000"},
                )
                self.assertEqual(second.status_code, 200, second.text)
                second_payload = second.json()
                self.assertEqual(second_payload["status"], "completed_partial")
                self.assertEqual(second_payload["run_id"], first_run_id)
                self.assertEqual(run_services[0].run_ids, [None, first_run_id])

    def test_background_run_returns_a_run_id_before_completion_and_exposes_events(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                }
            )
            with TestClient(
                create_app(
                    settings=settings,
                    api_config=ApiConfig(api_token=None, allow_unauthenticated=True),
                )
            ) as client:
                response = client.post(
                    "/api/v1/runs",
                    json={
                        "user_query": "Who are you?",
                        "constraints": {"background": True},
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["status"], "running")
                run_id = payload["run_id"]

                initial_replay = client.get(f"/api/v1/runs/{run_id}/replay")
                self.assertEqual(initial_replay.status_code, 200, initial_replay.text)
                self.assertIn(initial_replay.json()["mode"], {"pending", "core"})

                terminal = None
                for _ in range(60):
                    events = client.get(f"/api/v1/runs/{run_id}/events").json()
                    if events["status"] in {"completed", "failed", "cancelled", "timed_out", "gated"}:
                        terminal = events
                        break
                    sleep(0.1)
                self.assertIsNotNone(terminal)
                assert terminal is not None
                self.assertTrue(terminal["events"])

    def test_new_run_service_recovers_a_persisted_background_reservation(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                }
            )
            request = RequestEnvelope(
                user_query="Who are you?",
                constraints={"background": True},
            )
            first = AgentOS(settings)
            run_id = first.prepare_run(request, run_id="recovered-background")
            service = AgentOSRunService(AgentOS(settings))
            try:
                for _ in range(60):
                    detail = service.agent_os.inspect_run(run_id)
                    if detail.get("mode") == "core":
                        state = detail["snapshot"]
                        if state.get("status") in {"completed", "failed", "cancelled"}:
                            break
                    sleep(0.1)
                detail = service.agent_os.inspect_run(run_id)
            finally:
                service.close()

            self.assertEqual(detail["mode"], "core")
            self.assertEqual(detail["snapshot"]["status"], "completed")

    def test_background_worker_lease_prevents_duplicate_process_claims(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                }
            )
            first = AgentOSRunService(AgentOS(settings))
            second = AgentOSRunService(AgentOS(settings))
            try:
                request = RequestEnvelope(
                    user_query="Who are you?",
                    constraints={"background": True},
                )
                run_id = first.agent_os.prepare_run(request, run_id="leased-background")
                self.assertTrue(first._claim_background_lease(run_id))
                self.assertFalse(second._claim_background_lease(run_id))
                first._release_background_lease(run_id)
                self.assertTrue(second._claim_background_lease(run_id))
                second._release_background_lease(run_id)
                self.assertFalse(
                    (settings.runs_root / run_id / "agent_os_worker_lease.json").exists()
                )
            finally:
                first.close()
                second.close()


if __name__ == "__main__":
    unittest.main()
