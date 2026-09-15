"""Read-only diagnostics for the same frozen leaves native synthesis consumes.

This is not UZI's data/research quality review or an additional startup gate.
The rule-panel probe cannot promise that a later Agent-modified branch needs
no other response, so execution still validates its actual requested leaves.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

VERSION = "native-report-execution-dependencies-v2"


def report_execution_dependencies(
    item: Mapping[str, Any], *, profile: str, school: str | None = None,
) -> dict[str, Any]:
    """Never select newer evidence, fetch a provider, or call an analysis model."""
    result: dict[str, Any] = {
        "version": VERSION,
        "native_review": False,
        "assessment_scope": "pre_agent_rule_panel",
        "status": "unassessed",
        "can_execute_native_analysis": None,
        "reason_codes": [],
        "missing_requests": [],
        "missing_request_count": 0,
        "acquisition_policy": "explicit_data_center_task_only",
        "final_report_gate": "pending_agent_self_review",
    }
    context = item.get("context") or {}
    binding = context.get("native_quant_auxiliary")
    recording = item.get("native_quant_auxiliary_recording")
    if binding is None and recording is None:
        result.update(status="legacy_not_fixed", reason_codes=["native_quant_legacy_not_fixed"])
        return result
    if not item.get("symbol") or not item.get("snapshots"):
        result["reason_codes"] = ["frozen_input_structure_missing"]
        return result

    # Reuse the actual production computation and replay boundaries, not a
    # second copy of style selection, quant formulas, or source requirements.
    from domain_packs.finance_ashare.subagents import uzi_392_analysis as native
    from research_system.market_data.quant_auxiliary import QuantAuxiliaryInputError

    try:
        raw = native.build_uzi_raw(list(item["snapshots"]), str(item["symbol"]))
        prepared = native._prepare_native_computation(raw, profile=profile, school=school)
        diagnostics: dict[str, Any] = {}
        native._synthesize_native(
            prepared["raw_after_derivation"], prepared["dimensions"], prepared["panel"],
            agent_analysis=None, profile=profile, school=school,
            auxiliary_input={"binding": binding or {}, "recording": recording or {}},
            auxiliary_diagnostics=diagnostics,
        )
        result.update(status=diagnostics["status"], can_execute_native_analysis=True,
                      replayed_requests=diagnostics.get("replayed_requests", 0),
                      replay_policy=diagnostics.get("replay_policy"),
                      reason_codes=diagnostics.get("reason_codes", []),
                      missing_requests=diagnostics.get("missing_requests", []),
                      missing_request_count=diagnostics.get("missing_request_count", 0),
                      unavailable_responses=diagnostics.get("unavailable_responses", []),
                      report_blocking=False)
    except QuantAuxiliaryInputError as exc:
        result.update(can_execute_native_analysis=False, reason_codes=[exc.code],
                      missing_requests=exc.missing_requests,
                      missing_request_count=len(exc.missing_requests))
    except Exception as exc:  # noqa: BLE001 - non-authoritative native diagnostic boundary
        # Diagnostics must not turn malformed native inputs into a 500 for the
        # whole fund. Preserve the distinct computation failure without raw
        # values, exception text, or claiming it is a missing source response.
        result.update(status="probe_failed", reason_codes=["native_dependency_probe_failed"],
                      error_type=type(exc).__name__)
    return result
