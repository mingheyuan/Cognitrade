from __future__ import annotations

import json
import time
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from types import SimpleNamespace
from unittest.mock import patch

import jsonschema

from domain_packs.finance_ashare.holding_reports import (
    FundTypeUnsupportedError,
    StaleHoldingsConfirmationRequired,
    freeze_fund_holdings,
    require_stale_confirmation,
)
from research_system.api.agent_os_run_service import AgentOSRunService
from research_system.api.fund_holding_report_service import (
    FundHoldingBatchNotFound,
    FundHoldingReportService,
)
from research_system.api.sse import RunEventSource
from research_system.runtime.storage.jsonl import read_json, read_jsonl
from research_system.runtime.storage.runs.run_store import RunStore


def snapshot_artifact(*, funds: list[dict], artifact_id: str = "fund-artifact-1") -> dict:
    return {
        "artifact_id": artifact_id,
        "artifact_type": "fund_snapshot",
        "status": "active",
        "content": {"funds": funds},
    }


def equity_fund(*, count: int = 10, fund_code: str = "110011") -> dict:
    return {
        "fund_code": fund_code,
        "fund_name": "测试股票基金",
        "fund_type": "股票型",
        "holdings_period": "2026-06-30",
        "holdings_coverage": {"is_fund_of_funds": False},
        "top_holdings": [
            {
                "rank": index,
                "code": f"600{index:03d}",
                "name": f"测试股票{index}",
                "weight_pct": 11 - index,
            }
            for index in range(1, count + 1)
        ],
    }


class FakeRunService:
    def __init__(self, source_artifacts: list[dict], agent_os: "FakeAgentOS") -> None:
        self.source_artifacts = source_artifacts
        self.agent_os = agent_os
        self.bound = None

    def bind_fund_holding_reports(self, service) -> None:
        self.bound = service

    def artifacts(self, run_id: str, **_scope) -> dict:
        if run_id == "source-run":
            return {"artifacts": self.source_artifacts}
        return {"artifacts": list(self.agent_os.details[run_id].get("artifacts") or [])}

    def cancel(self, run_id: str, **_scope) -> dict:
        detail = self.agent_os.details.get(run_id)
        if detail is not None:
            detail["snapshot"] = {"status": "cancelled"}
        return {"run_id": run_id, "status": "cancelled"}


class FakeAgentOS:
    def __init__(self, runs_root: Path, *, delay: float = 0.02) -> None:
        self.run_store = RunStore(runs_root)
        self.details: dict[str, dict] = {}
        self.delay = delay
        self.fail_symbols: set[str] = set()
        self.partial_symbols: set[str] = set()
        self.partial_terminal_symbols: set[str] = set()
        self.raise_after_completion_symbols: set[str] = set()
        self.active = 0
        self.max_active = 0
        self._lock = Lock()

    def prepare_run(self, request, *, run_id: str, intent: str) -> str:
        self.run_store.create_run(request, intent, run_id=run_id)
        self.details[run_id] = {
            "mode": "pending",
            "snapshot": {"status": "received"},
            "artifacts": [],
        }
        return run_id

    def inspect_run(self, run_id: str) -> dict:
        return self.details[run_id]

    def handle_request(self, request, *, run_id: str):
        symbol = str((request.slots.get("symbols") or [""])[0])
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            if symbol in self.fail_symbols:
                self.details[run_id] = {
                    "mode": "graph",
                    "snapshot": {"status": "failed"},
                    "artifacts": [],
                }
            else:
                status = "completed_partial" if symbol in self.partial_terminal_symbols else "completed"
                risks = ["quote_data_gap"] if symbol in self.partial_symbols else []
                native_id = f"native-{symbol}"
                self.details[run_id] = {
                    "mode": "graph",
                    "snapshot": {"status": status},
                    "artifacts": [
                        {
                            "artifact_id": native_id,
                            "artifact_type": "uzi_native_analysis",
                            "status": "active",
                            "content": {
                                "critical_count": 0,
                                "report_allowed": True,
                                "recovery_history": [],
                                "results": [{
                                    "self_review": {
                                        "critical_count": 0,
                                        "warning_count": len(risks),
                                        "issues": [
                                            {
                                                "severity": "warning",
                                                "issue": risk,
                                                "suggested_fix": "补充真实数据后重算",
                                            }
                                            for risk in risks
                                        ],
                                    }
                                }],
                            },
                            "risk_flags": risks,
                        },
                        {
                            "artifact_id": f"report-{symbol}",
                            "artifact_type": "research_report",
                            "status": "active",
                            "content": {
                                "native_analysis_artifact_id": native_id,
                                "critical_count": 0,
                                "report_allowed": True,
                            },
                            "risk_flags": risks,
                        },
                    ],
                }
                if symbol in self.raise_after_completion_symbols:
                    self.details[run_id]["turn"] = {"status": "failed"}
                    raise RuntimeError("response finalization failed after artifacts committed")
        finally:
            with self._lock:
                self.active -= 1
        return SimpleNamespace()

    def resume(self, run_id: str):
        request = read_json(self.run_store.load_run_paths(run_id).request)
        return self.handle_request(SimpleNamespace(slots=request["slots"]), run_id=run_id)


