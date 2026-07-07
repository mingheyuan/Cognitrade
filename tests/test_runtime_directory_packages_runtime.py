from __future__ import annotations

import unittest

from research_system.runtime import core, diagnostics, presentation
from research_system.runtime.core.blackboard import Blackboard
from research_system.runtime.core.control_plane import RunControlPlane, RunControlState, utc_now
from research_system.runtime.core.event_stream import EventStream
from research_system.runtime.core.gates import GateEvaluator
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.core.logger import RunLogger
from research_system.runtime.core.mailbox import Mailbox
from research_system.runtime.diagnostics.doctoring.doctor import build_status, run_doctor
from research_system.runtime.diagnostics.doctoring.doctor_checks import build_doctor_checks, build_doctor_payload
from research_system.runtime.diagnostics.doctoring import build_doctor_checks as DoctoringBuildDoctorChecks
from research_system.runtime.diagnostics.doctoring import build_doctor_payload as DoctoringBuildDoctorPayload
from research_system.runtime.diagnostics.doctoring import build_status as DoctoringBuildStatus
from research_system.runtime.diagnostics.doctoring import run_doctor as DoctoringRunDoctor
from research_system.runtime.diagnostics import status as diagnostics_status
from research_system.runtime.diagnostics.status import ProviderHealthService, build_runtime_status
from research_system.runtime.presentation.display import build_display_summary
from research_system.runtime.presentation.presenter import (
    ChatPresenterLiveAccumulator as PresenterPackageLiveAccumulator,
)
from research_system.runtime.presentation.presenter import (
    ChatPresenterLivePayloads as PresenterPackageLivePayloads,
)
from research_system.runtime.presentation.presenter import (
    ChatPresenterPayloadState as PresenterPackagePayloadState,
)
from research_system.runtime.presentation.presenter import (
    ChatPresenterSummaries as PresenterPackageSummaries,
)
from research_system.runtime.presentation.presenter import (
    ChatStreamPresenter as PresenterPackageStreamPresenter,
)
from research_system.runtime.presentation.presenter import DisplayChunk as PresenterPackageDisplayChunk
from research_system.runtime.presentation.presenter.chat_presenter import ChatStreamPresenter, DisplayChunk
from research_system.runtime.presentation.presenter.chat_presenter_live_accumulator import (
    ChatPresenterLiveAccumulator,
)
from research_system.runtime.presentation.presenter.chat_presenter_live_payloads import (
    ChatPresenterLivePayloads,
)
from research_system.runtime.presentation.presenter.chat_presenter_payload_state import (
    ChatPresenterPayloadState,
)
from research_system.runtime.presentation.presenter.chat_presenter_summaries import (
    ChatPresenterSummaries,
)


class RuntimeDirectoryPackagesRuntimeTests(unittest.TestCase):
    def test_core_package_exports(self) -> None:
        self.assertIs(core.Blackboard, Blackboard)
        self.assertIs(core.RunControlPlane, RunControlPlane)
        self.assertIs(core.RunControlState, RunControlState)
        self.assertIs(core.utc_now, utc_now)
        self.assertIs(core.EventStream, EventStream)
        self.assertIs(core.GateEvaluator, GateEvaluator)
        self.assertIs(core.generate_ulid, generate_ulid)
        self.assertIs(core.RunLogger, RunLogger)
        self.assertIs(core.Mailbox, Mailbox)

    def test_diagnostics_package_exports(self) -> None:
        self.assertIs(diagnostics.ProviderHealthService, ProviderHealthService)
        self.assertIs(diagnostics.build_status, build_status)
        self.assertIs(diagnostics.run_doctor, run_doctor)
        self.assertIs(diagnostics.build_doctor_checks, build_doctor_checks)
        self.assertIs(diagnostics.build_doctor_payload, build_doctor_payload)
        self.assertIs(diagnostics.build_runtime_status, build_runtime_status)

    def test_diagnostics_doctoring_package_exports(self) -> None:
        self.assertIs(DoctoringBuildStatus, build_status)
        self.assertIs(DoctoringRunDoctor, run_doctor)
        self.assertIs(DoctoringBuildDoctorChecks, build_doctor_checks)
        self.assertIs(DoctoringBuildDoctorPayload, build_doctor_payload)

    def test_diagnostics_status_package_exports(self) -> None:
        self.assertIs(diagnostics_status.ProviderHealthService, ProviderHealthService)
        self.assertIs(diagnostics_status.build_runtime_status, build_runtime_status)

    def test_presentation_package_exports(self) -> None:
        self.assertIs(presentation.ChatStreamPresenter, ChatStreamPresenter)
        self.assertIs(presentation.DisplayChunk, DisplayChunk)
        self.assertIs(presentation.ChatPresenterLiveAccumulator, ChatPresenterLiveAccumulator)
        self.assertIs(presentation.ChatPresenterLivePayloads, ChatPresenterLivePayloads)
        self.assertIs(presentation.ChatPresenterPayloadState, ChatPresenterPayloadState)
        self.assertIs(presentation.ChatPresenterSummaries, ChatPresenterSummaries)
        self.assertIs(presentation.build_display_summary, build_display_summary)

    def test_presentation_presenter_package_exports(self) -> None:
        self.assertIs(PresenterPackageStreamPresenter, ChatStreamPresenter)
        self.assertIs(PresenterPackageDisplayChunk, DisplayChunk)
        self.assertIs(PresenterPackageLiveAccumulator, ChatPresenterLiveAccumulator)
        self.assertIs(PresenterPackageLivePayloads, ChatPresenterLivePayloads)
        self.assertIs(PresenterPackagePayloadState, ChatPresenterPayloadState)
        self.assertIs(PresenterPackageSummaries, ChatPresenterSummaries)


if __name__ == "__main__":
    unittest.main()
