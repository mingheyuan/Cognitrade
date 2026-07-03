from __future__ import annotations

import unittest

from research_system.cli import commands, runtime, state
from research_system.cli.commands.chat_command_catalog import CHAT_COMMAND_SUMMARY_LINES, CHAT_HELP_LINES, print_chat_help
from research_system.cli.commands.chat_command_completion_support import CHAT_COMMANDS, ChatCommandCompleter
from research_system.cli.commands.chat_command_parse_support import parse_chat_input
from research_system.cli.commands.chat_pending_clarification_support import clear_pending_clarification
from research_system.cli.commands.cli_command_dispatch import execute_cli_payload_command
from research_system.cli.commands.cli_command_payloads import (
    PROPOSAL_COMMANDS,
    RUN_ID_COMMANDS,
    STATUS_FILTER_COMMANDS,
    SUBAGENT_COMMANDS,
    build_cli_command_payload,
)
from research_system.cli.commands.research_request_payloads import build_research_request
from research_system.cli.runtime import app as runtime_app
from research_system.cli.runtime.app import (
    ChatAppRuntime as AppPackageChatAppRuntime,
    ChatEntryRuntime as AppPackageChatEntryRuntime,
    CliCommandRuntime as AppPackageCliCommandRuntime,
)
from research_system.cli.runtime.app_chat_entry_runtime import ChatEntryRuntime, build_chat_entry_runtime, run_chat_entry
from research_system.cli.runtime.app_chat_runtime import ChatAppRuntime, build_chat_app_runtime, handle_chat_line, print_cli_payload
from research_system.cli.runtime.app_cli_runtime import CliCommandRuntime, build_cli_command_runtime, execute_cli_command
from research_system.cli.runtime.chat_action_runtime import ChatActionRuntime, build_chat_action_runtime
from research_system.cli.runtime.chat_actions import handle_chat_cli_action, handle_chat_meta_action
from research_system.cli.runtime.chat_fallback_runtime import ChatFallbackRuntime, build_chat_fallback_runtime
from research_system.cli.runtime.chat_flow_runtime import ChatQueryFlowRuntime, build_chat_query_flow_runtime
from research_system.cli.runtime.chat_line_dispatcher import ChatLineDispatcherRuntime, build_chat_line_dispatcher_runtime, dispatch_chat_line
from research_system.cli.runtime.chat_session_bootstrap import ChatRuntimeContext, build_chat_startup_context
from research_system.cli.runtime.chat_shell_runtime import ChatShellRuntime, build_chat_shell_runtime
from research_system.cli.runtime.chat_ui_runtime_core import ChatUiRuntime, build_chat_ui_runtime
from research_system.cli.state.chat_state import (
    apply_session_state_patch_to_state,
    build_research_result_session_patch,
    clear_recent_session_patch,
    has_session_state_patch,
    persist_thread_checkpoint,
)
from research_system.cli.state.chat_state_models import ChatSessionState, ShellUiState, ShellViewState
from research_system.cli.state.chat_tui_session import TuiSession
from research_system.cli.state.chat_update_dispatch import dispatch_transcript_update
from research_system.cli.state.chat_update_factories import append_update, default_update_for_text, warning_update
from research_system.cli.state.chat_update_models import SESSION_PATCH_UNSET, SessionStatePatch, ShellStatePatch, TranscriptUpdate


