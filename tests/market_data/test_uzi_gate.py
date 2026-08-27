from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime
from research_system.market_data.uzi_gate import evaluate_uzi_collection_gate


DIMENSIONS = (
    "0_basic",
    "1_financials",
    "2_kline",
    "3_macro",
    "4_peers",
    "5_chain",
    "6_research",
    "7_industry",
    "8_materials",
    "9_futures",
    "10_valuation",
    "11_governance",
    "12_capital_flow",
    "13_policy",
    "14_moat",
    "15_events",
    "16_lhb",
    "17_sentiment",
    "18_trap",
    "19_contests",
)


def _snapshot(dimension: str, data: dict, *, quality: str = "available"):
    return SimpleNamespace(
        status="available",
        source_version="uzi-3.9.4-1b5cdcb",
        payload_json={"raw": data},
        query_manifest_json={"native_quality_status": quality},
        validation_json={"native_available": True, "audit_errors": []},
    )


def _native_rows(*, optional_fields: bool = True):
    data = {dimension: {"content": dimension} for dimension in DIMENSIONS}
    data["0_basic"] = {
        "name": "贵州茅台",
        "price": 1500,
        "industry": "白酒",
        "market_cap": 1_800_000_000_000,
    }
    data["1_financials"] = {"roe_history": [30, 31, 32]}
    data["2_kline"] = {
        # UZI's gate uses the derived stage, not Cognitrade's former 260-row
        # unadjusted-history requirement.
        "stage": "Stage 2",
    }
    if optional_fields:
        data["0_basic"].update(pe_ttm=25, pb=8)
        data["1_financials"].update(
            revenue_history=[1, 2, 3],
            net_profit_history=[1, 2, 3],
            financial_health={"status": "healthy"},
        )
        data["2_kline"].update(ma_align="bull", macd="positive")
        data["10_valuation"].update(pe=25, pe_quantile=50, pb_quantile=60)
        data["7_industry"].update(growth="positive")
        data["14_moat"].update(scores={"total": 80})
    return {
        dimension: _snapshot(dimension, payload)
        for dimension, payload in data.items()
    }


def test_uzi_gate_uses_native_fields_and_ignores_extra_pit_contracts() -> None:
    report = evaluate_uzi_collection_gate(_native_rows(), profile="medium")

    assert report["gate_passed"] is True
    assert report["coverage_pct"] == 100
    assert report["critical_count"] == 0


def test_uzi_missing_native_critical_field_is_recovery_input_not_a_separate_gate() -> None:
    rows = _native_rows()
    rows["0_basic"].payload_json["raw"].pop("name")

    report = evaluate_uzi_collection_gate(rows, profile="medium")

    assert report["critical_missing"] is True
    assert report["gate_passed"] is True
    assert not any(row["check"] == "data_integrity_critical" for row in report["issues"])


def test_uzi_warnings_do_not_block_medium_or_deep_html_gate() -> None:
    rows = _native_rows(optional_fields=False)
    rows["0_basic"].payload_json["raw"].update(pe_ttm=25, pb=8)
    rows["1_financials"].payload_json["raw"]["revenue_history"] = [1, 2, 3]

    medium = evaluate_uzi_collection_gate(rows, profile="medium")
    deep = evaluate_uzi_collection_gate(rows, profile="deep")

    assert medium["coverage_pct"] == 50
    assert medium["gate_passed"] is True
    assert deep["coverage_pct"] == 50
    assert deep["gate_passed"] is True


def test_uzi_lite_downgrades_low_coverage_but_medium_blocks_it() -> None:
    rows = _native_rows(optional_fields=False)
    rows["0_basic"].payload_json["raw"] = {"name": "贵州茅台"}
    rows["1_financials"].payload_json["raw"] = {"content": "财务来源返回但关键字段缺失"}
    rows["2_kline"].payload_json["raw"] = {"content": "行情来源返回但关键字段缺失"}

    lite = evaluate_uzi_collection_gate(rows, profile="lite")
    medium = evaluate_uzi_collection_gate(rows, profile="medium")

    assert lite["coverage_pct"] < 40
    assert lite["gate_passed"] is True
    assert medium["coverage_pct"] < 40
    assert medium["gate_passed"] is False


def test_uzi_inexplicably_empty_enabled_dimension_blocks() -> None:
    rows = _native_rows()
    rows["7_industry"].payload_json["raw"] = {}

    report = evaluate_uzi_collection_gate(rows, profile="medium")

    assert report["gate_passed"] is False
    assert any(
        row["check"] == "empty_dims"
        and row["dimension"] == "7_industry"
        and row["severity"] == "critical"
        for row in report["issues"]
    )


