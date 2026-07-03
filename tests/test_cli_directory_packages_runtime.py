from __future__ import annotations

import unittest

from research_system.cli import memory, rendering, view
from research_system.cli.memory.chat_memory import (
    build_chat_conversation_context,
    build_relevant_memory_context,
    current_thread_summary,
    ensure_session_thread,
    list_chat_threads,
    remember_basic_chat_turn,
    remember_chat_research_result,
    thread_preview_payload,
)
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.cli.rendering.chat_rendering import (
    apply_session_state_patch,
    apply_shell_state_patch,
    apply_tui_update,
    build_tui_hint_text,
    build_tui_status_text,
    commands_popup_text,
    has_shell_state_patch,
    refresh_tui,
    render_tui_output,
)
from research_system.cli.rendering.chat_rendering_output_refresh import refresh_output_area
from research_system.cli.view.chat_output_area import TranscriptOutputArea
from research_system.cli.view.chat_scene import (
    build_output_blocks,
    build_shell_scene_view,
    build_threads_blocks,
    build_tui_blocks,
)
from research_system.cli.view.chat_scene_models import CommandsPopupView, ShellSceneView, StatusBarItem, TranscriptRenderBlock
from research_system.cli.view.chat_trace_scene import build_trace_blocks
from research_system.cli.view.chat_view import build_chat_application, build_chat_view, sync_chat_view_from_scene
from research_system.cli.view.chat_view_models import ChatViewComponents


class CliDirectoryPackagesRuntimeTests(unittest.TestCase):
    def test_view_package_exports(self) -> None:
        self.assertIs(view.ChatViewComponents, ChatViewComponents)
        self.assertIs(view.TranscriptOutputArea, TranscriptOutputArea)
        self.assertIs(view.TranscriptRenderBlock, TranscriptRenderBlock)
        self.assertIs(view.StatusBarItem, StatusBarItem)
        self.assertIs(view.CommandsPopupView, CommandsPopupView)
        self.assertIs(view.ShellSceneView, ShellSceneView)
        self.assertIs(view.build_chat_view, build_chat_view)
        self.assertIs(view.build_chat_application, build_chat_application)
        self.assertIs(view.build_output_blocks, build_output_blocks)
        self.assertIs(view.build_threads_blocks, build_threads_blocks)
        self.assertIs(view.build_trace_blocks, build_trace_blocks)
        self.assertIs(view.build_shell_scene_view, build_shell_scene_view)
        self.assertIs(view.build_tui_blocks, build_tui_blocks)
        self.assertIs(view.sync_chat_view_from_scene, sync_chat_view_from_scene)

    def test_rendering_package_exports(self) -> None:
        self.assertIs(rendering.commands_popup_text, commands_popup_text)
        self.assertIs(rendering.render_tui_output, render_tui_output)
        self.assertIs(rendering.build_tui_status_text, build_tui_status_text)
        self.assertIs(rendering.build_tui_hint_text, build_tui_hint_text)
        self.assertIs(rendering.refresh_tui, refresh_tui)
        self.assertIs(rendering.apply_tui_update, apply_tui_update)
        self.assertIs(rendering.apply_shell_state_patch, apply_shell_state_patch)
        self.assertIs(rendering.apply_session_state_patch, apply_session_state_patch)
        self.assertIs(rendering.has_shell_state_patch, has_shell_state_patch)
        self.assertIs(rendering.refresh_output_area, refresh_output_area)

    def test_memory_package_exports(self) -> None:
        self.assertIs(memory.ChatThreadStore, ChatThreadStore)
        self.assertIs(memory.ensure_session_thread, ensure_session_thread)
        self.assertIs(memory.list_chat_threads, list_chat_threads)
        self.assertIs(memory.build_chat_conversation_context, build_chat_conversation_context)
        self.assertIs(memory.build_relevant_memory_context, build_relevant_memory_context)
        self.assertIs(memory.current_thread_summary, current_thread_summary)
        self.assertIs(memory.thread_preview_payload, thread_preview_payload)
        self.assertIs(memory.remember_basic_chat_turn, remember_basic_chat_turn)
        self.assertIs(memory.remember_chat_research_result, remember_chat_research_result)


if __name__ == "__main__":
    unittest.main()
