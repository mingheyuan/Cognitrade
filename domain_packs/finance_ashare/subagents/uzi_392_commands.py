"""Auditable command-to-Agent-OS map for the pinned UZI 3.9.2 command set."""

from __future__ import annotations

from typing import Any

from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import UZI_COMMAND_IDS


UZI_392_COMMAND_MAP: dict[str, dict[str, Any]] = {
    "analyze-stock": {"intent": "uzi_analyze_stock", "capability": "finance.uzi.native.analyze_stock", "status": "source_parity_strict_gate"},
    "quick-scan": {"intent": "quick_scan", "capability": "finance.uzi.quick_scan.compose", "status": "source_parity"},
    "panel-only": {"intent": "uzi_panel_only", "capability": "finance.uzi.command.panel_only", "status": "source_parity"},
    "scan-trap": {"intent": "uzi_scan_trap", "capability": "finance.uzi.command.scan_trap", "status": "source_parity_no_default_safe"},
    "dcf": {"intent": "uzi_dcf", "capability": "finance.uzi.native.dcf", "method_id": "dcf", "status": "source_parity"},
    "comps": {"intent": "uzi_comps", "capability": "finance.uzi.native.comps", "method_id": "comps", "status": "source_parity"},
    "lbo": {"intent": "uzi_lbo", "capability": "finance.uzi.native.quick_lbo", "method_id": "quick_lbo", "status": "source_parity"},
    "segmental-model": {"intent": "uzi_segmental_model", "capability": "finance.uzi.command.segmental_model", "status": "source_parity_two_phase"},
    "initiate": {"intent": "uzi_initiate", "capability": "finance.uzi.native.initiation_report", "method_id": "initiation_report", "status": "source_parity"},
    "earnings": {"intent": "uzi_earnings", "capability": "finance.uzi.native.earnings_review", "method_id": "earnings_review", "status": "source_parity"},
    "catalysts": {"intent": "uzi_catalysts", "capability": "finance.uzi.native.catalyst_calendar", "method_id": "catalyst_calendar", "status": "source_parity"},
    "thesis": {"intent": "uzi_thesis", "capability": "finance.uzi.native.thesis_tracker", "method_id": "thesis_tracker", "status": "source_parity"},
    "screen": {"intent": "uzi_screen", "capability": "finance.uzi.native.quantitative_screen", "method_id": "quantitative_screen", "status": "source_parity"},
    "dd": {"intent": "uzi_dd", "capability": "finance.uzi.native.dd_checklist", "method_id": "dd_checklist", "status": "source_parity"},
    "ic-memo": {"intent": "uzi_ic_memo", "capability": "finance.uzi.native.ic_memo", "method_id": "ic_memo", "status": "source_parity"},
    "ai-readiness": {"intent": "uzi_ai_readiness", "capability": "finance.uzi.native.ai_readiness", "method_id": "ai_readiness", "status": "source_parity"},
    "earnings-preview": {"intent": "uzi_earnings_preview", "capability": "finance.uzi.native.earnings_preview", "method_id": "earnings_preview", "status": "source_parity"},
    "model-update": {"intent": "uzi_model_update", "capability": "finance.uzi.native.model_update", "method_id": "model_update", "status": "source_parity_no_demo_defaults"},
    "returns": {"intent": "uzi_returns", "capability": "finance.uzi.native.returns_attribution", "method_id": "returns_attribution", "status": "source_parity_no_zero_imputation"},
    "rebalance": {"intent": "uzi_rebalance", "capability": "finance.uzi.native.holding_rebalance", "method_id": "holding_rebalance", "status": "source_parity_explicit_fee_acceptance"},
}


if set(UZI_392_COMMAND_MAP) != set(UZI_COMMAND_IDS):
    raise RuntimeError("Pinned UZI command map is incomplete")


__all__ = ["UZI_392_COMMAND_MAP"]
