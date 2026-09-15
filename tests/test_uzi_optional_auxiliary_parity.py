"""Offline native-helper comparison; synthetic cases are NOT live E2E results."""
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from domain_packs.finance_ashare.subagents import uzi_392_analysis as analysis
from research_system.market_data.contracts import canonical_hash
from research_system.market_data.quant_auxiliary import (
    QuantAuxiliaryInputError,
    quant_input_hash,
    replay_quant_auxiliary_recording,
)
from research_system.strategies.database import StrategyDatabase
from tests.test_uzi_frozen_auxiliary_execution import frozen_auxiliary, quant_snapshots


@pytest.fixture
def database():
    db = StrategyDatabase("sqlite://")
    db.create_schema()
    yield db
    db.close()


class PartialSource:
    def __init__(self, case):
        self.case = case

    def fetch(self, operation, parameters, **kwargs):
        failed = ((operation == "all_holding_funds" and self.case == "all_failure")
                  or (operation == "top_holdings" and parameters["fund_code"] == "000002"))
        if failed:
            return {"status": "source_error", "rows": [], "errors": ["ConnectionError"]}
        rows = ([{"fund_code": f"00000{i}", "fund_name": f"Fund {i}"} for i in range(3)]
                if operation == "all_holding_funds" else [{"股票代码": "600000", "占净值比例": 1.5}])
        if self.case == "verified_empty":
            rows = []
        return {"status": "available" if rows else "verified_empty", "rows": rows}


@pytest.mark.parametrize("profile", ["lite", "medium", "deep"])
@pytest.mark.parametrize("case,manager_count", [
    ("all_failure", 0), ("all_failure", 3), ("mixed_top", 0),
    ("mixed_top", 21), ("verified_empty", 0), ("no_recording", 0),
])
def test_fallback_synthesis_matches_unmodified_native_helpers(database, profile, case, manager_count):
    raw = analysis.build_uzi_raw(quant_snapshots(), "600000")
    raw["fund_managers"] = [{"fund_code": f"{i:06}", "fund_name": f"Fund {i}"}
                            for i in range(manager_count)]
    prepared = analysis._prepare_native_computation(raw, profile=profile, school=None)
    raw = prepared["raw_after_derivation"]
    frozen = frozen_auxiliary(database, raw, source=PartialSource(case))
    if case == "no_recording":
        binding = {"version": "native-quant-aux-v1", "input_hash": quant_input_hash(raw),
                   "status": "unassessed", "reason_codes": ["native_quant_inputs_missing"]}
        frozen = {"binding": binding, "recording": {"binding": deepcopy(binding), "receipt": None, "responses": {}}}
    before = deepcopy(frozen)
    diagnostics = {}
    actual_panel, actual = analysis._synthesize_native(
        raw, prepared["dimensions"], prepared["panel"], agent_analysis=None,
        profile=profile, school=None, auxiliary_input=frozen, auxiliary_diagnostics=diagnostics,
    )

    # Run the original helper implementations against recorded source outcomes.
    # They must catch their own exceptions/return [], not our replay exceptions.
    def source_rows(operation, **parameters):
        body = frozen["recording"]["responses"].get(canonical_hash({"operation": operation, "parameters": parameters}))
        if not body or body["status"] not in {"available", "verified_empty"}:
            raise ConnectionError("Synthetic failed source, no network")
        return deepcopy(body["rows"])

    def fetch_all(ticker):
        return source_rows("all_holding_funds", ticker_code=ticker, max_funds=80)

    def fetch_top(*, symbol, date):
        rows = source_rows("top_holdings", fund_code=symbol, top_n=10, holdings_year=date)
        return SimpleNamespace(empty=not rows, head=lambda _n: SimpleNamespace(to_dict=lambda _kind: rows))

    with analysis._uzi_native_environment(profile=profile, school=None):
        module = import_module("lib.quant_signal")
        holders = import_module("fetch_fund_holders")
        expected_panel = deepcopy(prepared["panel"])
        with patch.object(module, "ak", SimpleNamespace(fund_portfolio_hold_em=fetch_top)), patch.object(
            module, "cached", lambda _key, _kind, fetch, **_kwargs: fetch(),
        ), patch.object(holders, "fetch_holding_funds", fetch_all):
            expected = import_module("lib.pipeline.score_fns").generate_synthesis(
                raw, prepared["dimensions"], expected_panel, agent_analysis=None,
            )
    assert actual == expected
    assert actual_panel == expected_panel
    assert frozen == before
    assert diagnostics["report_blocking"] is False
    assert diagnostics["status"] == ("available" if case == "verified_empty" else "degraded")
    if case == "all_failure" and manager_count:
        # Failure of broad discovery must not discard the existing three funds.
        assert diagnostics["replayed_requests"] == 2


def test_unbound_response_cannot_be_smuggled_through_an_absent_receipt():
    raw = {"ticker": "600000.SH", "fund_managers": []}
    binding = {"version": "native-quant-aux-v1", "status": "unassessed", "input_hash": quant_input_hash(raw)}
    recording = {"binding": binding, "receipt": None,
                 "responses": {"unbound": {"status": "available", "rows": [{"fund_code": "fake"}]}}}
    with (
        pytest.raises(QuantAuxiliaryInputError, match="native_quant_recording_mismatch"),
        replay_quant_auxiliary_recording(recording, raw=raw, binding=binding),
    ):
        pytest.fail("Invalid recording must not reach native code")
