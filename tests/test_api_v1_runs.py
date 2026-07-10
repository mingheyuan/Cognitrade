from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from api_test_support import (
    API_TOKEN,
    INVOCATION_ID,
    REQUEST_ID,
    RERUN_ID,
    RUN_ID,
    THREAD_ID,
    FakeOrchestrator,
    FakeOrchestratorFactory,
    auth_headers,
    completed_response,
)
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope


REPO_ROOT = Path(__file__).resolve().parents[1]


class ApiV1RunsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.settings = Settings.from_env(Path(self.temp_dir.name))
        ensure_workspace_layout(self.settings)
        self.orchestrator = FakeOrchestrator()
        self.orchestrator_factory = FakeOrchestratorFactory(self.orchestrator)
        self.app = create_app(
            settings=self.settings,
            api_config=ApiConfig(api_token=API_TOKEN),
            orchestrator_factory=self.orchestrator_factory,
            repo_root=REPO_ROOT,
        )
        self.client_context = TestClient(self.app, raise_server_exceptions=False)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_lifespan_constructs_one_orchestrator_and_closes_it(self) -> None:
        self.assertEqual(self.orchestrator_factory.settings_seen, [self.settings])
        self.assertFalse(self.orchestrator.closed)

        self.client_context.__exit__(None, None, None)

        self.assertTrue(self.orchestrator.closed)
        self.client_context = _ClosedClientContext()

    def test_bearer_authentication_is_required(self) -> None:
        cases = (
            ({}, "authentication_required"),
            ({"Authorization": "Basic abc"}, "authentication_required"),
            (auth_headers("wrong-token"), "invalid_token"),
        )

        for headers, expected_code in cases:
            with self.subTest(expected_code=expected_code, headers=headers):
                response = self.client.get("/api/v1/runs", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["error"]["code"], expected_code)

        self.assertEqual(self.orchestrator.calls, [])

    def test_transport_request_id_cache_and_cors_headers(self) -> None:
        response = self.client.get(
            "/api/v1/runs",
            headers={
                **auth_headers(),
                "X-Request-ID": "browser-request-1",
                "Origin": "http://127.0.0.1:4173",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-request-id"], "browser-request-1")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://127.0.0.1:4173",
        )

    def test_startup_requires_token_unless_local_override_is_explicit(self) -> None:
        app = create_app(
            settings=self.settings,
            api_config=ApiConfig(api_token=None),
            orchestrator_factory=FakeOrchestratorFactory(FakeOrchestrator()),
            repo_root=REPO_ROOT,
        )

        with self.assertRaisesRegex(ValueError, "COGNITRADE_API_TOKEN is required"):
            with TestClient(app, raise_server_exceptions=False):
                pass

    def test_unauthenticated_mode_rejects_non_loopback_binding(self) -> None:
        config = ApiConfig(
            api_token=None,
            allow_unauthenticated=True,
            host="0.0.0.0",
        )

        with self.assertRaisesRegex(ValueError, "must bind to a loopback host"):
            config.validate()

        ApiConfig(
            api_token=None,
            allow_unauthenticated=True,
            host="127.0.0.1",
        ).validate()

    def test_startup_failure_closes_constructed_orchestrator(self) -> None:
        orchestrator = FakeOrchestrator()

        def fail_chat_service(_settings: Settings):
            raise RuntimeError("chat startup failed")

        app = create_app(
            settings=self.settings,
            api_config=ApiConfig(api_token=API_TOKEN),
            orchestrator_factory=FakeOrchestratorFactory(orchestrator),
            chat_service_factory=fail_chat_service,
            repo_root=REPO_ROOT,
        )

        with self.assertRaisesRegex(RuntimeError, "chat startup failed"):
            with TestClient(app, raise_server_exceptions=False):
                pass

        self.assertTrue(orchestrator.closed)

    def test_post_runs_validates_and_forwards_request_envelope(self) -> None:
        response = self.client.post(
            "/api/v1/runs",
            headers=auth_headers(),
            json={
                "request_id": REQUEST_ID,
                "intent": "stock_check",
                "user_query": "分析 600519 的投资价值",
                "conversation_context": [{"role": "user", "content": "长期视角"}],
                "constraints": {
                    "symbol": "600519",
                    "snapshot_types": ["market", "fundamental", "notice"],
                    "investment_cycle": "long_term",
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request_id"], REQUEST_ID)
        self.assertEqual(response.json()["run_id"], RUN_ID)
        self.assertEqual(response.json()["status"], "completed")
        calls = self.orchestrator.calls_for("run")
        self.assertEqual(len(calls), 1)
        request = calls[0].args[0]
        self.assertIsInstance(request, RequestEnvelope)
        self.assertEqual(request.request_id, REQUEST_ID)
        self.assertEqual(request.intent, "stock_check")
        self.assertEqual(request.constraints["symbol"], "600519")
        self.assertEqual(request.slots, {})

    def test_post_runs_defaults_an_unspecified_intent_for_agent_os_task_intake(self) -> None:
        response = self.client.post(
            "/api/v1/runs",
            headers=auth_headers(),
            json={"user_query": "Analyze 600519."},
        )

        self.assertEqual(response.status_code, 200)
        calls = self.orchestrator.calls_for("run")
        self.assertEqual(len(calls), 1)
        request = calls[0].args[0]
        self.assertEqual(request.intent, "stock_check")

    def test_domain_terminal_statuses_remain_http_200(self) -> None:
        for status in ("needs_clarification", "failed", "cancelled", "timed_out"):
            with self.subTest(status=status):
                self.orchestrator.outputs["run"] = completed_response(status=status)
                response = self.client.post(
                    "/api/v1/runs",
                    headers=auth_headers(),
                    json={
                        "request_id": REQUEST_ID,
                        "intent": "stock_check",
                        "user_query": "分析 600519",
                        "constraints": {"symbol": "600519"},
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], status)

    def test_history_filters_before_applying_limit(self) -> None:
        response = self.client.get(
            "/api/v1/runs?status=failed&limit=1",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [{"run_id": RERUN_ID, "status": "failed", "intent": "stock_compare"}],
        )
        self.assertEqual(len(self.orchestrator.calls_for("history")), 1)

    def test_observability_routes_forward_identifiers_without_rewriting_payloads(self) -> None:
        cases = (
            (f"/api/v1/runs/{RUN_ID}/replay", "replay", (RUN_ID,)),
            (f"/api/v1/runs/{RUN_ID}/events", "events", (RUN_ID,)),
            (f"/api/v1/runs/{RUN_ID}/trace", "trace", (RUN_ID,)),
            (f"/api/v1/runs/{RUN_ID}/steps", "steps", (RUN_ID,)),
            (f"/api/v1/runs/{RUN_ID}/metrics", "metrics", (RUN_ID,)),
            (f"/api/v1/runs/{RUN_ID}/recovery", "resume", (RUN_ID,)),
            (
                f"/api/v1/runs/{RUN_ID}/threads/{THREAD_ID}",
                "thread",
                (RUN_ID, THREAD_ID),
            ),
            (
                f"/api/v1/runs/{RUN_ID}/threads/{THREAD_ID}/replay",
                "thread_replay",
                (RUN_ID, THREAD_ID),
            ),
            (
                f"/api/v1/runs/{RUN_ID}/threads/{THREAD_ID}/metrics",
                "thread_metrics",
                (RUN_ID, THREAD_ID),
            ),
            (
                f"/api/v1/runs/{RUN_ID}/subagents/{INVOCATION_ID}",
                "subagent",
                (RUN_ID, INVOCATION_ID),
            ),
        )

        for path, method_name, expected_args in cases:
            with self.subTest(path=path):
                before = len(self.orchestrator.calls_for(method_name))
                response = self.client.get(path, headers=auth_headers())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), self.orchestrator.outputs[method_name])
                calls = self.orchestrator.calls_for(method_name)
                self.assertEqual(len(calls), before + 1)
                self.assertEqual(calls[-1].args, expected_args)
                self.assertEqual(calls[-1].kwargs, {})

    def test_cancel_forwards_without_request_body(self) -> None:
        response = self.client.post(
            f"/api/v1/runs/{RUN_ID}/cancel",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cancellation_requested"])
        call = self.orchestrator.calls_for("cancel")[-1]
        self.assertEqual(call.args, (RUN_ID,))
        self.assertEqual(call.kwargs, {})

    def test_rerun_route_forwards_execution_graph_node(self) -> None:
        top_level = self.client.post(
            f"/api/v1/runs/{RUN_ID}/reruns",
            headers=auth_headers(),
            json={"from_node": "retrieve"},
        )
        self.assertEqual(top_level.status_code, 200)
        self.assertEqual(top_level.json()["run_id"], RERUN_ID)
        top_call = self.orchestrator.calls_for("rerun_from")[-1]
        self.assertEqual(top_call.args, (RUN_ID,))
        self.assertEqual(top_call.kwargs, {"from_node": "retrieve"})

    def test_run_mutations_are_serialized(self) -> None:
        state_lock = threading.Lock()
        first_entered = threading.Event()
        second_entered = threading.Event()
        release_first = threading.Event()
        call_count = 0
        active_count = 0
        max_active_count = 0

        def blocking_run(_request):
            nonlocal call_count, active_count, max_active_count
            with state_lock:
                call_count += 1
                current_call = call_count
                active_count += 1
                max_active_count = max(max_active_count, active_count)
            if current_call == 1:
                first_entered.set()
                release_first.wait(timeout=2.0)
            else:
                second_entered.set()
            with state_lock:
                active_count -= 1
            return completed_response()

        self.orchestrator.outputs["run"] = blocking_run
        request_json = {
            "intent": "stock_check",
            "user_query": "分析 600519",
            "constraints": {"symbol": "600519"},
        }
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                self.client.post,
                "/api/v1/runs",
                headers=auth_headers(),
                json=request_json,
            )
            self.assertTrue(first_entered.wait(timeout=1.0))
            second = executor.submit(
                self.client.post,
                "/api/v1/runs",
                headers=auth_headers(),
                json=request_json,
            )
            try:
                self.assertFalse(second_entered.wait(timeout=0.1))
            finally:
                release_first.set()
            first_response = first.result(timeout=2.0)
            second_response = second.result(timeout=2.0)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(max_active_count, 1)

    def test_reads_and_cancel_do_not_wait_for_the_run_mutation_lock(self) -> None:
        run_entered = threading.Event()
        release_run = threading.Event()

        def blocking_run(_request):
            run_entered.set()
            release_run.wait(timeout=2.0)
            return completed_response()

        self.orchestrator.outputs["run"] = blocking_run
        request_json = {
            "intent": "stock_check",
            "user_query": "分析 600519",
            "constraints": {"symbol": "600519"},
        }
        with ThreadPoolExecutor(max_workers=3) as executor:
            run_future = executor.submit(
                self.client.post,
                "/api/v1/runs",
                headers=auth_headers(),
                json=request_json,
            )
            self.assertTrue(run_entered.wait(timeout=1.0))
            history_future = executor.submit(
                self.client.get,
                "/api/v1/runs",
                headers=auth_headers(),
            )
            cancel_future = executor.submit(
                self.client.post,
                f"/api/v1/runs/{RUN_ID}/cancel",
                headers=auth_headers(),
            )
            try:
                history_response = history_future.result(timeout=0.5)
                cancel_response = cancel_future.result(timeout=0.5)
            finally:
                release_run.set()
            run_response = run_future.result(timeout=2.0)

        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(run_response.status_code, 200)

    def test_intent_specific_constraints_are_rejected_before_orchestration(self) -> None:
        cases = (
            {
                "intent": "unknown_intent",
                "user_query": "unsupported",
                "constraints": {},
            },
            {
                "intent": "stock_compare",
                "user_query": "只比较一个标的",
                "constraints": {"symbols": ["600519"]},
            },
            {
                "intent": "stock_compare",
                "user_query": "比较重复标的",
                "constraints": {"symbols": ["600519", "000001", "600519"]},
            },
            {
                "intent": "review_thesis",
                "user_query": "复核历史结论",
                "constraints": {},
            },
            {
                "intent": "methodology_evolution",
                "user_query": "演进方法论",
                "constraints": {},
            },
            {
                "intent": "stock_check",
                "user_query": "分析 600519",
                "constraints": {"symbol": "600519", "unsupported_constraint": True},
            },
        )

        for payload in cases:
            with self.subTest(intent=payload["intent"], constraints=payload["constraints"]):
                response = self.client.post(
                    "/api/v1/runs",
                    headers=auth_headers(),
                    json=payload,
                )
                self.assertEqual(response.status_code, 422)
                self.assertIn("code", response.json()["error"])

        self.assertEqual(self.orchestrator.calls_for("run"), [])

    def test_request_envelope_rejects_unknown_top_level_fields(self) -> None:
        response = self.client.post(
            "/api/v1/runs",
            headers=auth_headers(),
            json={
                "intent": "stock_check",
                "user_query": "分析 600519",
                "constraints": {"symbol": "600519"},
                "unexpected": "field",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("error", response.json())
        self.assertEqual(self.orchestrator.calls_for("run"), [])


class _ClosedClientContext:
    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
