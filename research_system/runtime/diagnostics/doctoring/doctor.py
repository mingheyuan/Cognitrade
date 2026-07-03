from __future__ import annotations

from research_system.runtime.diagnostics.doctoring.doctor_checks import build_doctor_payload
from research_system.runtime.diagnostics.status.status_runtime import build_runtime_status
from research_system.runtime.platform.settings import Settings


def build_status(settings: Settings) -> dict:
    return build_runtime_status(settings)


def run_doctor(settings: Settings) -> dict:
    return build_doctor_payload(settings)


__all__ = ["build_status", "run_doctor"]
