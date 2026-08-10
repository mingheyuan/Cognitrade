from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class SettingsRuntimeTests(unittest.TestCase):
    def test_ensure_workspace_layout_seeds_templates_from_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template_root = root / "templates"
            for relative_path, content in {
                "workspace/config/provider_policy.yml": "provider: seeded\n",
                "workspace/config/data_sources.yml": "sources: seeded\n",
                "workspace/config/http_json_sources.yml": "sources: seeded-http-json\n",
                "workspace/config/mcp_servers.yml": "servers: seeded\n",
                "workspace/config/openapi_sources.yml": "sources: seeded-openapi\n",
                "workspace/config/skill_sources.yml": "sources: seeded-skill\n",
            }.items():
                path = template_root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            settings = Settings.from_env(root)
            ensure_workspace_layout(settings, template_root=template_root)

            self.assertTrue(settings.runs_root.is_dir())
            self.assertTrue(settings.reports_root.is_dir())
            self.assertTrue((settings.memory_root / "exports").is_dir())
            self.assertEqual(settings.mcp_servers_path.read_text(encoding="utf-8"), "servers: seeded\n")
            self.assertEqual(settings.http_json_sources_path.read_text(encoding="utf-8"), "sources: seeded-http-json\n")
            self.assertEqual(settings.openapi_sources_path.read_text(encoding="utf-8"), "sources: seeded-openapi\n")
            self.assertEqual(settings.skill_sources_path.read_text(encoding="utf-8"), "sources: seeded-skill\n")

    def test_settings_from_env_preserves_workspace_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "OPENAI_MODEL=test-model\n"
                "OPENAI_REVIEW_MODEL=test-review-model\n"
                "OPENAI_REASONING_EFFORT=xhigh\n"
                "OPENAI_SERVICE_TIER=priority\n"
                "OPENAI_DISABLE_RESPONSE_STORAGE=false\n"
                "UZI_LLM_REQUEST_TIMEOUT_SECONDS=360\n"
                "COGNITRADE_BAOSTOCK_SOCKET_TIMEOUT_SECONDS=9\n"
                "DEFAULT_TIMEZONE=UTC\n",
                encoding="utf-8",
            )

            settings = Settings.from_env(root)
            resolved_root = root.resolve()

            self.assertEqual(settings.default_model, "test-model")
            self.assertEqual(settings.review_model, "test-review-model")
            self.assertEqual(settings.openai_reasoning_effort, "xhigh")
            self.assertEqual(settings.openai_service_tier, "priority")
            self.assertFalse(settings.openai_disable_response_storage)
            self.assertEqual(settings.uzi_llm_request_timeout_seconds, 360)
            self.assertEqual(settings.baostock_socket_timeout_seconds, 9)
            self.assertEqual(settings.default_timezone, "UTC")
            self.assertEqual(settings.workspace_root, resolved_root / "workspace")
            self.assertEqual(settings.config_root, resolved_root / "workspace" / "config")


if __name__ == "__main__":
    unittest.main()
