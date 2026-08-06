from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_test_support import (
    API_TOKEN,
    EVENT_ID,
    RUN_ID,
    FakeRunService,
    FakeRunServiceFactory,
    auth_headers,
    run_event,
)
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.api.sse import RunEventSource
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.storage.jsonl import append_jsonl, write_json


REPO_ROOT = Path(__file__).resolve().parents[1]
SECOND_EVENT_ID = "01J00000000000000000000007"
MISSING_RUN_ID = "01J00000000000000000000008"

EXPECTED_API_V1_OPERATIONS = {
    ("POST", "/api/v1/runs"),
    ("GET", "/api/v1/runs"),
    ("GET", "/api/v1/runs/{run_id}"),
    ("GET", "/api/v1/runs/{run_id}/artifacts"),
    ("GET", "/api/v1/runs/{run_id}/report"),
    ("GET", "/api/v1/runs/{run_id}/replay"),
    ("GET", "/api/v1/runs/{run_id}/events"),
    ("GET", "/api/v1/runs/{run_id}/events/stream"),
    ("GET", "/api/v1/runs/{run_id}/trace"),
    ("GET", "/api/v1/runs/{run_id}/steps"),
    ("GET", "/api/v1/runs/{run_id}/metrics"),
    ("GET", "/api/v1/runs/{run_id}/threads/{thread_id}"),
    ("GET", "/api/v1/runs/{run_id}/threads/{thread_id}/replay"),
    ("GET", "/api/v1/runs/{run_id}/threads/{thread_id}/metrics"),
    ("GET", "/api/v1/runs/{run_id}/subagents/{invocation_id}"),
    ("GET", "/api/v1/runs/{run_id}/recovery"),
    ("POST", "/api/v1/runs/{run_id}/cancel"),
    ("POST", "/api/v1/runs/{run_id}/reruns"),
    ("POST", "/api/v1/chat/threads"),
    ("GET", "/api/v1/chat/threads"),
    ("GET", "/api/v1/chat/threads/{thread_id}"),
    ("GET", "/api/v1/chat/threads/{thread_id}/preview"),
    ("PATCH", "/api/v1/chat/threads/{thread_id}"),
    ("POST", "/api/v1/chat/threads/{thread_id}/forks"),
    ("POST", "/api/v1/chat/threads/{thread_id}/archive"),
    ("POST", "/api/v1/chat/threads/{thread_id}/unarchive"),
    ("POST", "/api/v1/chat/threads/{thread_id}/compact"),
    ("POST", "/api/v1/chat/threads/{thread_id}/runs"),
    ("GET", "/api/v1/admin/domain-packs"),
    ("GET", "/api/v1/admin/domain-packs/{domain_id}"),
    ("GET", "/api/v1/admin/runs/{run_id}/approvals"),
    ("POST", "/api/v1/admin/runs/{run_id}/approvals"),
    ("POST", "/api/v1/admin/runs/{run_id}/approvals/{approval_id}/reject"),
    ("GET", "/api/v1/admin/memory"),
    ("POST", "/api/v1/admin/memory/expire"),
    ("POST", "/api/v1/admin/memory/{memory_id}/promote"),
    ("POST", "/api/v1/admin/memory/{memory_id}/revoke"),
    ("GET", "/api/v1/system/status"),
    ("POST", "/api/v1/system/doctor"),
    ("POST", "/api/v1/admin/system/checks"),
    ("POST", "/api/v1/admin/artifacts/cleanup"),
    ("POST", "/api/v1/admin/qdrant/reindex"),
    ("GET", "/api/v1/dashboard/{domain_id}/{view_id}"),
}


