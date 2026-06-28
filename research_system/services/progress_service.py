from __future__ import annotations

from research_system.schemas.protocol import ProgressState


class ProgressService:
    def build(
        self,
        *,
        current_phase: str | None,
        active_agent: str | None,
        latest_event: str | None,
        status_message: str,
        percent: int,
    ) -> ProgressState:
        return ProgressState(
            current_phase=current_phase,
            active_agent=active_agent,
            latest_event=latest_event,
            status_message=status_message,
            percent=percent,
        )
