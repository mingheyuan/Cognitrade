from __future__ import annotations

from research_system.runtime.orchestration.orchestrator import ResearchOrchestrator
from research_system.cli.flow.chat_research_runner import run_chat_research as flow_run_chat_research


def run_chat_research(*args, **kwargs) -> object:
    # Keep top-level `ResearchOrchestrator` patchable for tests.
    orchestrator_cls = kwargs.pop("orchestrator_cls", ResearchOrchestrator)
    return flow_run_chat_research(*args, orchestrator_cls=orchestrator_cls, **kwargs)
