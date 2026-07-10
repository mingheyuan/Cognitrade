from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class SettingsRuntimeTests(unittest.TestCase):
    def test_load_yaml_configs_returns_known_keys_and_missing_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            settings.config_root.mkdir(parents=True, exist_ok=True)
            settings.taxonomy_root.mkdir(parents=True, exist_ok=True)
            settings.retrieval_policy_path.write_text("mode: hybrid\n", encoding="utf-8")
            settings.taxonomy_dimensions_path.write_text("sector:\n  aliases: []\n", encoding="utf-8")

            configs = settings.load_yaml_configs()

            self.assertEqual(configs["retrieval_policy"], {"mode": "hybrid"})
            self.assertEqual(configs["taxonomy_dimensions"], {"sector": {"aliases": []}})
            self.assertEqual(configs["review_policy"], {})
            self.assertEqual(
                set(configs),
                {
                    "retrieval_policy",
                    "methodology_manifest",
                    "router_defaults",
                    "subagent_discovery_policy",
                    "subagent_discovery_approval_policy",
                    "subagent_discovery_activation_policy",
                    "provider_policy",
                    "data_sources",
                    "http_json_sources",
                    "mcp_servers",
                    "openapi_sources",
                    "skill_sources",
                    "taxonomy_dimensions",
                    "taxonomy_dimension_aliases",
                    "taxonomy_dimension_patterns",
                    "review_policy",
                },
            )

    def test_ensure_workspace_layout_seeds_templates_from_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template_root = root / "templates"
            for relative_path, content in {
                "workspace/config/retrieval_policy.yml": "retrieval: seeded\n",
                "workspace/config/methodology_manifest.yml": "manifest: seeded\n",
                "workspace/config/review_policy.yml": "review: seeded\n",
                "workspace/config/router_defaults.yml": "router: seeded\n",
                "workspace/config/subagent_discovery_policy.yml": "defaults: seeded-discovery\n",
                "workspace/config/subagent_discovery_approval_policy.yml": "defaults: seeded-discovery-approval\n",
                "workspace/config/subagent_discovery_activation_policy.yml": "defaults: seeded-discovery-activation\n",
                "workspace/config/provider_policy.yml": "provider: seeded\n",
                "workspace/config/data_sources.yml": "sources: seeded\n",
                "workspace/config/http_json_sources.yml": "sources: seeded-http-json\n",
                "workspace/config/mcp_servers.yml": "servers: seeded\n",
                "workspace/config/openapi_sources.yml": "sources: seeded-openapi\n",
                "workspace/config/skill_sources.yml": "sources: seeded-skill\n",
                "workspace/taxonomy/dimensions.yml": "dimensions: seeded\n",
                "workspace/taxonomy/dimension_aliases.yml": "aliases: seeded\n",
                "workspace/taxonomy/dimension_patterns.yml": "patterns: seeded\n",
            }.items():
                path = template_root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            settings = Settings.from_env(root)
            ensure_workspace_layout(settings, template_root=template_root)

            self.assertTrue((settings.proposals_root / "pending").is_dir())
            self.assertTrue((settings.proposals_root / "approved").is_dir())
            self.assertTrue((settings.proposals_root / "rejected").is_dir())
            self.assertEqual(settings.retrieval_policy_path.read_text(encoding="utf-8"), "retrieval: seeded\n")
            self.assertEqual(
                settings.subagent_discovery_policy_path.read_text(encoding="utf-8"),
                "defaults: seeded-discovery\n",
            )
            self.assertEqual(
                settings.subagent_discovery_approval_policy_path.read_text(encoding="utf-8"),
                "defaults: seeded-discovery-approval\n",
            )
            self.assertEqual(
                settings.subagent_discovery_activation_policy_path.read_text(encoding="utf-8"),
                "defaults: seeded-discovery-activation\n",
            )
            self.assertEqual(settings.mcp_servers_path.read_text(encoding="utf-8"), "servers: seeded\n")
            self.assertEqual(settings.http_json_sources_path.read_text(encoding="utf-8"), "sources: seeded-http-json\n")
            self.assertEqual(settings.openapi_sources_path.read_text(encoding="utf-8"), "sources: seeded-openapi\n")
            self.assertEqual(settings.skill_sources_path.read_text(encoding="utf-8"), "sources: seeded-skill\n")
            self.assertEqual(settings.taxonomy_dimension_patterns_path.read_text(encoding="utf-8"), "patterns: seeded\n")

    def test_settings_from_env_preserves_workspace_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("OPENAI_MODEL=test-model\nDEFAULT_TIMEZONE=UTC\n", encoding="utf-8")

            settings = Settings.from_env(root)

            self.assertEqual(settings.default_model, "test-model")
            self.assertEqual(settings.default_timezone, "UTC")
            self.assertEqual(settings.workspace_root, root / "workspace")
            self.assertEqual(settings.config_root, root / "workspace" / "config")
            self.assertEqual(settings.taxonomy_root, root / "workspace" / "taxonomy")


if __name__ == "__main__":
    unittest.main()
