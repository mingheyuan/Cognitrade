from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.adapters.vendors.mcp_source_registry import McpSourceRegistry
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


class McpSourceRegistryTests(unittest.TestCase):
    def test_registry_loads_servers_and_tools_from_workspace_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            settings.mcp_servers_path.write_text(
                "\n".join(
                    [
                        "servers:",
                        "  - id: mock_market_tools",
                        "    name: Mock Market Tools",
                        "    transport: stdio",
                        "    command: python",
                        "    args: ['-u', 'fake_server.py']",
                        "    timeout_seconds: 11.5",
                        "    tools:",
                        "      - name: market_signal_lookup",
                        "        summary: Lookup one market signal",
                        "        read_only: true",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            registry = McpSourceRegistry(settings=settings)

            server = registry.get("mock_market_tools")
            assert server is not None
            self.assertEqual(server.command, "python")
            self.assertEqual(server.timeout_seconds, 11.5)
            tool = registry.tool("mock_market_tools", "market_signal_lookup")
            self.assertIsNotNone(tool)
            assert tool is not None
            self.assertTrue(tool.read_only)
            self.assertEqual(tool.summary, "Lookup one market signal")


if __name__ == "__main__":
    unittest.main()
