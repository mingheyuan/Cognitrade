"""Production scoring must use the same pure entry as the parity replay."""

import importlib
from copy import deepcopy
from unittest.mock import patch

from domain_packs.finance_ashare.subagents import uzi_392_analysis as analysis
from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
from domain_packs.finance_ashare.subagents.uzi_input_views import (
    without_native_demo_holdings,
)
from tests.test_uzi_392_analysis import _lite_snapshots, _LiteAgentProvider


def test_recorded_overrides_do_not_recompute_native_aggregates():
    analysis._initialize_native_imports()
    score_fns = importlib.import_module("lib.pipeline.score_fns")
    raw = build_uzi_raw(_lite_snapshots(), "600000")
    with analysis._uzi_native_environment(profile="lite", school=None):
        dims = score_fns.score_dimensions(raw)
        panel = score_fns.generate_panel(dims, raw)
    agent = {"agent_reviewed": True, "per_investor_override": {
        "buffett": {"score": 88, "signal": "bullish", "headline": "录制评审事实",
                    "verdict": "买入", "reasoning": "固定证据支持的录制意见"},
    }}
    original = deepcopy(panel)
    result = analysis.replay_native_alignment(
        raw, profile="lite", agent_analysis=agent, panel=panel,
    )
    assert panel == original
    with analysis._uzi_native_environment(profile="lite", school=None):
        expected = deepcopy(panel)
        score_fns.generate_synthesis(result["raw_after_derivation"], result["dimensions"],
                                     expected, agent_analysis=deepcopy(agent))
    assert result["panel"] == expected
    assert result["panel"]["panel_consensus"] == original["panel_consensus"]
    assert result["panel_before_overrides"] == original


def test_live_analysis_does_not_apply_the_legacy_host_projection():
    with patch.object(analysis, "_profile_native_panel", side_effect=AssertionError("host re-score")):
        result = analysis.execute_native_analysis(
            snapshot_rows=_lite_snapshots(), symbols=["600000"], profile="lite",
            llm_provider=_LiteAgentProvider(),
        )["results"][0]
    assert result["synthesis"]
    assert not result.get("computation_error")


def test_replay_does_not_mutate_raw_or_agent_output():
    raw = build_uzi_raw(_lite_snapshots(), "600000")
    agent = {"agent_reviewed": True}
    original_raw, original_agent = deepcopy(raw), deepcopy(agent)
    result = analysis.replay_native_alignment(raw, profile="medium", agent_analysis=agent)
    assert raw == original_raw
    assert agent == original_agent
    assert result["native_python_review"]["source_commit"] == analysis.UZI_SOURCE_COMMIT


def test_native_demo_holding_is_preserved_only_in_canonical_computation():
    raw = build_uzi_raw(_lite_snapshots(), "600000")
    result = analysis.replay_native_alignment(raw, profile="medium")
    canonical = result["raw_after_derivation"]
    original = deepcopy(canonical)
    view = without_native_demo_holdings(canonical)
    assert canonical == original
    assert view["dimensions"]["22_deep_methods"]["data"]["portfolio_rebalance"] == {
        "quality_status": "data_gap", "data_gap": ["explicit_portfolio_positions_required"],
        "result": None,
    }
    assert canonical["dimensions"]["22_deep_methods"]["data"]["portfolio_rebalance"] != (
        view["dimensions"]["22_deep_methods"]["data"]["portfolio_rebalance"]
    )
