"""A narrow layout compatibility rule must not rewrite native valuations."""
import importlib
from copy import deepcopy

from domain_packs.finance_ashare.reporting.uzi_392_exact_report import (
    _apply_render_compatibility,
    _render_data_gap_banner,
    _renderer_module,
)


def test_native_coverage_metrics_wrap_without_modifying_native_markup_or_values():
    _renderer_module()
    native = importlib.import_module("lib.report.institutional")
    value = {"initiating_coverage": {"headline": {
        "rating": "持有", "target_price": 30, "current_price": 230.89, "upside_pct": -87,
    }}}
    before = deepcopy(value)
    card = native._render_initiating_coverage(value)
    html = '<html><head></head><body><div class="score-giant">48</div>' + card + '</body></html>'
    actual = _apply_render_compatibility(html, overall_score=48)
    assert card in actual
    assert "¥230.89" in actual and "-87.0%" in actual
    assert value == before
    assert "@media (max-width: 640px)" in actual
    assert ".initiating-block > div:nth-child(2)" in actual
    assert "flex-wrap: wrap" in actual
    assert ".school-scores > div:last-child" in actual
    assert "grid-template-columns: minmax(0, 1fr)" in actual
    assert ".sens-heatmap" in actual and "overflow-x: auto" in actual


def test_native_peer_tables_scroll_inside_the_card_not_the_page():
    html = '<html><head></head><body><div class="score-giant">48</div></body></html>'
    actual = _apply_render_compatibility(html, overall_score=48)
    assert ".dim-card {" in actual and "min-width: 0" in actual
    assert ".dim-viz {" in actual and "overflow-x: auto" in actual


def test_data_gap_acknowledgement_does_not_claim_sources_were_exhausted():
    renderer = _renderer_module()
    gaps = {"coverage_pct": 94, "unresolved": 0,
            "tasks": [{"dim": "12_capital_flow", "label": "资金流", "status": "acknowledged"}]}
    before = deepcopy(gaps)
    banner = _render_data_gap_banner(renderer, gaps, raw={"ticker": "688041.SH"}, synthesis={})
    assert "Agent 已尝试浏览器抓取" not in banner
    assert "真的拿不到" not in banner
    assert "已确认无法补齐" not in banner
    assert "采集记录" in banner
    assert "资金流 · 12_capital_flow" in banner
    assert gaps == before


def test_long_real_numbers_wrap_without_rounding_or_removing_native_content():
    markup = ('<div class="risk-box"><ul><li>PE(TTM)为182.88244136、PEG为5.349727887035</li></ul></div>'
              '<div class="zones-grid"><div class="zone"><div class="zone-price">¥286.35783333333336</div></div></div>')
    actual = _apply_render_compatibility(
        '<html><head></head><body><div class="score-giant">48</div>' + markup + '</body></html>', overall_score=48)
    assert markup in actual
    assert ".risk-box li," in actual and ".zone-price {" in actual
    assert ".zones-grid > .zone {" in actual
    assert "overflow-wrap: anywhere" in actual
