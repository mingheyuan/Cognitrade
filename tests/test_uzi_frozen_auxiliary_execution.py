"""Production synthesis replays native fallbacks without calling them verified facts."""

import importlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from domain_packs.finance_ashare.subagents import runtime
from domain_packs.finance_ashare.subagents import uzi_392_analysis as analysis
from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
from research_system.market_data.quant_auxiliary import (
    collect_quant_auxiliary_inputs,
    load_quant_auxiliary_recording,
    quant_input_hash,
    replay_quant_auxiliary_recording,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_quant_auxiliary_inputs import DAY, NOW, Source
from tests.test_uzi_392_analysis import _lite_snapshots, _LiteAgentProvider


@pytest.fixture()
def database():
    db = StrategyDatabase("sqlite://")
    db.create_schema()
    yield db
    db.close()


def frozen_auxiliary(database, raw, *, source):
    binding = collect_quant_auxiliary_inputs(
        database, raw=raw, as_of_date=DAY, now=NOW, source=source,
    )
    with database.session() as session:
        recording = load_quant_auxiliary_recording(
            MarketDataRepository(session), binding=binding, raw=raw,
            cutoff_at=datetime(2026, 9, 8, 15, 59, 59, tzinfo=timezone.utc),
        )
    return {"binding": binding, "recording": recording}


def quant_snapshots():
    # A dedicated fixture reaches native style's quant branch instead of its
    # earlier distressed return. No actual report facts or verdicts change.
    rows = _lite_snapshots()
    for row in rows:
        if row["snapshot_type"] in {"market", "valuation"}:
            row["facts"]["_uzi_raw_result"]["data"]["pb"] = 1.2
    return rows


def test_frozen_response_production_synthesis_matches_unmodified_native(database):
    raw = build_uzi_raw(quant_snapshots(), "600000")
    prepared = analysis._prepare_native_computation(raw, profile="lite", school=None)
    raw = prepared["raw_after_derivation"]
    frozen = frozen_auxiliary(database, raw, source=Source())
    before = deepcopy(frozen)
    actual_panel, actual = analysis._synthesize_native(
        raw, prepared["dimensions"], prepared["panel"], profile="lite",
        school=None, agent_analysis=None, auxiliary_input=frozen,
    )
    with analysis._uzi_native_environment(profile="lite", school=None):
        expected_panel = deepcopy(prepared["panel"])
        with replay_quant_auxiliary_recording(frozen["recording"], raw=raw,
                                             binding=frozen["binding"]):
            expected = importlib.import_module("lib.pipeline.score_fns").generate_synthesis(
                raw, prepared["dimensions"], expected_panel, agent_analysis=None,
            )
    assert actual == expected
    assert actual_panel == expected_panel
    assert frozen == before


def test_missing_optional_auxiliary_reaches_model_and_discloses_unknown(database):
    raw = build_uzi_raw(quant_snapshots(), "600000")
    frozen = frozen_auxiliary(database, raw, source=Source(fail=True))
    provider = _LiteAgentProvider()
    result = analysis.execute_native_analysis(
        quant_snapshots(), ["600000"], profile="lite", llm_provider=provider,
        auxiliary_inputs={"600000.SH": frozen},
    )
    assert provider.calls > 0
    stock = result["results"][0]
    assert "input_dependencies" not in stock["self_review"]
    diagnostics = stock["computation_diagnostics"]["native_quant_auxiliary"]
    assert diagnostics["status"] == "degraded"
    assert diagnostics["missing_request_count"] > 0
    disclosure = next(row for row in stock["synthesis"]["data_gaps"]["tasks"]
                      if row.get("field") == "native_quant_auxiliary")
    assert disclosure["status"] == "unavailable"
    assert disclosure["report_blocking"] is False
    assert "不代表确认没有量化基金持仓" in disclosure["label"]
    assert frozen["binding"]["status"] == "unassessed"


def test_completed_recording_stays_out_of_model_raw_and_result(database):
    raw = build_uzi_raw(quant_snapshots(), "600000")
    frozen = frozen_auxiliary(database, raw, source=Source())
    provider = _LiteAgentProvider()
    result = analysis.execute_native_analysis(
        quant_snapshots(), ["600000"], profile="lite", llm_provider=provider,
        auxiliary_inputs={"600000": frozen},
    )
    assert provider.calls > 0
    assert result["results"][0]["synthesis"]
    assert "receipt" not in result["results"][0]["raw"]
    assert frozen["binding"]["content_hash"] not in " ".join(provider.user_prompts)
    assert "native_quant_auxiliary_recording" not in json.dumps(result)
    diagnostics = result["results"][0]["computation_diagnostics"]["native_quant_auxiliary"]
    assert diagnostics["status"] == "available"
    assert diagnostics["replayed_requests"] > 0


def test_tampered_or_wrong_symbol_binding_does_not_start_model(database):
    raw = build_uzi_raw(_lite_snapshots(), "600000")
    frozen = frozen_auxiliary(database, raw, source=Source())
    frozen["binding"]["input_hash"] = quant_input_hash({"ticker": "688041.SH"})
    provider = _LiteAgentProvider()
    result = analysis.execute_native_analysis(
        _lite_snapshots(), ["600000"], profile="lite", llm_provider=provider,
        auxiliary_inputs={"600000": frozen},
    )
    assert provider.calls == 0
    assert not result["report_allowed"]
    assert result["results"][0]["self_review"]["input_dependencies"]["reason_codes"] == ["native_quant_binding_mismatch"]


def test_native_branch_without_quant_does_not_require_an_unused_response(database):
    # This fixture takes native distressed style before the quant fallback.
    raw = build_uzi_raw(_lite_snapshots(), "600000")
    frozen = frozen_auxiliary(database, raw, source=Source(fail=True))
    provider = _LiteAgentProvider()
    result = analysis.execute_native_analysis(
        _lite_snapshots(), ["600000"], profile="lite", llm_provider=provider,
        auxiliary_inputs={"600000": frozen},
    )
    assert provider.calls > 0
    stock = result["results"][0]
    assert stock["synthesis"]
    assert stock["computation_diagnostics"]["native_quant_auxiliary"]["status"] == "not_required"
    assert "input_dependencies" not in stock["self_review"]


def test_runtime_reads_artifact_recording_without_copying_into_snapshots():
    marker = {"binding": {"version": "native-quant-aux-v1"}, "receipt": {"fixed": True}}
    manifest = {"workflow_pins": {"native_quant_auxiliary_version": "native-quant-aux-v1"},
                "items": [{"symbol": "600000", "context": {"native_quant_auxiliary": marker["binding"]},
                           "native_quant_auxiliary_recording": marker}]}
    request = SimpleNamespace(content={"report_data_manifest": manifest})
    context = SimpleNamespace(node=SimpleNamespace(inputs={"research_profile": "lite"}),
                              artifacts=lambda kind: [request] if kind == "user_request" else [])
    assert runtime._frozen_quant_auxiliary_inputs(context) == {
        "600000": {"binding": marker["binding"], "recording": marker},
    }
    with patch.object(runtime, "_snapshot_context", return_value=([], ["600000"], "", [], [])), patch.object(
        runtime, "execute_native_analysis", return_value={"results": []},
    ) as execute:
        runtime.execute_uzi_native_full_analysis(context)
    assert execute.call_args.kwargs["auxiliary_inputs"]["600000"]["recording"] == marker
    assert execute.call_args.args[0] == []


def test_legacy_request_does_not_select_a_new_recording():
    context = SimpleNamespace(node=SimpleNamespace(inputs={}), artifacts=lambda _kind: [])
    assert runtime._frozen_quant_auxiliary_inputs(context) is None
