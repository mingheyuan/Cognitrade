from __future__ import annotations

import unittest

from research_system.runtime.blackboard import Blackboard
from research_system.runtime.control_plane import RunControlPlane, RunControlState, utc_now
from research_system.runtime.core.blackboard import Blackboard as CoreBlackboard
from research_system.runtime.core.control_plane import (
    RunControlPlane as CoreRunControlPlane,
    RunControlState as CoreRunControlState,
    utc_now as core_utc_now,
)
from research_system.runtime.core.event_stream import EventStream as CoreEventStream
from research_system.runtime.core.gates import GateEvaluator as CoreGateEvaluator
from research_system.runtime.core.id_utils import generate_ulid as core_generate_ulid
from research_system.runtime.core.logger import RunLogger as CoreRunLogger
from research_system.runtime.core.mailbox import Mailbox as CoreMailbox
from research_system.runtime.event_stream import EventStream
from research_system.runtime.gates import GateEvaluator
from research_system.runtime.id_utils import generate_ulid
from research_system.runtime.logger import RunLogger
from research_system.runtime.mailbox import Mailbox


class CoreRuntimeTests(unittest.TestCase):
    def test_top_level_core_exports_remain_compatible_with_core_package(self) -> None:
        self.assertIs(Blackboard, CoreBlackboard)
        self.assertIs(RunControlPlane, CoreRunControlPlane)
        self.assertIs(RunControlState, CoreRunControlState)
        self.assertIs(utc_now, core_utc_now)
        self.assertIs(EventStream, CoreEventStream)
        self.assertIs(GateEvaluator, CoreGateEvaluator)
        self.assertIs(generate_ulid, core_generate_ulid)
        self.assertIs(RunLogger, CoreRunLogger)
        self.assertIs(Mailbox, CoreMailbox)


if __name__ == "__main__":
    unittest.main()
