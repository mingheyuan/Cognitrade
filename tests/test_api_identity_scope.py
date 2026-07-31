from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from research_system.api.app import create_app
from research_system.api.config import ApiConfig, ApiPrincipalBinding
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


REPO_ROOT = Path(__file__).resolve().parents[1]
API_TOKEN = "identity-scope-test-token"


class ApiIdentityScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings.from_env(REPO_ROOT).model_copy(
            update={
                "workspace_root": root,
                "runs_root": root / "runs",
                "memory_root": root / "memory",
                "domain_packs_root": REPO_ROOT / "domain_packs",
            }
        )
        ensure_workspace_layout(self.settings)
        self.app = create_app(
            settings=self.settings,
            api_config=ApiConfig(
                api_token=API_TOKEN,
                sse_poll_interval_seconds=0.001,
                sse_heartbeat_seconds=1.0,
            ),
            repo_root=REPO_ROOT,
        )
        self.client_context = TestClient(self.app, raise_server_exceptions=False)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    @staticmethod
    def headers(user_id: str, tenant_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {API_TOKEN}",
            "X-User-ID": user_id,
            "X-Tenant-ID": tenant_id,
        }

    def test_environment_principal_bindings_are_parsed_without_exposing_tokens(self) -> None:
        from research_system.api.config import ApiConfig

        with patch(
            "research_system.api.config.load_settings_env_values",
            return_value={
                "COGNITRADE_API_PRINCIPALS": (
                    '[{"token":"secret-a","actor_id":"actor-a",'
                    '"user_id":"user-a","tenant_id":"tenant-a","roles":["user"]}]'
                )
            },
        ):
            config = ApiConfig.from_env(REPO_ROOT)

        self.assertTrue(config.authentication_enabled)
        self.assertEqual(config.principal_bindings[0].actor_id, "actor-a")
        self.assertEqual(config.principal_bindings[0].roles, ("user",))
        self.assertNotIn("secret-a", repr(config.principal_bindings[0]))

    def create_core_run(self, user_id: str, tenant_id: str) -> str:
        response = self.client.post(
            "/api/v1/runs",
            headers=self.headers(user_id, tenant_id),
            json={"user_query": "Who are you?"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return str(response.json()["run_id"])

    def test_runs_sse_and_chat_threads_are_scoped_to_the_authenticated_identity(self) -> None:
        first_headers = self.headers("user-a", "tenant-a")
        second_headers = self.headers("user-b", "tenant-b")
        first_run = self.create_core_run("user-a", "tenant-a")
        second_run = self.create_core_run("user-b", "tenant-b")

        first_history = self.client.get("/api/v1/runs", headers=first_headers)
        second_history = self.client.get("/api/v1/runs", headers=second_headers)
        self.assertEqual(first_history.status_code, 200, first_history.text)
        self.assertEqual(second_history.status_code, 200, second_history.text)
        self.assertEqual([row["run_id"] for row in first_history.json()], [first_run])
        self.assertEqual([row["run_id"] for row in second_history.json()], [second_run])

        for path in (
            f"/api/v1/runs/{second_run}/replay",
            f"/api/v1/runs/{second_run}/events",
            f"/api/v1/runs/{second_run}/trace",
            f"/api/v1/runs/{second_run}/steps",
            f"/api/v1/runs/{second_run}/metrics",
            f"/api/v1/runs/{second_run}/events/stream",
            f"/api/v1/admin/runs/{second_run}/approvals",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, headers=first_headers)
                self.assertEqual(response.status_code, 404, response.text)

        first_thread = self.client.post(
            "/api/v1/chat/threads",
            headers=first_headers,
        )
        second_thread = self.client.post(
            "/api/v1/chat/threads",
            headers=second_headers,
        )
        self.assertEqual(first_thread.status_code, 201, first_thread.text)
        self.assertEqual(second_thread.status_code, 201, second_thread.text)
        first_thread_id = first_thread.json()["thread_id"]
        second_thread_id = second_thread.json()["thread_id"]

        first_threads = self.client.get("/api/v1/chat/threads?scope=all", headers=first_headers)
        second_threads = self.client.get("/api/v1/chat/threads?scope=all", headers=second_headers)
        self.assertEqual(
            [row["thread_id"] for row in first_threads.json()],
            [first_thread_id],
        )
        self.assertEqual(
            [row["thread_id"] for row in second_threads.json()],
            [second_thread_id],
        )
        cross_thread = self.client.get(
            f"/api/v1/chat/threads/{second_thread_id}",
            headers=first_headers,
        )
        self.assertEqual(cross_thread.status_code, 404, cross_thread.text)
        self.assertEqual(cross_thread.json()["error"]["code"], "chat_thread_not_found")

    def test_explicit_principal_binding_ignores_forged_identity_headers_and_enforces_roles(self) -> None:
        app = create_app(
            settings=self.settings,
            api_config=ApiConfig(
                api_token=None,
                principal_bindings=(
                    ApiPrincipalBinding(
                        token="bound-user-token",
                        actor_id="actor-a",
                        user_id="user-a",
                        tenant_id="tenant-a",
                        roles=("user",),
                    ),
                ),
            ),
            repo_root=REPO_ROOT,
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            headers = {"Authorization": "Bearer bound-user-token"}
            created = client.post(
                "/api/v1/runs",
                headers=headers,
                json={"user_query": "Who are you?"},
            )
            self.assertEqual(created.status_code, 200, created.text)
            run_id = created.json()["run_id"]
            replay = client.get(f"/api/v1/runs/{run_id}/replay", headers=headers)
            self.assertEqual(replay.status_code, 200, replay.text)
            persisted_request = replay.json()["request"]
            self.assertEqual(persisted_request["user_id"], "user-a")
            self.assertEqual(persisted_request["tenant_id"], "tenant-a")

            forged = client.get(
                f"/api/v1/runs/{run_id}/replay",
                headers={
                    **headers,
                    "X-User-ID": "attacker",
                    "X-Tenant-ID": "tenant-attacker",
                },
            )
            self.assertEqual(forged.status_code, 403, forged.text)
            self.assertEqual(forged.json()["error"]["code"], "identity_header_conflict")

            admin = client.post(
                "/api/v1/admin/artifacts/cleanup",
                headers=headers,
            )
            self.assertEqual(admin.status_code, 403, admin.text)
            self.assertEqual(admin.json()["error"]["code"], "insufficient_role")

    def test_explicit_principal_bindings_isolate_two_users_without_headers(self) -> None:
        app = create_app(
            settings=self.settings,
            api_config=ApiConfig(
                api_token=None,
                principal_bindings=(
                    ApiPrincipalBinding(
                        token="bound-a",
                        actor_id="actor-a",
                        user_id="user-a",
                        tenant_id="tenant-a",
                    ),
                    ApiPrincipalBinding(
                        token="bound-b",
                        actor_id="actor-b",
                        user_id="user-b",
                        tenant_id="tenant-b",
                    ),
                ),
            ),
            repo_root=REPO_ROOT,
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            headers_a = {"Authorization": "Bearer bound-a"}
            headers_b = {"Authorization": "Bearer bound-b"}
            run_a = client.post(
                "/api/v1/runs",
                headers=headers_a,
                json={"user_query": "Who are you?"},
            ).json()["run_id"]
            run_b = client.post(
                "/api/v1/runs",
                headers=headers_b,
                json={"user_query": "Who are you?"},
            ).json()["run_id"]
            self.assertEqual(
                [row["run_id"] for row in client.get("/api/v1/runs", headers=headers_a).json()],
                [run_a],
            )
            cross = client.get(f"/api/v1/runs/{run_b}/replay", headers=headers_a)
            self.assertEqual(cross.status_code, 404, cross.text)


if __name__ == "__main__":
    unittest.main()
