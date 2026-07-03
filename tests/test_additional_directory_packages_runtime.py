from __future__ import annotations

import unittest

from research_system.cli import flow as flow_package
from research_system.cli import payloads as payloads_package
from research_system.cli import shell as shell_package
from research_system.cli.flow.chat_controller import handle_chat_line
from research_system.cli.flow.chat_dispatch import (
    answer_basic_chat,
    decide_chat_dispatch,
    print_streamed_basic_chat_answer,
)
from research_system.cli.flow.chat_event_adapter import build_chat_presenter, chat_event_listener
from research_system.cli.flow.chat_preprocessor import PreparedChatRunRequest, prepare_chat_run_request
from research_system.cli.flow.chat_research_runner import run_chat_research
from research_system.cli.flow.chat_result import print_chat_result_payload, stream_chat_rendered_result
from research_system.cli.payloads.payload_rendering import (
    emit_run_context_feed,
    emit_run_detail_fields,
    print_doctor_payload,
    print_events_payload,
    print_payload,
    print_proposal_payload,
    print_proposal_rows,
    print_replay_payload,
    print_rows,
    print_status_payload,
    print_steps_payload,
    print_subagent_payload,
    print_trace_payload,
)
from research_system.cli.shell.chat_fallback import run_chat_fallback
from research_system.cli.shell.chat_shell import run_chat_tui
from research_system.services import reporting as reporting_package
from research_system.services.reporting.reporting_utils import strategy_follow_up, summarize_proposal_drafts


class AdditionalDirectoryPackagesRuntimeTests(unittest.TestCase):
    def test_flow_package_exports(self) -> None:
        self.assertIs(flow_package.handle_chat_line, handle_chat_line)
        self.assertIs(flow_package.answer_basic_chat, answer_basic_chat)
        self.assertIs(flow_package.decide_chat_dispatch, decide_chat_dispatch)
        self.assertIs(flow_package.print_streamed_basic_chat_answer, print_streamed_basic_chat_answer)
        self.assertIs(flow_package.prepare_chat_run_request, prepare_chat_run_request)
        self.assertIs(flow_package.PreparedChatRunRequest, PreparedChatRunRequest)
        self.assertIs(flow_package.build_chat_presenter, build_chat_presenter)
        self.assertIs(flow_package.chat_event_listener, chat_event_listener)
        self.assertIs(flow_package.run_chat_research, run_chat_research)
        self.assertIs(flow_package.print_chat_result_payload, print_chat_result_payload)
        self.assertIs(flow_package.stream_chat_rendered_result, stream_chat_rendered_result)

    def test_payloads_package_exports(self) -> None:
        self.assertIs(payloads_package.emit_run_context_feed, emit_run_context_feed)
        self.assertIs(payloads_package.emit_run_detail_fields, emit_run_detail_fields)
        self.assertIs(payloads_package.print_payload, print_payload)
        self.assertIs(payloads_package.print_rows, print_rows)
        self.assertIs(payloads_package.print_proposal_rows, print_proposal_rows)
        self.assertIs(payloads_package.print_proposal_payload, print_proposal_payload)
        self.assertIs(payloads_package.print_status_payload, print_status_payload)
        self.assertIs(payloads_package.print_replay_payload, print_replay_payload)
        self.assertIs(payloads_package.print_trace_payload, print_trace_payload)
        self.assertIs(payloads_package.print_events_payload, print_events_payload)
        self.assertIs(payloads_package.print_steps_payload, print_steps_payload)
        self.assertIs(payloads_package.print_subagent_payload, print_subagent_payload)
        self.assertIs(payloads_package.print_doctor_payload, print_doctor_payload)

    def test_shell_package_exports(self) -> None:
        self.assertIs(shell_package.run_chat_tui, run_chat_tui)
        self.assertIs(shell_package.run_chat_fallback, run_chat_fallback)

    def test_reporting_package_exports(self) -> None:
        self.assertIs(reporting_package.strategy_follow_up, strategy_follow_up)
        self.assertIs(reporting_package.summarize_proposal_drafts, summarize_proposal_drafts)


if __name__ == "__main__":
    unittest.main()
