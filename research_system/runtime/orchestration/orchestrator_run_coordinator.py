from __future__ import annotations

from typing import Any

from research_system.runtime.orchestration.orchestrator_run_startup_support import (
    OrchestratorRunStartupSupport,
)
from research_system.runtime.orchestration.orchestrator_run_terminal_support import (
    OrchestratorRunTerminalSupport,
)
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


class OrchestratorRunCoordinator:
    """Coordinates the canonical Agent OS request lifecycle."""

    def __init__(self, host: Any) -> None:
        self.host = host
        # Kept as compatibility dependencies for callers that inspect the
        # assembled service graph. They are intentionally not used to execute
        # a request; ``run`` delegates to the canonical AgentOS entrypoint.
        self.startup_support = OrchestratorRunStartupSupport(host)
        self.terminal_support = OrchestratorRunTerminalSupport(host)

    def run(self, request: RequestEnvelope) -> ResponseEnvelope:
        """Compatibility facade for the one canonical request lifecycle.

        The former coordinator created a routing Turn and then a second
        Graph-owned Run.  ``ResearchOrchestrator.run`` now enters AgentOS and
        TurnRunner directly, so this compatibility object must not recreate a
        competing lifecycle.
        """
        return self.host.run(request)


__all__ = ["OrchestratorRunCoordinator"]