def test_empty_dimension_preserves_native_error_location_semantics() -> None:
    pipeline_rows = _native_rows()
    pipeline_rows["7_industry"].payload_json = {
        "raw": {},
        "_uzi_raw_result": {
            "data": {},
            "fallback": True,
            "_pipeline": {"quality": "error", "error": "worker failed"},
        },
    }
    pipeline_rows["7_industry"].query_manifest_json = {
        "native_quality_status": "error"
    }
    legacy_rows = _native_rows()
    legacy_rows["7_industry"].payload_json = {
        "raw": {},
        "_uzi_raw_result": {
            "data": {},
            "fallback": True,
            "error": "legacy fetch failed",
        },
    }
    legacy_rows["7_industry"].query_manifest_json = {
        "native_quality_status": "error"
    }

    pipeline = evaluate_uzi_collection_gate(pipeline_rows, profile="medium")
    legacy = evaluate_uzi_collection_gate(legacy_rows, profile="medium")

    assert any(
        row["dimension"] == "7_industry" and row["severity"] == "critical"
        for row in pipeline["issues"]
    )
    assert any(
        row["dimension"] == "7_industry" and row["severity"] == "warning"
        for row in legacy["issues"]
    )


def test_medium_empty_fund_holders_matches_native_prefix_semantics(monkeypatch) -> None:
    rows = _native_rows()
    rows["6_fund_holders"] = _snapshot("6_fund_holders", {})

    monkeypatch.setenv("UZI_DEPTH", "medium")
    Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[2])
    review_module = importlib.import_module("lib.self_review")
    dimensions = {
        dimension: {"data": dict(snapshot.payload_json["raw"])}
        for dimension, snapshot in rows.items()
    }
    native = review_module.check_empty_dims(
        {
            "raw": {"dimensions": dimensions},
            "dims": dimensions,
            "market": "A",
            "syn": {},
            "panel": {},
            "ag": {},
            "ticker": "600519.SH",
        }
    )
    actual = evaluate_uzi_collection_gate(rows, profile="medium", market="A")

    assert any(
        issue.dim == "6_fund_holders" and issue.severity == "critical"
        for issue in native
    )
    assert any(
        issue["dimension"] == "6_fund_holders"
        and issue["severity"] == "critical"
        for issue in actual["issues"]
    )


def test_number_six_bonus_presence_matches_native_all_dims_semantics(monkeypatch) -> None:
    rows = _native_rows()
    rows.pop("6_research")
    rows["6_fund_holders"] = _snapshot(
        "6_fund_holders", {"holders": [{"name": "示例基金"}]}
    )

    monkeypatch.setenv("UZI_DEPTH", "medium")
    Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[2])
    review_module = importlib.import_module("lib.self_review")
    dimensions = {
        dimension: {"data": dict(snapshot.payload_json["raw"])}
        for dimension, snapshot in rows.items()
    }
    native = review_module.check_all_dims_exist(
        {
            "raw": {"dimensions": dimensions},
            "dims": dimensions,
            "market": "A",
            "syn": {},
            "panel": {},
            "ag": {},
            "ticker": "600519.SH",
        }
    )
    actual = evaluate_uzi_collection_gate(rows, profile="medium", market="A")

    assert not any(issue.dim == "6_" for issue in native)
    assert not any(
        issue["check"] == "all_dims_exist" and issue["dimension"] == "6_"
        for issue in actual["issues"]
    )


def test_collection_precheck_matches_pinned_native_data_self_review(monkeypatch) -> None:
    Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[2])
    integrity_module = importlib.import_module("lib.data_integrity")
    review_module = importlib.import_module("lib.self_review")
    monkeypatch.delenv("UZI_CLI_ONLY", raising=False)
    monkeypatch.delenv("CI", raising=False)

    scenarios = [
        ("medium", _native_rows()),
        ("medium", _native_rows(optional_fields=False)),
        ("lite", _native_rows(optional_fields=False)),
    ]
    scenarios[2][1]["0_basic"].payload_json["raw"] = {"name": "贵州茅台"}
    scenarios[2][1]["1_financials"].payload_json["raw"] = {"content": "财务来源有响应"}
    scenarios[2][1]["2_kline"].payload_json["raw"] = {"content": "行情来源有响应"}

    for profile, snapshots in scenarios:
        monkeypatch.setenv("UZI_DEPTH", profile)
        dimensions = {
            dimension: {"data": dict(snapshot.payload_json["raw"])}
            for dimension, snapshot in snapshots.items()
        }
        raw = {"dimensions": dimensions}
        raw["_integrity"] = integrity_module.validate(raw)
        context = {
            "raw": raw,
            "dims": dimensions,
            "market": "A",
            "syn": {},
            "panel": {},
            "ag": {},
            "ticker": "600519.SH",
        }
        native_issues = [
            *review_module.check_all_dims_exist(context),
            *review_module.check_empty_dims(context),
            *review_module.check_hk_kline_populated(context),
            *review_module.check_hk_financials_populated(context),
            *review_module.check_coverage_threshold(context),
        ]
        actual = evaluate_uzi_collection_gate(snapshots, profile=profile, market="A")

        assert actual["critical_count"] == sum(
            issue.severity == "critical" for issue in native_issues
        )
        assert actual["warning_count"] == sum(
            issue.severity == "warning" for issue in native_issues
        )
        assert actual["gate_passed"] is not any(
            issue.severity == "critical" for issue in native_issues
        )
