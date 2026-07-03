from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "emit_run_context_feed": ("research_system.cli.payloads.payload_rendering", "emit_run_context_feed"),
    "emit_run_detail_fields": ("research_system.cli.payloads.payload_rendering", "emit_run_detail_fields"),
    "print_payload": ("research_system.cli.payloads.payload_rendering", "print_payload"),
    "print_rows": ("research_system.cli.payloads.payload_rendering", "print_rows"),
    "print_proposal_rows": ("research_system.cli.payloads.payload_rendering", "print_proposal_rows"),
    "print_proposal_payload": ("research_system.cli.payloads.payload_rendering", "print_proposal_payload"),
    "print_status_payload": ("research_system.cli.payloads.payload_rendering", "print_status_payload"),
    "print_replay_payload": ("research_system.cli.payloads.payload_rendering", "print_replay_payload"),
    "print_trace_payload": ("research_system.cli.payloads.payload_rendering", "print_trace_payload"),
    "print_events_payload": ("research_system.cli.payloads.payload_rendering", "print_events_payload"),
    "print_steps_payload": ("research_system.cli.payloads.payload_rendering", "print_steps_payload"),
    "print_subagent_payload": ("research_system.cli.payloads.payload_rendering", "print_subagent_payload"),
    "print_doctor_payload": ("research_system.cli.payloads.payload_rendering", "print_doctor_payload"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
