from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from api_test_support import (
    API_TOKEN,
    FakeOrchestrator,
    FakeOrchestratorFactory,
    auth_headers,
)
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


REPO_ROOT = Path(__file__).resolve().parents[1]
MISSING_THREAD_ID = "thread_deadbeefcafe"


class ApiV1ChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.settings = Settings.from_env(Path(self.temp_dir.name))
        ensure_workspace_layout(self.settings)
        self.orchestrator = FakeOrchestrator()
        self.app = create_app(
            settings=self.settings,
            api_config=ApiConfig(api_token=API_TOKEN),
            orchestrator_factory=FakeOrchestratorFactory(self.orchestrator),
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

    def test_all_nine_chat_routes_follow_the_documented_lifecycle(self) -> None:
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
