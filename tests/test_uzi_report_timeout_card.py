"""A native pipeline timeout is an unavailable view, not an empty observation."""

from copy import deepcopy

import pytest

from domain_packs.finance_ashare.reporting.uzi_392_exact_report import (
    _escape_payload,
    _renderer_module,
    render_exact_uzi_392_report,
)
from tests.test_uzi_native_report import sample_uzi_artifacts


def native_analysis():
    return next(
        row["content"] for row in sample_uzi_artifacts()
        if row["artifact_type"] == "uzi_native_analysis"
    )


def timeout_envelope():
    return {
        "data": {}, "source": "pipeline:timeout", "fallback": True,
        "_pipeline": {
            "dim_key": "similar_stocks", "quality": "error", "data_gaps": [],
            "latency_ms": None, "fetched_at": None, "cached": False,
            "top_level_fields": {}, "error": "fetcher timeout > 30s",
        },
    }


def test_timeout_card_discloses_failure_without_mutating_authoritative_results():
    analysis = native_analysis()
    row = analysis["results"][0]
    row["raw"]["similar_stocks"] = timeout_envelope()
    row["synthesis"].setdefault("friendly", {})["similar_stocks"] = timeout_envelope()
    before = deepcopy(analysis)
    # Keep the unmodified upstream failure as the causal baseline.
    with pytest.raises(TypeError, match="slice"):
        _renderer_module().render_friendly_layer(row["synthesis"], row["raw"])
    html, _ = render_exact_uzi_392_report(analysis)
    assert 'data-render-diagnostic="native_similar_stocks_timeout"' in html
    assert "可比股数据采集超时，未取得结果；不代表不存在可比股" in html
    assert "暂无可比股" not in html
    assert analysis == before
    # The native renderer is not monkey-patched by the host compatibility view.
    with pytest.raises(TypeError, match="slice"):
        _renderer_module().render_friendly_layer(row["synthesis"], row["raw"])


@pytest.mark.parametrize("value", [None, [], [{"name": "<fixture>", "code": "TEST"}]])
def test_valid_list_and_native_missing_state_keep_exact_upstream_markup(value):
    analysis = native_analysis()
    row = analysis["results"][0]
    row["synthesis"].setdefault("friendly", {})["similar_stocks"] = value
    expected = _renderer_module().render_friendly_layer(
        _escape_payload(row["synthesis"]), _escape_payload(row["raw"]),
    )
    before = deepcopy(analysis)
    html, _ = render_exact_uzi_392_report(analysis)
    assert expected in html
    assert "native_similar_stocks_timeout" not in html
    assert analysis == before


def test_unknown_shape_is_not_silently_hidden_as_a_known_timeout():
    analysis = native_analysis()
    analysis["results"][0]["synthesis"].setdefault("friendly", {})[
        "similar_stocks"
    ] = {"unexpected": "shape"}
    with pytest.raises(TypeError, match="slice"):
        render_exact_uzi_392_report(analysis)


@pytest.mark.parametrize("change", [
    {"data": {"similar_stocks": [{"name": "PARTIAL_RESULT", "code": "TEST"}]}},
    {"_pipeline": {**timeout_envelope()["_pipeline"], "error": {"unknown": "shape"}}},
    {"_pipeline": {**timeout_envelope()["_pipeline"], "top_level_fields": {"similar_stocks": []}}},
])
def test_near_match_with_partial_data_or_unknown_error_is_not_hidden(change):
    analysis = native_analysis()
    row = analysis["results"][0]
    row["synthesis"].setdefault("friendly", {})["similar_stocks"] = {
        **timeout_envelope(), **change,
    }
    before = deepcopy(analysis)
    with pytest.raises(TypeError, match="slice"):
        render_exact_uzi_392_report(analysis)
    assert analysis == before


def test_timeout_does_not_bypass_report_quality_gate():
    analysis = native_analysis()
    analysis["report_allowed"] = False
    with pytest.raises(ValueError, match="关键质量门禁"):
        render_exact_uzi_392_report(analysis)