class FundHoldingFreezeTests(unittest.TestCase):
    def test_freezes_direct_holdings_and_keeps_unsupported_markets(self) -> None:
        fund = equity_fund(count=2)
        fund["top_holdings"].append({"rank": 3, "code": "HK00700", "name": "腾讯控股", "weight_pct": 3.2})
        frozen = freeze_fund_holdings(
            [snapshot_artifact(funds=[fund])],
            fund_code="110011",
            now=date(2026, 8, 6),
        )

        self.assertEqual(frozen["holding_source"], "direct_equity_disclosure")
        self.assertEqual(frozen["disclosure_date"], "2026-06-30")
        self.assertEqual(len(frozen["holdings"]), 3)
        self.assertTrue(frozen["holdings"][0]["supported"])
        self.assertFalse(frozen["holdings"][2]["supported"])

    def test_feeder_uses_target_etf_lookthrough_and_effective_weight(self) -> None:
        fund = {
            "fund_code": "020464",
            "fund_name": "测试 ETF 联接",
            "fund_type": "ETF联接",
            "holdings_coverage": {"is_fund_of_funds": True},
            "lookthrough_period": "目标ETF 2026年2季度股票投资明细 截止至： 2026-06-30",
            "top_holdings": [{"code": "688999", "name": "不应使用", "weight_pct": 1}],
            "lookthrough_holdings": [
                {"rank": 16, "code": "688012", "name": "中微公司", "weight_pct": 15.02, "effective_weight_pct": 13.851444},
            ],
        }
        frozen = freeze_fund_holdings(
            [snapshot_artifact(funds=[fund])],
            fund_code="020464",
            now=date(2026, 8, 6),
        )

        self.assertEqual(frozen["holding_source"], "target_etf_lookthrough")
        self.assertEqual(frozen["holdings"][0]["symbol"], "688012")
        self.assertEqual(frozen["holdings"][0]["rank"], 1)
        self.assertEqual(frozen["holdings"][0]["fund_weight_pct"], 13.851444)
        self.assertEqual(frozen["holdings"][0]["target_security_weight_pct"], 15.02)

    def test_quarter_disclosure_is_normalized_to_quarter_end(self) -> None:
        fund = equity_fund(count=1)
        fund["holdings_period"] = "2026年2季度股票投资明细"
        frozen = freeze_fund_holdings(
            [snapshot_artifact(funds=[fund])],
            fund_code="110011",
            now=date(2026, 8, 6),
        )

        self.assertEqual(frozen["disclosure_date"], "2026-06-30")
        self.assertEqual(frozen["disclosure_age_days"], 37)
        self.assertEqual(frozen["stale_level"], "current")

    def test_stale_threshold_requires_confirmation_and_missing_date_is_a_gap(self) -> None:
        fund = equity_fund(count=1)
        fund["holdings_period"] = "2025-12-01"
        frozen = freeze_fund_holdings(
            [snapshot_artifact(funds=[fund])],
            fund_code="110011",
            now=date(2026, 8, 6),
        )
        self.assertEqual(frozen["stale_level"], "confirmation_required")
        with self.assertRaises(StaleHoldingsConfirmationRequired):
            require_stale_confirmation(frozen, confirmed=False)
        require_stale_confirmation(frozen, confirmed=True)

        fund["holdings_period"] = "最新披露期"
        missing = freeze_fund_holdings(
            [snapshot_artifact(funds=[fund])],
            fund_code="110011",
            now=date(2026, 8, 6),
        )
        self.assertIn("holding_disclosure_date_missing", missing["data_gap"])

    def test_rejects_non_equity_funds(self) -> None:
        fund = equity_fund(count=1)
        fund.update({"fund_name": "测试债券基金", "fund_type": "债券型"})
        with self.assertRaises(FundTypeUnsupportedError):
            freeze_fund_holdings([snapshot_artifact(funds=[fund])], fund_code="110011")


class FundHoldingReportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.runs_root = Path(self.temp_dir.name) / "runs"
        self.agent_os = FakeAgentOS(self.runs_root)
        self.artifacts = [snapshot_artifact(funds=[equity_fund()])]
        self.run_service = FakeRunService(self.artifacts, self.agent_os)
        self.settings = SimpleNamespace(
            runs_root=self.runs_root,
            fund_holding_report_concurrency=2,
        )
        self.service = FundHoldingReportService(
            settings=self.settings,
            agent_os=self.agent_os,
            run_service=self.run_service,
        )

    def tearDown(self) -> None:
        self.service.close()
        self.temp_dir.cleanup()

    def wait_terminal(self, batch_id: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.service.detail(batch_id)
            if state["status"] != "running":
                return state
            time.sleep(0.01)
        self.fail(f"batch did not finish: {batch_id}")

    def test_runs_ten_children_with_dedicated_concurrency_and_canonical_input(self) -> None:
        self.agent_os.delay = 0.08
        created = self.service.create_batch("source-run", fund_code="110011", profile="lite")
        active_duplicate = self.service.create_batch(
            "source-run", fund_code="110011", profile="lite", force=True
        )
        self.assertEqual(active_duplicate["batch_id"], created["batch_id"])
        final = self.wait_terminal(created["batch_id"])

        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["counts"]["deliverable"], 10)
        self.assertEqual(self.agent_os.max_active, 2)
        metrics = self.service.metrics(final["batch_id"])
        self.assertGreater(metrics["usage"]["duration_seconds"], 0)
        self.assertEqual(
            metrics["concurrency"],
            {"holding_reports": 2, "uzi_llm": 2},
        )
        events = read_jsonl(self.agent_os.run_store.load_run_paths(final["batch_id"]).root / "execution_events.jsonl")
        self.assertTrue(any(row.get("event_type") == "assistant_message_ready" for row in events))
        self.assertTrue(RunEventSource._is_terminal(self.agent_os.run_store.load_run_paths(final["batch_id"]).root))
        child = final["holdings"][0]
        request = read_json(self.agent_os.run_store.load_run_paths(child["child_run_id"]).request)
        self.assertNotIn("holding_context", request["slots"])
        self.assertEqual(request["slots"], {"symbols": [child["symbol"]]})
        self.assertTrue(request["constraints"]["canonical_uzi_analysis"])
        self.assertEqual(request["constraints"]["parent_batch_id"], final["batch_id"])
        self.assertEqual(final["source_artifact_id"], "fund-artifact-1")
        self.assertEqual(child["rank"], 1)
        user_request_schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "domain_packs"
                / "finance_ashare"
                / "artifact_schemas"
                / "user_request.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.validate(
            {
                "query": request["user_query"],
                "symbols": request["slots"]["symbols"],
            },
            user_request_schema,
        )

        reused = self.service.create_batch("source-run", fund_code="110011", profile="lite")
        self.assertEqual(reused["batch_id"], final["batch_id"])
        self.agent_os.settings = SimpleNamespace(
            runs_root=self.runs_root,
            max_child_threads_per_turn=4,
            run_timeout_seconds=300,
        )
        api_runs = AgentOSRunService(self.agent_os)
        try:
            api_runs.bind_fund_holding_reports(self.service)
            history = api_runs.history()
            self.assertEqual([row["run_id"] for row in history], [final["batch_id"]])
            self.assertEqual(history[0]["intent"], "fund_holding_research")
        finally:
            api_runs.close()
        regenerated = self.service.create_batch("source-run", fund_code="110011", profile="lite", force=True)
        self.assertNotEqual(regenerated["batch_id"], final["batch_id"])
        self.wait_terminal(regenerated["batch_id"])

    def test_partial_failure_retry_and_partial_delivery(self) -> None:
        self.agent_os.fail_symbols.add("600001")
        self.agent_os.partial_symbols.add("600002")
        created = self.service.create_batch("source-run", fund_code="110011", profile="lite")
        final = self.wait_terminal(created["batch_id"])

        self.assertEqual(final["status"], "completed_partial")
        self.assertEqual(final["counts"]["failed"], 1)
        self.assertEqual(final["counts"]["deliverable"], 9)
        partial = next(item for item in final["holdings"] if item["symbol"] == "600002")
        self.assertTrue(partial["report_available"])
        self.assertIn("quote_data_gap", partial["data_gap"])

        legacy_state = self.service._load_batch(final["batch_id"])
        legacy_state["concurrency"] = 5
        self.service._write_batch(
            legacy_state,
            event_type="fund_holding_batch_legacy_concurrency",
        )
        self.agent_os.fail_symbols.clear()
        retried = self.service.retry(final["batch_id"], symbols=["600001"])
        self.assertEqual(retried["concurrency"], 2)
        recovered = self.wait_terminal(retried["batch_id"])
        self.assertEqual(recovered["counts"]["deliverable"], 10)
        self.assertEqual(len(recovered["holdings"][0]["attempt_run_ids"]), 2)

        regenerated = self.service.retry(
            recovered["batch_id"], symbols=["600002"], force=True
        )
        regenerated_final = self.wait_terminal(regenerated["batch_id"])
        regenerated_item = next(
            item for item in regenerated_final["holdings"] if item["symbol"] == "600002"
        )
        untouched_item = next(
            item for item in regenerated_final["holdings"] if item["symbol"] == "600003"
        )
        self.assertEqual(len(regenerated_item["attempt_run_ids"]), 2)
        self.assertEqual(len(untouched_item["attempt_run_ids"]), 1)

    def test_committed_reports_survive_response_finalization_error(self) -> None:
        self.run_service.source_artifacts = [snapshot_artifact(funds=[equity_fund(count=1)])]
        self.agent_os.raise_after_completion_symbols.add("600001")

        created = self.service.create_batch("source-run", fund_code="110011", profile="lite")
        final = self.wait_terminal(created["batch_id"])

        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["counts"]["deliverable"], 1)
        self.assertTrue(final["holdings"][0]["report_available"])
        self.assertIsNone(final["holdings"][0]["error"])

    def test_quality_gated_report_survives_partial_chat_synthesis(self) -> None:
        self.run_service.source_artifacts = [snapshot_artifact(funds=[equity_fund(count=1)])]
        self.agent_os.partial_terminal_symbols.add("600001")

        created = self.service.create_batch("source-run", fund_code="110011", profile="lite")
        final = self.wait_terminal(created["batch_id"])

        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["counts"]["deliverable"], 1)
        self.assertEqual(final["counts"]["failed"], 0)
        self.assertEqual(final["holdings"][0]["status"], "completed")
        self.assertTrue(final["holdings"][0]["report_available"])
        self.assertIsNone(final["holdings"][0]["error"])

    def test_cancel_preserves_completed_and_marks_unfinished(self) -> None:
        self.agent_os.delay = 0.08
        created = self.service.create_batch("source-run", fund_code="110011", profile="medium")
        time.sleep(0.02)
        cancelled = self.service.cancel(created["batch_id"])
        self.assertTrue(cancelled["cancel_requested"])
        final = self.wait_terminal(created["batch_id"])
        self.assertEqual(final["status"], "completed_partial")
        self.assertGreater(final["counts"]["cancelled"], 0)

    def test_recovered_running_item_honors_persisted_batch_cancellation(self) -> None:
        self.run_service.source_artifacts = [
            snapshot_artifact(funds=[equity_fund(count=1)])
        ]
        with patch.object(self.service, "_submit_item"):
            created = self.service.create_batch(
                "source-run",
                fund_code="110011",
                profile="medium",
            )
        state = self.service._load_batch(created["batch_id"])
        item = state["holdings"][0]
        item["status"] = "running"
        state["cancel_requested"] = True
        self.service._refresh_parent_status(state)
        self.service._write_batch(
            state,
            event_type="fund_holding_batch_cancel_requested",
        )

        self.service._execute_item(
            created["batch_id"],
            str(item["symbol"]),
            str(item["child_run_id"]),
        )

        final = self.service.detail(created["batch_id"])
        self.assertEqual(final["status"], "completed_partial")
        self.assertEqual(final["counts"]["cancelled"], 1)
        self.assertEqual(
            self.agent_os.details[str(item["child_run_id"])]["mode"],
            "pending",
        )

    def test_zip_contains_each_deliverable_pdf_and_readme(self) -> None:
        fund = equity_fund(count=2)
        self.run_service.source_artifacts = [snapshot_artifact(funds=[fund])]
        created = self.service.create_batch("source-run", fund_code="110011", profile="lite", force=True)
        final = self.wait_terminal(created["batch_id"])

        with patch("research_system.api.fund_holding_report_service.render_uzi_native_html", return_value=SimpleNamespace(html="<html></html>")), patch(
            "research_system.api.fund_holding_report_service.render_uzi_native_pdf",
            return_value=b"%PDF-test",
        ):
            archive, filename, count = self.service.build_pdf_zip(final["batch_id"])

        self.assertEqual(count, 2)
        self.assertTrue(filename.endswith(".zip"))
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            self.assertIn("README.txt", bundle.namelist())
            self.assertEqual(len([name for name in bundle.namelist() if name.endswith(".pdf")]), 2)

    def test_completed_partial_and_legacy_presentation_cannot_bypass_uzi_gate(self) -> None:
        detail = {
            "mode": "graph",
            "snapshot": {"status": "completed_partial"},
            "artifacts": [
                {"artifact_id": "report", "artifact_type": "research_report", "status": "active", "content": {}},
                {"artifact_id": "presentation", "artifact_type": "report_presentation", "status": "active", "content": {}},
            ],
        }

        available, gaps = self.service._report_state(detail)

        self.assertFalse(available)
        self.assertIn("uzi_native_analysis_missing", gaps)

    def test_native_critical_blocks_holding_report_delivery(self) -> None:
        detail = {
            "mode": "graph",
            "snapshot": {"status": "completed_partial"},
            "artifacts": [
                {
                    "artifact_id": "native",
                    "artifact_type": "uzi_native_analysis",
                    "status": "active",
                    "content": {
                        "critical_count": 1,
                        "report_allowed": False,
                        "results": [{"self_review": {"issues": [{"severity": "critical", "issue": "缺少真实价格"}]}}],
                    },
                },
                {
                    "artifact_id": "report",
                    "artifact_type": "research_report",
                    "status": "active",
                    "content": {
                        "native_analysis_artifact_id": "native",
                        "critical_count": 1,
                        "report_allowed": False,
                    },
                },
            ],
        }

        available, gaps = self.service._report_state(detail)

        self.assertFalse(available)
        self.assertIn("uzi_critical_quality_gate_blocked", gaps)

    def test_terminal_gate_error_preserves_all_critical_fixes_without_exception_names(self) -> None:
        detail = {
            "artifacts": [
                {
                    "artifact_type": "uzi_native_analysis",
                    "status": "active",
                    "content": {
                        "results": [
                            {
                                "self_review": {
                                    "issues": [
                                        {
                                            "severity": "critical",
                                            "issue": "所属行业缺失",
                                            "suggested_fix": "恢复真实行业来源后重算",
                                        },
                                        {
                                            "severity": "critical",
                                            "dim": "agent_analysis",
                                            "issue": "Agent 分析生成失败：LLMProviderError",
                                            "suggested_fix": "CLI 直跑可以忽略；Agent OS 需要生成分析",
                                        },
                                        {
                                            "severity": "warning",
                                            "issue": "估值样本不足",
                                            "suggested_fix": "补充同行样本",
                                        },
                                        {
                                            "severity": "critical",
                                            "dim": "3_",
                                            "issue": "应跑的维度 3 完全缺失",
                                            "suggested_fix": "重跑 run.py <ticker> --no-resume 或手动 fetch_X",
                                        },
                                    ]
                                }
                            }
                        ]
                    },
                }
            ]
        }

        message = self.service._terminal_report_error(detail, "gated")

        self.assertIn("所属行业缺失（建议：恢复真实行业来源后重算）", message)
        self.assertIn(
            "Agent 分析生成失败：运行异常（建议：在 Agent OS 中完成结构化 Agent 分析后重算与自审，不允许忽略该 critical）",
            message,
        )
        self.assertNotIn("估值样本不足", message)
        self.assertNotIn("LLMProviderError", message)
        self.assertNotIn("CLI 直跑可以忽略", message)
        self.assertIn(
            "应跑的维度 3 完全缺失（建议：通过 Agent OS 仅重试该持仓，系统会按当前研究档位重新执行启用的数据源）",
            message,
        )
        self.assertNotIn("run.py", message)
        self.assertNotIn("fetch_X", message)

    def test_terminal_gate_error_has_actionable_fallback_without_native_artifact(self) -> None:
        message = self.service._terminal_report_error({"artifacts": []}, "gated")

        self.assertIn("未返回具体 critical", message)
        self.assertIn("打开“证据”", message)

    def test_terminal_agent_timeout_does_not_claim_a_data_source_gap(self) -> None:
        detail = {
            "artifacts": [
                {
                    "artifact_type": "uzi_native_analysis",
                    "status": "active",
                    "content": {
                        "results": [
                            {
                                "self_review": {
                                    "issues": [
                                        {
                                            "severity": "critical",
                                            "dim": "agent_analysis",
                                            "issue": "agent_analysis.json 不存在",
                                            "suggested_fix": "完成 Agent 分析后重算",
                                        },
                                        {
                                            "severity": "critical",
                                            "dim": "agent_analysis",
                                            "issue": "Agent 分析生成失败：llm_request_timeout: 480s",
                                            "evidence": "llm_transport_timeout",
                                            "suggested_fix": "按自审问题修正后重算",
                                        },
                                    ]
                                }
                            }
                        ]
                    },
                }
            ]
        }

        message = self.service._terminal_report_error(detail, "gated")

        self.assertIn("模型服务响应超时", message)
        self.assertIn("模型请求已超时", message)
        self.assertNotIn("agent_analysis.json 不存在", message)
        self.assertNotIn("Agent 分析缺失或未确认", message)
        self.assertNotIn("补充真实数据或恢复数据源", message)

    def test_terminal_upstream_502_does_not_claim_a_data_source_gap(self) -> None:
        detail = {
            "artifacts": [
                {
                    "artifact_type": "uzi_native_analysis",
                    "status": "active",
                    "content": {
                        "results": [
                            {
                                "self_review": {
                                    "issues": [
                                        {
                                            "severity": "critical",
                                            "dim": "agent_analysis",
                                            "issue": "Agent 分析生成失败：502 Upstream request failed",
                                            "evidence": "llm_provider_unavailable",
                                            "suggested_fix": "按自审问题修正后重算",
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                }
            ]
        }

        message = self.service._terminal_report_error(detail, "gated")

        self.assertIn("模型上游服务暂时不可用", message)
        self.assertIn("模型上游暂时不可用", message)
        self.assertNotIn("补充真实数据或恢复数据源", message)

    def test_public_payload_enriches_legacy_generic_gated_error(self) -> None:
        self.agent_os.details["legacy-child"] = {
            "turn": {"status": "gated"},
            "artifacts": [
                {
                    "artifact_type": "uzi_native_analysis",
                    "status": "active",
                    "content": {
                        "results": [
                            {
                                "self_review": {
                                    "issues": [
                                        {
                                            "severity": "critical",
                                            "issue": "六维定性研究证据不足",
                                            "suggested_fix": "补齐每维两个真实 URL 后重算",
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                }
            ],
        }
        state = {
            "holdings": [
                {
                    "child_run_id": "legacy-child",
                    "error": "子运行以 gated 状态结束，未生成完整报告",
                }
            ]
        }

        public = self.service._public(state)

        self.assertIn("六维定性研究证据不足", public["holdings"][0]["error"])
        self.assertIn("补齐每维两个真实 URL 后重算", public["holdings"][0]["error"])
        self.assertEqual(
            state["holdings"][0]["error"],
            "子运行以 gated 状态结束，未生成完整报告",
        )

    def test_batch_scope_requires_exact_user_and_tenant_match(self) -> None:
        created = self.service.create_batch(
            "source-run",
            fund_code="110011",
            profile="lite",
            user_id="alice",
            tenant_id="tenant-a",
        )

        self.assertEqual(
            self.service.detail(
                created["batch_id"],
                user_id="alice",
                tenant_id="tenant-a",
            )["batch_id"],
            created["batch_id"],
        )
        with self.assertRaises(FundHoldingBatchNotFound):
            self.service.detail(
                created["batch_id"],
                user_id="bob",
                tenant_id="tenant-a",
            )
        with self.assertRaises(FundHoldingBatchNotFound):
            self.service.detail(
                created["batch_id"],
                user_id="alice",
                tenant_id="tenant-b",
            )
        self.wait_terminal(created["batch_id"])


if __name__ == "__main__":
    unittest.main()
