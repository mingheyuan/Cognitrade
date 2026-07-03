from __future__ import annotations

from research_system.runtime.doctor_checks import build_doctor_payload
from research_system.runtime.settings import Settings
from research_system.runtime.status_runtime import build_runtime_status


def build_status(settings: Settings) -> dict:
    return build_runtime_status(settings)


def run_doctor(settings: Settings) -> dict:
    return build_doctor_payload(settings)