class CliDomainPackagesRuntimeTests(unittest.TestCase):
    def test_commands_package_exports(self) -> None:
        self.assertIs(commands.CHAT_COMMANDS, CHAT_COMMANDS)
        self.assertIs(commands.CHAT_COMMAND_SUMMARY_LINES, CHAT_COMMAND_SUMMARY_LINES)
        self.assertIs(commands.CHAT_HELP_LINES, CHAT_HELP_LINES)
        self.assertIs(commands.PROPOSAL_COMMANDS, PROPOSAL_COMMANDS)
        self.assertIs(commands.RUN_ID_COMMANDS, RUN_ID_COMMANDS)
        self.assertIs(commands.STATUS_FILTER_COMMANDS, STATUS_FILTER_COMMANDS)
        self.assertIs(commands.SUBAGENT_COMMANDS, SUBAGENT_COMMANDS)
        self.assertIs(commands.ChatCommandCompleter, ChatCommandCompleter)
        self.assertIs(commands.parse_chat_input, parse_chat_input)
        self.assertIs(commands.print_chat_help, print_chat_help)
        self.assertIs(commands.clear_pending_clarification, clear_pending_clarification)
        self.assertIs(commands.build_cli_command_payload, build_cli_command_payload)
        self.assertIs(commands.build_research_request, build_research_request)
        self.assertIs(commands.execute_cli_payload_command, execute_cli_payload_command)

    def test_state_package_exports(self) -> None:
        self.assertIs(state.ChatSessionState, ChatSessionState)
        self.assertIs(state.ShellViewState, ShellViewState)
        self.assertIs(state.ShellUiState, ShellUiState)
        self.assertIs(state.TuiSession, TuiSession)
        self.assertIs(state.TranscriptUpdate, TranscriptUpdate)
        self.assertIs(state.ShellStatePatch, ShellStatePatch)
        self.assertIs(state.SessionStatePatch, SessionStatePatch)
        self.assertIs(state.SESSION_PATCH_UNSET, SESSION_PATCH_UNSET)
        self.assertIs(state.append_update, append_update)
        self.assertIs(state.warning_update, warning_update)
        self.assertIs(state.default_update_for_text, default_update_for_text)
        self.assertIs(state.dispatch_transcript_update, dispatch_transcript_update)
        self.assertIs(state.apply_session_state_patch_to_state, apply_session_state_patch_to_state)
        self.assertIs(state.build_research_result_session_patch, build_research_result_session_patch)
        self.assertIs(state.clear_recent_session_patch, clear_recent_session_patch)
        self.assertIs(state.persist_thread_checkpoint, persist_thread_checkpoint)
        self.assertIs(state.has_session_state_patch, has_session_state_patch)

    def test_runtime_package_exports(self) -> None:
        self.assertIs(runtime.CliCommandRuntime, CliCommandRuntime)
        self.assertIs(runtime.ChatAppRuntime, ChatAppRuntime)
        self.assertIs(runtime.ChatEntryRuntime, ChatEntryRuntime)
        self.assertIs(runtime.ChatActionRuntime, ChatActionRuntime)
        self.assertIs(runtime.ChatFallbackRuntime, ChatFallbackRuntime)
        self.assertIs(runtime.ChatQueryFlowRuntime, ChatQueryFlowRuntime)
        self.assertIs(runtime.ChatLineDispatcherRuntime, ChatLineDispatcherRuntime)
        self.assertIs(runtime.ChatRuntimeContext, ChatRuntimeContext)
        self.assertIs(runtime.ChatShellRuntime, ChatShellRuntime)
        self.assertIs(runtime.ChatUiRuntime, ChatUiRuntime)
        self.assertIs(runtime.build_cli_command_runtime, build_cli_command_runtime)
        self.assertIs(runtime.execute_cli_command, execute_cli_command)
        self.assertIs(runtime.build_chat_app_runtime, build_chat_app_runtime)
        self.assertIs(runtime.handle_chat_line, handle_chat_line)
        self.assertIs(runtime.print_cli_payload, print_cli_payload)
        self.assertIs(runtime.build_chat_entry_runtime, build_chat_entry_runtime)
        self.assertIs(runtime.run_chat_entry, run_chat_entry)
        self.assertIs(runtime.build_chat_action_runtime, build_chat_action_runtime)
        self.assertIs(runtime.handle_chat_cli_action, handle_chat_cli_action)
        self.assertIs(runtime.handle_chat_meta_action, handle_chat_meta_action)
        self.assertIs(runtime.build_chat_fallback_runtime, build_chat_fallback_runtime)
        self.assertIs(runtime.build_chat_query_flow_runtime, build_chat_query_flow_runtime)
        self.assertIs(runtime.build_chat_line_dispatcher_runtime, build_chat_line_dispatcher_runtime)
        self.assertIs(runtime.dispatch_chat_line, dispatch_chat_line)
        self.assertIs(runtime.build_chat_startup_context, build_chat_startup_context)
        self.assertIs(runtime.build_chat_shell_runtime, build_chat_shell_runtime)
        self.assertIs(runtime.build_chat_ui_runtime, build_chat_ui_runtime)

    def test_runtime_app_package_exports(self) -> None:
        self.assertIs(runtime_app.ChatAppRuntime, AppPackageChatAppRuntime)
        self.assertIs(runtime_app.ChatEntryRuntime, AppPackageChatEntryRuntime)
        self.assertIs(runtime_app.CliCommandRuntime, AppPackageCliCommandRuntime)
        self.assertIs(runtime_app.ChatAppRuntime, ChatAppRuntime)
        self.assertIs(runtime_app.ChatEntryRuntime, ChatEntryRuntime)
        self.assertIs(runtime_app.CliCommandRuntime, CliCommandRuntime)


if __name__ == "__main__":
    unittest.main()
