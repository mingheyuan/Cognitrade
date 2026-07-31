from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from api_test_support import (
    API_TOKEN,
    RUN_ID,
    THREAD_ID,
    FakeRunService,
    FakeRunServiceFactory,
    auth_headers,
)
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


REPO_ROOT = Path(__file__).resolve().parents[1]


class ApiV1ErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.settings = Settings.from_env(Path(self.temp_dir.name))
        ensure_workspace_layout(self.settings)
        self.run_service = FakeRunService()
        self.app = create_app(
            settings=self.settings,
            api_config=ApiConfig(api_token=API_TOKEN),
            run_service_factory=FakeRunServiceFactory(self.run_service),
            repo_root=REPO_ROOT,
        )
        self.client_context = TestClient(self.app, raise_server_exceptions=False)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def assert_error_envelope(self, response, *, status_code: int) -> dict:
        self.assertEqual(response.status_code, status_code)
        payload = response.json()
        self.assertEqual(set(payload), {"error"})
        error = payload["error"]
        self.assertIsInstance(error.get("code"), str)
        self.assertTrue(error["code"])
        self.assertIsInstance(error.get("message"), str)
        self.assertTrue(error["message"])
        return error

    def test_missing_run_maps_file_not_found_to_404(self) -> None:
        self.run_service.errors["replay"] = FileNotFoundError(f"run not found: {RUN_ID}")

        response = self.client.get(
            f"/api/v1/runs/{RUN_ID}/replay",
            headers=auth_headers(),
        )

        error = self.assert_error_envelope(response, status_code=404)
        self.assertIn("not_found", error["code"])

    def test_missing_nested_resource_maps_key_error_to_404(self) -> None:
        self.run_service.errors["thread"] = KeyError(THREAD_ID)

        response = self.client.get(
            f"/api/v1/runs/{RUN_ID}/threads/{THREAD_ID}",
            headers=auth_headers(),
        )

        error = self.assert_error_envelope(response, status_code=404)
        self.assertIn("not_found", error["code"])

    def test_unknown_rerun_node_maps_to_422(self) -> None:
        self.run_service.errors["rerun_from"] = ValueError(
            "unknown_rerun_node:not_a_node"
        )

        response = self.client.post(
            f"/api/v1/runs/{RUN_ID}/reruns",
            headers=auth_headers(),
            json={"from_node": "not_a_node"},
        )

        error = self.assert_error_envelope(response, status_code=422)
        self.assertEqual(error["code"], "unknown_rerun_node")

    def test_incomplete_rerun_source_maps_to_409(self) -> None:
        for core_message in (
            f"missing_request_payload:{RUN_ID}",
            f"missing_execution_graph_payload:{RUN_ID}",
        ):
            with self.subTest(core_message=core_message):
                self.run_service.errors["rerun_from"] = ValueError(core_message)
                response = self.client.post(
                    f"/api/v1/runs/{RUN_ID}/reruns",
                    headers=auth_headers(),
                    json={"from_node": "retrieve"},
                )
                error = self.assert_error_envelope(response, status_code=409)
                self.assertEqual(error["code"], "source_run_incomplete")

    def test_unhandled_exception_is_redacted_in_500_response(self) -> None:
        secret = "secret-provider-credential=abc123"
        self.run_service.errors["trace"] = RuntimeError(secret)

        with self.assertLogs("research_system.api.errors", level="ERROR"):
            response = self.client.get(
                f"/api/v1/runs/{RUN_ID}/trace",
                headers=auth_headers(),
            )

        error = self.assert_error_envelope(response, status_code=500)
        serialized = response.text
        self.assertEqual(error["code"], "internal_server_error")
        self.assertNotIn(secret, serialized)
        self.assertNotIn("Traceback", serialized)
        self.assertEqual(response.headers["x-request-id"], error["request_id"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_internal_builtin_exceptions_remain_server_errors(self) -> None:
        cases = (
            ("trace", ValueError("corrupt trace store")),
            ("trace", KeyError("corrupt trace key")),
            ("history", FileNotFoundError("missing internal history index")),
        )

        for operation, exception in cases:
            with self.subTest(operation=operation, exception=type(exception).__name__):
                self.run_service.errors[operation] = exception
                path = f"/api/v1/runs/{RUN_ID}/trace" if operation == "trace" else "/api/v1/runs"
                with self.assertLogs("research_system.api.errors", level="ERROR"):
                    response = self.client.get(path, headers=auth_headers())
                error = self.assert_error_envelope(response, status_code=500)
                self.assertEqual(error["code"], "internal_server_error")
                self.run_service.errors.pop(operation, None)

    def test_malformed_json_uses_error_envelope_and_does_not_call_core(self) -> None:
        headers = {**auth_headers(), "Content-Type": "application/json"}

        response = self.client.post(
            "/api/v1/runs",
            headers=headers,
            content=b'{"intent":"stock_check","user_query":',
        )

        self.assert_error_envelope(response, status_code=400)
        self.assertEqual(self.run_service.calls_for("run"), [])

    def test_query_validation_errors_use_the_same_envelope(self) -> None:
        for query in ("limit=0", "limit=101", "status=not_a_status"):
            with self.subTest(query=query):
                response = self.client.get(
                    f"/api/v1/runs?{query}",
                    headers=auth_headers(),
                )
                self.assert_error_envelope(response, status_code=422)

        self.assertEqual(self.run_service.calls_for("history"), [])

    def test_path_traversal_identifier_is_rejected_before_storage_access(self) -> None:
        paths = (
            "/api/v1/runs/%2E%2E/replay",
            "/api/v1/runs/%2E%2E%5Cworkspace/replay",
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path, headers=auth_headers())
                error = self.assert_error_envelope(response, status_code=400)
                self.assertEqual(error["code"], "invalid_identifier")

        self.assertEqual(self.run_service.calls_for("replay"), [])

    def test_unknown_route_is_still_returned_as_error_envelope(self) -> None:
        response = self.client.get(
            "/api/v1/not-a-real-endpoint",
            headers=auth_headers(),
        )

        self.assert_error_envelope(response, status_code=404)


if __name__ == "__main__":
    unittest.main()