class ApiV1SseOpenApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.settings = Settings.from_env(Path(self.temp_dir.name))
        ensure_workspace_layout(self.settings)
        self.run_service = FakeRunService()
        self.app = create_app(
            settings=self.settings,
            api_config=ApiConfig(
                api_token=API_TOKEN,
                sse_poll_interval_seconds=0.001,
                sse_heartbeat_seconds=1.0,
            ),
            run_service_factory=FakeRunServiceFactory(self.run_service),
            repo_root=REPO_ROOT,
        )
        self.client_context = TestClient(self.app, raise_server_exceptions=False)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def write_terminal_run(
        self,
        *,
        run_id: str = RUN_ID,
        status: str = "completed",
        events: list[dict] | None = None,
    ) -> Path:
        root = self.settings.runs_root / run_id
        root.mkdir(parents=True, exist_ok=True)
        for event in events or []:
            append_jsonl(root / "execution_events.jsonl", event)
        write_json(
            root / "execution_snapshot.json",
            {"run_id": run_id, "status": status},
        )
        return root

    def test_sse_response_has_required_headers_and_raw_event_frame(self) -> None:
        event = run_event(event_type="graph_node_started").model_dump(mode="json")
        event["message"] = "采集进度 50%"
        self.write_terminal_run(events=[event])

        response = self.client.get(
            f"/api/v1/runs/{RUN_ID}/events/stream",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertEqual(response.headers["connection"], "keep-alive")
        expected_data = json.dumps(
            event,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        self.assertEqual(
            response.text,
            f"id: {EVENT_ID}\nevent: graph_node_started\ndata: {expected_data}\n\n",
        )

    def test_last_event_id_resumes_after_the_matching_event(self) -> None:
        first = run_event().model_dump(mode="json")
        second = run_event(
            event_id=SECOND_EVENT_ID,
            event_type="graph_run_finished",
        ).model_dump(mode="json")
        self.write_terminal_run(events=[first, second])

        response = self.client.get(
            f"/api/v1/runs/{RUN_ID}/events/stream",
            headers={**auth_headers(), "Last-Event-ID": EVENT_ID},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(EVENT_ID + "\n", response.text)
        self.assertIn(f"id: {SECOND_EVENT_ID}\n", response.text)
        self.assertIn("event: graph_run_finished\n", response.text)
        self.assertEqual(response.text.count("data: "), 1)

    def test_each_terminal_status_ends_an_empty_stream(self) -> None:
        terminal_statuses = (
            "completed",
            "cancelled",
            "failed",
            "gated",
        )
        for index, terminal_status in enumerate(terminal_statuses, start=10):
            run_id = f"01J000000000000000000000{index:02d}"
            self.write_terminal_run(run_id=run_id, status=terminal_status)
            with self.subTest(status=terminal_status):
                response = self.client.get(
                    f"/api/v1/runs/{run_id}/events/stream",
                    headers=auth_headers(),
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.text, "")

    def test_terminal_stream_drains_an_event_written_after_terminal_state(self) -> None:
        self.write_terminal_run()
        event = run_event(event_type="graph_run_finished").model_dump(mode="json")
        source = RunEventSource(
            self.settings,
            ApiConfig(
                api_token=API_TOKEN,
                sse_poll_interval_seconds=0.001,
                sse_heartbeat_seconds=1.0,
            ),
        )
        read_count = 0

        def delayed_terminal_event(_path: Path) -> list[dict]:
            nonlocal read_count
            read_count += 1
            return [] if read_count < 3 else [event]

        async def read_first_frame() -> str:
            stream = source.stream(RUN_ID)
            try:
                return await asyncio.wait_for(anext(stream), timeout=0.2)
            finally:
                await stream.aclose()

        with patch.object(source, "_read_events", side_effect=delayed_terminal_event), patch.object(
            source,
            "_is_terminal",
            return_value=True,
        ):
            frame = asyncio.run(read_first_frame())

        self.assertIn("event: graph_run_finished\n", frame)
        self.assertIn(f"id: {EVENT_ID}\n", frame)

    def test_graph_stream_waits_for_final_assistant_delivery(self) -> None:
        root = self.write_terminal_run(
            events=[run_event(event_type="graph_run_finished").model_dump(mode="json")]
        )
        (root / "artifact_board.jsonl").write_text("{}\n", encoding="utf-8")

        self.assertFalse(RunEventSource._is_terminal(root))

        final_event = run_event(
            event_id=SECOND_EVENT_ID,
            event_type="assistant_message_ready",
        ).model_dump(mode="json")
        final_event["delivery_status"] = "completed"
        append_jsonl(root / "execution_events.jsonl", final_event)

        self.assertTrue(RunEventSource._is_terminal(root))

    def test_running_stream_emits_configured_heartbeat(self) -> None:
        self.write_terminal_run(status="running")
        source = RunEventSource(
            self.settings,
            ApiConfig(
                api_token=API_TOKEN,
                sse_poll_interval_seconds=0.001,
                sse_heartbeat_seconds=0.001,
            ),
        )

        async def read_first_frame() -> str:
            stream = source.stream(RUN_ID)
            try:
                return await asyncio.wait_for(anext(stream), timeout=0.2)
            finally:
                await stream.aclose()

        self.assertEqual(asyncio.run(read_first_frame()), ": heartbeat\n\n")

    def test_missing_run_returns_404_before_opening_stream(self) -> None:
        response = self.client.get(
            f"/api/v1/runs/{MISSING_RUN_ID}/events/stream",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "resource_not_found")

    def test_unknown_last_event_id_returns_cursor_conflict(self) -> None:
        self.write_terminal_run(events=[run_event().model_dump(mode="json")])

        response = self.client.get(
            f"/api/v1/runs/{RUN_ID}/events/stream",
            headers={
                **auth_headers(),
                "Last-Event-ID": "01J00000000000000000000999",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "event_cursor_not_found")

    def test_openapi_operations_exactly_match_the_documented_routes(self) -> None:
        schema = self.app.openapi()
        actual_operations = {
            (method.upper(), path)
            for path, path_item in schema["paths"].items()
            for method in path_item
            if path.startswith("/api/v1/")
            and method.lower() in {"get", "post", "patch", "put", "delete"}
        }

        self.assertEqual(len(EXPECTED_API_V1_OPERATIONS), 43)
        self.assertEqual(actual_operations, EXPECTED_API_V1_OPERATIONS)
        self.assertEqual(len(actual_operations), 43)
        bearer = schema["components"]["securitySchemes"]["BearerAuth"]
        self.assertEqual(bearer["type"], "http")
        self.assertEqual(bearer["scheme"], "bearer")
        for method, path in EXPECTED_API_V1_OPERATIONS:
            operation = schema["paths"][path][method.lower()]
            self.assertIn({"BearerAuth": []}, operation.get("security") or [])
            parameter_names = {
                str(parameter.get("name") or "").lower()
                for parameter in operation.get("parameters") or []
            }
            self.assertNotIn("authorization", parameter_names)
        self.assertFalse(
            any("/api/v1/api/v1" in path for _method, path in actual_operations)
        )
        operation_ids = [
            operation["operationId"]
            for path_item in schema["paths"].values()
            for method, operation in path_item.items()
            if method.lower() in {"get", "post", "patch", "put", "delete"}
        ]
        self.assertEqual(len(operation_ids), len(set(operation_ids)))


if __name__ == "__main__":
    unittest.main()
