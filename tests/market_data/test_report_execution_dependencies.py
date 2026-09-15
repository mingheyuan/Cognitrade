"""Frozen auxiliary diagnostics do not become additional native report gates."""
import socket
from copy import deepcopy

import pytest

from research_system.market_data.research_readiness import report_input_readiness
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_quant_auxiliary_inputs import Source
from tests.test_uzi_392_analysis import _lite_snapshots
from tests.test_uzi_frozen_auxiliary_execution import (
    build_uzi_raw,
    frozen_auxiliary,
    quant_snapshots,
)


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Preflight must only replay frozen responses")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def database():
    db = StrategyDatabase("sqlite://")
    db.create_schema()
    yield db
    db.close()


def item(rows, frozen):
    return {
        "symbol": "600000.SH", "snapshots": rows,
        "context": {"native_quant_auxiliary": frozen["binding"]},
        "native_quant_auxiliary_recording": frozen["recording"],
    }


@pytest.mark.parametrize("fail", [True, False])
@pytest.mark.parametrize("profile", ["lite", "medium", "deep"])
def test_preflight_probes_actual_native_auxiliary_requests_without_changing_gate(database, fail, profile):
    rows = quant_snapshots()
    frozen = frozen_auxiliary(database, build_uzi_raw(rows, "600000"), source=Source(fail=fail))
    inputs = [item(rows, frozen)]
    before = deepcopy(inputs)
    result = report_input_readiness(inputs, {"research_profile": profile})
    diagnostics = result["stocks"][0]["execution_dependencies"]
    assert diagnostics["can_execute_native_analysis"] is True
    assert diagnostics["status"] == ("degraded" if fail else "available")
    assert diagnostics["report_blocking"] is False
    assert bool(diagnostics["missing_requests"]) is fail
    assert diagnostics["native_review"] is False
    assert diagnostics["assessment_scope"] == "pre_agent_rule_panel"
    assert result["can_start_analysis"] is True
    assert result["stocks"][0]["input_blockers"] == []
    assert inputs == before


def test_unused_auxiliary_is_not_reported_as_a_required_missing_dependency(database):
    rows = _lite_snapshots()  # Native distressed style returns before quant.
    frozen = frozen_auxiliary(database, build_uzi_raw(rows, "600000"), source=Source(fail=True))
    result = report_input_readiness([item(rows, frozen)], {"research_profile": "lite"})
    diagnostics = result["stocks"][0]["execution_dependencies"]
    assert diagnostics["status"] == "not_required"
    assert diagnostics["can_execute_native_analysis"] is True
    assert diagnostics["missing_requests"] == []


def test_legacy_input_is_not_looked_up_or_claimed_complete():
    result = report_input_readiness([{"symbol": "600000", "snapshots": _lite_snapshots()}], {})
    diagnostics = result["stocks"][0]["execution_dependencies"]
    assert diagnostics["status"] == "legacy_not_fixed"
    assert diagnostics["can_execute_native_analysis"] is None
    assert result["can_start_analysis"] is True


def test_wrong_binding_is_diagnostic_not_a_fabricated_native_critical(database):
    rows = quant_snapshots()
    frozen = frozen_auxiliary(database, build_uzi_raw(rows, "600000"), source=Source())
    inputs = item(rows, frozen)
    inputs["context"]["native_quant_auxiliary"] = {**frozen["binding"], "input_hash": "a" * 64}
    result = report_input_readiness([inputs], {"research_profile": "lite"})
    diagnostics = result["stocks"][0]["execution_dependencies"]
    assert diagnostics["reason_codes"] == ["native_quant_binding_mismatch"]
    assert diagnostics["can_execute_native_analysis"] is False
    assert not any(issue.get("check") == "native_quant_binding_mismatch"
                   for issue in result["stocks"][0]["data_issues"])


def test_unexpected_native_probe_failure_is_not_a_new_source_gap_or_start_gate(database, monkeypatch):
    from domain_packs.finance_ashare.subagents import uzi_392_analysis as native
    rows = quant_snapshots()
    frozen = frozen_auxiliary(database, build_uzi_raw(rows, "600000"), source=Source())
    def broken(*_args, **_kwargs):
        raise TypeError("sensitive source payload must not appear in diagnostics")
    monkeypatch.setattr(native, "_prepare_native_computation", broken)
    result = report_input_readiness([item(rows, frozen)], {"research_profile": "lite"})
    diagnostics = result["stocks"][0]["execution_dependencies"]
    assert diagnostics["status"] == "probe_failed"
    assert diagnostics["can_execute_native_analysis"] is None
    assert diagnostics["reason_codes"] == ["native_dependency_probe_failed"]
    assert diagnostics["error_type"] == "TypeError"
    assert diagnostics["missing_requests"] == []
    assert "sensitive" not in str(diagnostics)
    assert result["can_start_analysis"] is True
