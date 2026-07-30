from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLI_ROOT = REPOSITORY_ROOT / "research_system" / "cli"

RETAINED_CLI_COMPAT_MODULES = {
    "research_system.cli.chat_shell_scene": "research_system.cli.view.chat_shell_scene",
    "research_system.cli.chat_thread_queries": "research_system.cli.memory.chat_thread_queries",
    "research_system.cli.chat_trace_events_blocks": "research_system.cli.view.chat_trace_events_blocks",
    "research_system.cli.chat_trace_events_linking": "research_system.cli.view.chat_trace_events_linking",
    "research_system.cli.chat_trace_events_rows": "research_system.cli.view.chat_trace_events_rows",
    "research_system.cli.chat_trace_events_support": "research_system.cli.view.chat_trace_events_support",
    "research_system.cli.chat_trace_scene_support": "research_system.cli.view.chat_trace_scene_support",
}

REMOVED_CLI_COMPAT_MODULES = {
    "research_system.cli.chat_shell_hint_support",
    "research_system.cli.chat_shell_layout_support",
    "research_system.cli.chat_shell_runtime",
    "research_system.cli.chat_shell_status_support",
    "research_system.cli.chat_thread_actions",
    "research_system.cli.chat_thread_context_support",
    "research_system.cli.chat_thread_keybindings",
    "research_system.cli.chat_thread_lifecycle",
    "research_system.cli.chat_thread_query_views",
    "research_system.cli.chat_thread_state",
    "research_system.cli.chat_thread_store_index_support",
    "research_system.cli.chat_thread_store_record_support",
    "research_system.cli.chat_thread_store_turn_support",
    "research_system.cli.chat_thread_summary_support",
    "research_system.cli.chat_threads_focus_support",
    "research_system.cli.chat_threads_scene",
    "research_system.cli.chat_threads_tree_support",
    "research_system.cli.chat_trace_block_support",
    "research_system.cli.chat_trace_branch_support",
    "research_system.cli.chat_trace_focus_support",
    "research_system.cli.chat_trace_grouping",
    "research_system.cli.chat_trace_output_scene",
    "research_system.cli.chat_trace_rendering_support",
    "research_system.cli.chat_transcript_scene",
    "research_system.cli.runtime.app_chat_entry_runtime",
    "research_system.cli.runtime.app_chat_runtime",
    "research_system.cli.runtime.app_cli_runtime",
}


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(REPOSITORY_ROOT).with_suffix("").parts)


def _repository_python_files() -> list[Path]:
    audit_file = Path(__file__).resolve()
    roots = [REPOSITORY_ROOT / name for name in ("research_system", "tests", "scripts")]
    return [
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*.py")
        if path.resolve() != audit_file
    ]


def _has_star_import(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return any(
        isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
        for node in tree.body
    )


class CliCompatibilityTests(unittest.TestCase):
    def test_retained_cli_compat_modules_reexport_the_canonical_module(self) -> None:
        for facade_name, canonical_name in RETAINED_CLI_COMPAT_MODULES.items():
            with self.subTest(facade=facade_name):
                facade = importlib.import_module(facade_name)
                canonical = importlib.import_module(canonical_name)
                facade_exports = {name for name in vars(facade) if not name.startswith("_")}
                canonical_exports = {name for name in vars(canonical) if not name.startswith("_")}

                self.assertEqual(facade_exports, canonical_exports)
                for name in canonical_exports:
                    self.assertIs(getattr(facade, name), getattr(canonical, name), name)

    def test_cli_has_no_star_compat_imports(self) -> None:
        actual = {
            _module_name(path)
            for path in CLI_ROOT.rglob("*.py")
            if _has_star_import(path)
        }

        self.assertEqual(actual, set())
        self.assertTrue(REMOVED_CLI_COMPAT_MODULES.isdisjoint(actual))

    def test_removed_cli_compat_modules_have_no_files(self) -> None:
        for module_name in REMOVED_CLI_COMPAT_MODULES:
            with self.subTest(module=module_name):
                path = REPOSITORY_ROOT / (module_name.replace(".", "/") + ".py")
                self.assertFalse(path.exists(), path)

    def test_removed_cli_compat_modules_are_not_importable(self) -> None:
        for module_name in REMOVED_CLI_COMPAT_MODULES:
            with self.subTest(module=module_name):
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module(module_name)

    def test_removed_cli_compat_modules_have_no_repository_references(self) -> None:
        references = {
            module_name: [
                path
                for path in _repository_python_files()
                if module_name in path.read_text(encoding="utf-8-sig")
            ]
            for module_name in REMOVED_CLI_COMPAT_MODULES
        }

        self.assertEqual(references, {module_name: [] for module_name in REMOVED_CLI_COMPAT_MODULES})


if __name__ == "__main__":
    unittest.main()
