from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from api_test_support import (
    API_TOKEN,
    FakeRunService,
    FakeRunServiceFactory,
    auth_headers,
)
from research_system.api.app import create_app
from research_system.api.chat_store import ChatThreadStore
from research_system.api.config import ApiConfig
from research_system.api.chat_service import ChatService
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import ProgressState, ResponseEnvelope


REPO_ROOT = Path(__file__).resolve().parents[1]
MISSING_THREAD_ID = "thread_deadbeefcafe"


class ApiV1ChatTests(unittest.TestCase):
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

    def create_thread(self) -> str:
        response = self.client.post("/api/v1/chat/threads", headers=auth_headers())
        self.assertEqual(response.status_code, 201)
        thread_id = str(response.json().get("thread_id") or "")
        self.assertRegex(thread_id, r"^thread_[0-9a-f]{12}$")
        return thread_id

    def assert_not_found(self, response) -> None:
        self.assertEqual(response.status_code, 404)
        error = response.json()["error"]
        self.assertEqual(error["code"], "chat_thread_not_found")

    def test_thread_lifecycle_routes_follow_the_documented_lifecycle(self) -> None:
        thread_id = self.create_thread()

        listed = self.client.get("/api/v1/chat/threads", headers=auth_headers())
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([row["thread_id"] for row in listed.json()], [thread_id])

        detail = self.client.get(
            f"/api/v1/chat/threads/{thread_id}",
            headers=auth_headers(),
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["thread_id"], thread_id)
        self.assertEqual(detail.json()["turns"], [])

        preview = self.client.get(
            f"/api/v1/chat/threads/{thread_id}/preview?turn_limit=3",
            headers=auth_headers(),
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["thread_id"], thread_id)
        self.assertEqual(preview.json()["recent_turns"], [])

        renamed = self.client.patch(
            f"/api/v1/chat/threads/{thread_id}",
            headers=auth_headers(),
            json={"thread_label": "  医药板块   长期跟踪  "},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["thread_label"], "医药板块 长期跟踪")

        forked = self.client.post(
            f"/api/v1/chat/threads/{thread_id}/forks",
            headers=auth_headers(),
        )
        self.assertEqual(forked.status_code, 201)
        child_thread_id = forked.json()["thread_id"]
        self.assertNotEqual(child_thread_id, thread_id)
        self.assertEqual(forked.json()["parent_thread_id"], thread_id)

        archived = self.client.post(
            f"/api/v1/chat/threads/{thread_id}/archive",
            headers=auth_headers(),
        )
        self.assertEqual(archived.status_code, 200)
        self.assertIsNone(archived.json()["next_thread_id"])
        archived_rows = self.client.get(
            "/api/v1/chat/threads?scope=archived",
            headers=auth_headers(),
        )
        self.assertEqual(
            [row["thread_id"] for row in archived_rows.json()],
            [thread_id],
        )

        unarchived = self.client.post(
            f"/api/v1/chat/threads/{thread_id}/unarchive",
            headers=auth_headers(),
        )
        self.assertEqual(unarchived.status_code, 200)
        self.assertEqual(unarchived.json()["thread_id"], thread_id)

        compacted = self.client.post(
            f"/api/v1/chat/threads/{thread_id}/compact",
            headers=auth_headers(),
        )
        self.assertEqual(compacted.status_code, 200)
        self.assertEqual(compacted.json()["thread_id"], thread_id)
        self.assertEqual(compacted.json()["source_turn_count"], 0)

        all_rows = self.client.get(
            "/api/v1/chat/threads?scope=all",
            headers=auth_headers(),
        )
        self.assertEqual(len(all_rows.json()), 2)
        self.assertEqual(
            {row["thread_id"] for row in all_rows.json()},
            {thread_id, child_thread_id},
        )

    def test_missing_thread_operations_return_404_without_creating_it(self) -> None:
        cases = (
            ("GET", f"/api/v1/chat/threads/{MISSING_THREAD_ID}", None),
            ("GET", f"/api/v1/chat/threads/{MISSING_THREAD_ID}/preview", None),
            (
                "PATCH",
                f"/api/v1/chat/threads/{MISSING_THREAD_ID}",
                {"thread_label": "must not be created"},
            ),
            ("POST", f"/api/v1/chat/threads/{MISSING_THREAD_ID}/forks", None),
            ("POST", f"/api/v1/chat/threads/{MISSING_THREAD_ID}/archive", None),
            ("POST", f"/api/v1/chat/threads/{MISSING_THREAD_ID}/unarchive", None),
            ("POST", f"/api/v1/chat/threads/{MISSING_THREAD_ID}/compact", None),
            (
                "POST",
                f"/api/v1/chat/threads/{MISSING_THREAD_ID}/runs",
                {
                    "intent": "stock_check",
                    "user_query": "Review 600000.",
                    "constraints": {"research_profile": "lite"},
                },
            ),
        )

        for method, path, body in cases:
            with self.subTest(method=method, path=path):
                response = self.client.request(
                    method,
                    path,
                    headers=auth_headers(),
                    json=body,
                )
                self.assert_not_found(response)

        listed = self.client.get(
            "/api/v1/chat/threads?scope=all",
            headers=auth_headers(),
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), [])
        self.assertFalse(
            ChatThreadStore(self.settings).has_thread(thread_id=MISSING_THREAD_ID)
        )

    def test_thread_research_runs_through_the_agent_os_run_service(self) -> None:
        thread_id = self.create_thread()

        response = self.client.post(
            f"/api/v1/chat/threads/{thread_id}/runs",
            headers=auth_headers(),
            json={
                "intent": "stock_check",
                "user_query": "Review 600000 with supplied evidence.",
                "constraints": {"research_profile": "lite"},
                "slots": {"symbols": ["600000"]},
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["run_id"], "01J00000000000000000000002")
        call = self.run_service.calls_for("run")[-1]
        request = call.args[0]
        self.assertEqual(request.intent, "stock_check")
        self.assertEqual(request.slots["chat_thread_id"], thread_id)
        turns = ChatThreadStore(self.settings).read_all_turns(thread_id=thread_id)
        self.assertEqual([turn["turn_type"] for turn in turns], ["research_query", "research_result"])
        self.assertEqual([turn["run_id"] for turn in turns], [response.json()["run_id"], response.json()["run_id"]])

    def test_background_completion_is_appended_to_the_same_chat_thread_once(self) -> None:
        thread_id = self.create_thread()
        service = ChatService(self.settings)
        response = ResponseEnvelope(
            request_id="request-background-complete",
            run_id="run-background-complete",
            mode="graph",
            intent="stock_check",
            status="completed",
            message="Final research result.",
            progress=ProgressState(
                current_phase="execution_graph",
                active_agent="main_agent",
                latest_event="graph_run_finished",
                status_message="completed",
                percent=100,
            ),
        )

        service.record_run_completion(thread_id=thread_id, response=response)
        service.record_run_completion(thread_id=thread_id, response=response)

        turns = ChatThreadStore(self.settings).read_all_turns(thread_id=thread_id)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["content"], "Final research result.")
        self.assertTrue(turns[0]["metadata"]["background_completed"])

    def test_archive_does_not_create_an_implicit_replacement_thread(self) -> None:
        thread_id = self.create_thread()

        response = self.client.post(
            f"/api/v1/chat/threads/{thread_id}/archive",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["next_thread_id"])
        rows = self.client.get(
            "/api/v1/chat/threads?scope=all",
            headers=auth_headers(),
        ).json()
        self.assertEqual([row["thread_id"] for row in rows], [thread_id])

    def test_compact_archives_old_turns_and_retains_recent_context(self) -> None:
        thread_id = self.create_thread()
        store = ChatThreadStore(self.settings)
        for index in range(10):
            store.append_turn(
                thread_id=thread_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"turn {index + 1}",
                turn_type="basic_chat",
                recorded_at=f"2026-07-10T08:{index:02d}:00+00:00",
            )

        compacted = self.client.post(
            f"/api/v1/chat/threads/{thread_id}/compact",
            headers=auth_headers(),
        )

        self.assertEqual(compacted.status_code, 200)
        self.assertEqual(compacted.json()["source_turn_count"], 10)
        self.assertEqual(compacted.json()["covered_turn_count"], 10)
        self.assertEqual(len(store.read_all_turns(thread_id=thread_id)), 8)
        self.assertEqual(len(store.read_archived_turns(thread_id=thread_id)), 2)
        detail = self.client.get(
            f"/api/v1/chat/threads/{thread_id}",
            headers=auth_headers(),
        ).json()
        self.assertEqual(detail["turn_count"], 8)
        self.assertEqual(detail["lifetime_turn_count"], 10)
        self.assertEqual(len(detail["turns"]), 10)

    def test_chat_request_validation_uses_error_envelopes(self) -> None:
        thread_id = self.create_thread()
        cases = (
            ("GET", "/api/v1/chat/threads?limit=0", None, 422),
            ("GET", "/api/v1/chat/threads?limit=101", None, 422),
            ("GET", "/api/v1/chat/threads?scope=unknown", None, 422),
            (
                "GET",
                f"/api/v1/chat/threads/{thread_id}/preview?turn_limit=0",
                None,
                422,
            ),
            (
                "GET",
                f"/api/v1/chat/threads/{thread_id}/preview?turn_limit=21",
                None,
                422,
            ),
            (
                "PATCH",
                f"/api/v1/chat/threads/{thread_id}",
                {"thread_label": "   "},
                422,
            ),
            (
                "PATCH",
                f"/api/v1/chat/threads/{thread_id}",
                {"thread_label": "x", "unknown": True},
                422,
            ),
            (
                "GET",
                "/api/v1/chat/threads/%2E%2E%5Cworkspace",
                None,
                400,
            ),
        )

        for method, path, body, expected_status in cases:
            with self.subTest(method=method, path=path):
                response = self.client.request(
                    method,
                    path,
                    headers=auth_headers(),
                    json=body,
                )
                self.assertEqual(response.status_code, expected_status)
                self.assertIn("error", response.json())
                self.assertTrue(response.json()["error"]["code"])


if __name__ == "__main__":
    unittest.main()
