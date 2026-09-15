"""Offline red specifications: real reference_work + fake SDK/native I/O.

The selected self-review checks are loaded unchanged from the pinned b366
file. This is not a native full-run fixture or a content-quality oracle.
No production runner is changed and no real model/source can be called.
"""

import asyncio
import importlib.util
import json
import socket
import sys
import time
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from tests.codex_comparison_fakes import model_transport
import pytest

from scripts import run_uzi_agent_comparison as t3

NATIVE = Path(__file__).resolve().parents[1] / "domain_packs/finance_ashare/vendor/uzi_3_9_2/scripts"
SIZES = {"A": 6, "B": 9, "C": 7, "D": 4, "E": 7, "F": 24, "G": 4, "H": 4, "I": 1}


@pytest.fixture
def native_checks(monkeypatch):
    name = "offline_b366_review_checks"
    spec = importlib.util.spec_from_file_location(name, NATIVE / "lib/self_review.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def payload(request):
    for line in request["input"].splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and "raw" in value:
            return value
    raise AssertionError("Reference request must keep the frozen input")


@pytest.fixture
def run_offline(monkeypatch, tmp_path, native_checks):
    def run(scenario):
        reference = tmp_path / scenario / "reference"
        scripts = reference / "skills/deep-analysis/scripts"
        output = tmp_path / scenario / "output"
        rows = [{"investor_id": f"fixture_{school}_{i}", "group": school,
                 "signal": "neutral", "score": 50, "headline": "Frozen fact 12",
                 "reasoning": "Synthetic archived fact 12. Unknown remains unknown."}
                for school, count in SIZES.items() for i in range(count)]
        original_raw = {"ticker": "FIXTURE.SH", "market": "A", "dimensions": {
            "0_basic": {"data": {"main_business": "Synthetic computing", "industry": "Synthetic"}},
            "1_financials": {"data": {"net_profit_history": []}},
        }}
        if scenario == "source_gap":
            original_raw["dimensions"]["0_basic"]["data"] = {}
        initial_raw = deepcopy(original_raw)
        requests, reviews, events, writes, cache = [], [], [], {}, {}
        counters = Counter()
        modules = {name: SimpleNamespace(__file__=str(scripts / (name.replace(".", "/") + ".py")))
                   for name in ("lib.stock_features", "compute_deep_methods", "lib.pipeline.score_fns",
                                "lib.self_review", "lib.agent_analysis_validator", "lib.quant_signal",
                                "lib.analysis_profile", "lib.cache", "assemble_report", "lib.data_integrity", "run_real_test", "inline_assets")}
        modules["lib.data_integrity"].refresh_recovery_artifact = lambda *_args: None
        modules["lib.stock_features"].extract_features = lambda *_args: {"fixed": 12}
        modules["lib.stock_features"].sanitize_features = deepcopy
        methods = modules["compute_deep_methods"]
        methods.compute_dim_20 = lambda *_args: {"data": {
            "dcf": {"intrinsic_per_share": 0 if scenario.startswith("warning") else 12},
            "comps": {"implied_price": 12}}}
        methods.compute_dim_21 = lambda *_args: {"data": {"computed": 21}}
        methods.compute_dim_22 = lambda *_args: {"data": {"computed": 22}}
        score = modules["lib.pipeline.score_fns"]
        score.score_dimensions = lambda *_args: {"unchanged_native_score": 50}
        score.generate_panel = lambda *_args: {"investors": deepcopy(rows), "consensus_score": 50}

        def synthesis(_raw, _dims, _panel, agent_analysis=None):
            counters["native_synthesis"] += 1
            events.append("synthesis")
            agent = agent_analysis or {}
            return {"dim_commentary": deepcopy(agent.get("dim_commentary", {})),
                    "dashboard": {"battle_plan": {"entry": 12, "stop": 10}},
                    "overall_score": 50, "verdict_label": "unchanged native fixture"}

        score.generate_synthesis = synthesis
        modules["lib.analysis_profile"].get_profile = lambda *_args: SimpleNamespace(fetchers_enabled={"0_basic", "1_financials"})
        modules["lib.analysis_profile"].apply_profile_to_env = lambda *_args: None
        modules["lib.agent_analysis_validator"].validate = lambda *_args: []
        modules["lib.cache"].write_task_output = lambda _ticker, name, value: cache.update({name: deepcopy(value)})
        modules["lib.cache"].read_task_output = lambda _ticker, name: deepcopy(cache.get(name))

        def review(_ticker):
            counters["review"] += 1
            events.append("review")
            ctx = {"ticker": "FIXTURE.SH", "market": "A", "raw": cache["raw_data"],
                   "dims": cache["raw_data"]["dimensions"], "syn": cache["synthesis"],
                   "ag": cache["agent_analysis"], "panel": cache["panel"]}
            if scenario in {"repairable", "no_progress", "two_changes", "baseline_repair"}:
                issues = native_checks.check_placeholder_strings(ctx)
            elif scenario == "seat":
                seat = next(row for row in ctx["panel"]["investors"] if row["investor_id"] == "fixture_C_0")
                issues = [] if seat["headline"] == "Repaired frozen fact" else [native_checks.Issue(
                    severity="critical", category="panel", dim="panel", issue="fixture_C_0 has an unsupported headline",
                    evidence="fixture_C_0", suggested_fix="Revise fixture_C_0 using the frozen observation, retain other groups.")]
            elif scenario == "source_gap":
                issues = native_checks.check_empty_dims(ctx)
            elif scenario.startswith("warning"):
                issues = native_checks.check_valuation_sanity(ctx)
            else:
                issues = []
            result = {"ticker": "FIXTURE.SH", "critical_count": sum(i.severity == "critical" for i in issues),
                      "warning_count": sum(i.severity == "warning" for i in issues),
                      "info_count": sum(i.severity == "info" for i in issues),
                      "issues": [i.to_dict() for i in issues], "checks_run": ["selected_unchanged_b366_check"]}
            result["passed"] = result["critical_count"] == 0
            reviews.append(deepcopy(result))
            return result

        def write_review(_ticker, result):
            cache["_review_issues"] = deepcopy(result)
            return output / ".cache/FIXTURE.SH/_review_issues.json"

        modules["lib.self_review"].review_all = review
        modules["lib.self_review"].write_review = write_review

        def assemble(_ticker):
            counters["assemble"] += 1
            events.append("assemble")
            # Preserve the fact that native assemble independently checks again.
            checked = review(_ticker)
            write_review(_ticker, checked)
            assert checked["critical_count"] == 0
            return output / "reports/FIXTURE.SH/full-report.html"

        modules["assemble_report"].assemble = assemble
        modules["run_real_test"].stage2 = assemble
        quant = modules["lib.quant_signal"]
        quant._fetch_top_holdings = quant._fetch_all_holding_funds = lambda *_args: pytest.fail("No source I/O")
        importer = t3.importlib.import_module
        monkeypatch.setattr(t3.importlib, "import_module", lambda name: modules[name] if name in modules else importer(name))
        monkeypatch.setattr(sys, "path", list(sys.path))
        monkeypatch.setenv("OPENAI_API_KEY", "offline-not-secret")
        monkeypatch.setenv("UZI_SKIP_REVIEW", "must-be-removed")
        monkeypatch.setattr(socket.socket, "connect", lambda *_args: pytest.fail("Unexpected real socket"))
        monkeypatch.setattr(socket, "getaddrinfo", lambda *_args: pytest.fail("Unexpected real DNS"))
        monkeypatch.setattr(t3, "file_binding", lambda path: {"path": str(path), "sha256": "synthetic-offline"})
        monkeypatch.setattr(t3, "read_bound", lambda binding: deepcopy(original_raw) if binding["fixture"] == "raw" else {})
        monkeypatch.setattr(t3, "native_text", lambda _ref, path: "OFFLINE_TASK25" if "task2.5" in path else "OFFLINE_NATIVE_INSTRUCTIONS")

        def write_new(path, value):
            assert str(path) not in writes, "Old audit attempts must never be overwritten"
            writes[str(path)] = deepcopy(value)

        monkeypatch.setattr(t3, "write_new", write_new)

        class FakeClient:
            def __init__(self, **_kwargs):
                self.responses = self.with_raw_response = self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def create(self, *, timeout, **request):
                assert 0 < timeout <= 60
                requests.append(deepcopy(request))
                data = payload(request)
                if "rules" in data:
                    counters["roleplay"] += 1
                    value = {"investors": deepcopy(data["rules"])}
                    if scenario in {"baseline", "baseline_repair"}:
                        value["investors"][0]["score"] = 99
                    if scenario == "seat" and "native_review_issues" in data:
                        for row in value["investors"]:
                            if row["investor_id"] == "fixture_C_0":
                                row["headline"] = "Repaired frozen fact"
                elif "OFFLINE_TASK25" in request["instructions"]:
                    counters["qualitative"] += 1
                    marker = "指定维度 "
                    dims, _ = json.JSONDecoder().raw_decode(request["input"].split(marker, 1)[1])
                    value = {dim: {"evidence": [{"source": "synthetic archive", "url": "https://offline.invalid/archive",
                                               "retrieved_at": "2026-09-12", "finding": f"Frozen {dim} observation {i}"} for i in (1, 2)],
                                   "associations": [{"causal_chain": "Synthetic known association"}],
                                   "conclusion": "Only synthetic fixed evidence."} for dim in dims}
                else:
                    counters["main"] += 1
                    events.append("agent-main")
                    commentary = {f"{i}_fixture": "Frozen observation 12 supports a limited inference." for i in range(15)}
                    if scenario in {"repairable", "no_progress", "baseline_repair"} and (counters["main"] == 1 or scenario == "no_progress"):
                        commentary["0_fixture"] = "[TODO]"
                    if scenario == "two_changes":
                        commentary["0_fixture"] = f"[TODO] content revision {counters['main']}"
                    value = {"agent_reviewed": True, "dim_commentary": commentary}
                    if counters["main"] > 1 and scenario in {"warning", "warning_rewrite"}:
                        value["review_acknowledged"] = {"20_valuation_models": "Frozen financial history is empty; DCF remains unknown, not zero fair value."}
                    if counters["main"] > 1 and scenario == "warning_rewrite":
                        # Native schema permits normal rewording and additional
                        # disclosure in the affected main analysis stage.
                        value["dim_commentary"]["1_fixture"] = "The same frozen observation 12 remains available; this is a rephrasing."
                        value["review_notes"] = "No raw facts were corrected."
                        value["raw_fixed"] = False
                text = json.dumps(value)
                response = SimpleNamespace(output_text=text, model_dump=lambda **_kwargs: {"output_text": text})
                return SimpleNamespace(status_code=200, headers={"x-request-id": "offline"}, parse=lambda: response)

        monkeypatch.setattr(t3, "codex_model_transport", model_transport(FakeClient))
        config = {"model": "offline", "base_url": "https://offline.invalid/v1", "model_concurrency": 4,
                  "request_timeout_seconds": 60, "store": False, "service_tier": "default",
                  "temperature": 0.1, "reasoning_effort": "high"}
        manifest = {"native_reference": {"path": str(reference)}, "profile": "medium",
                    "inputs": {"raw": {"fixture": "raw"}, "auxiliary": {"fixture": "auxiliary"}},
                    "model_configuration": config}
        if scenario in {"frozen_computation", "reuse_personas", "reuse_personas_drift"}:
            frozen = deepcopy(original_raw)
            frozen["dimensions"]["21_research_workflow"] = {"data": {"date": "2026-09-12"}}
            reused = {"computation": {"raw": frozen, "features": {"fixed": 12},
                      "features_before_derivation": {"fixed": 12}, "feature_stage": "recorded",
                      "dimensions": {"unchanged_native_score": 50},
                      "panel": {"investors": deepcopy(rows), "consensus_score": 50}},
                      "data_gaps": {"tasks": []},
                      "provenance": {"execution": "reused_recorded_native_computation_not_recomputed"}}
            monkeypatch.setattr(t3, "verify_reference_computation_reuse", lambda *_args: deepcopy(reused), raising=False)
            for function in ("compute_dim_20", "compute_dim_21", "compute_dim_22"):
                monkeypatch.setattr(methods, function, lambda *_args: pytest.fail("Frozen computation must not use today's clock"))
        if scenario.startswith("reuse_personas"):
            manifest["reference_roleplay_reuse"] = {"fixture": True}
            research = [{"stage": f"qualitative-{name}", "status": "completed", "attempts": 0,
                         "value": {dim: {"evidence": [{"source": "synthetic", "url": "https://offline.invalid/doc",
                             "retrieved_at": "2026-09-12", "finding": f"Frozen fact {i}"} for i in (1, 2)],
                             "associations": [{"causal_chain": "Fixed"}], "conclusion": "Fixed"} for dim in dims}}
                        for name, dims in t3.RESEARCH]
            monkeypatch.setattr(t3, "verify_reference_qualitative_reuse", lambda *_args, **_kw: (deepcopy(research), []))
            def reused_personas(_manifest, *, current_requests):
                assert not requests, "All reused requests must be validated before any model request"
                assert len(current_requests) == 5
                if scenario == "reuse_personas_drift":
                    raise ValueError("Persona reuse request changed")
                values = {f"roleplay-{name}": {"stage": f"roleplay-{name}", "status": "completed", "attempts": 0,
                           "execution": "reused_not_new_model_execution", "value": {"investors": deepcopy(group_rows)}}
                          for name, group_rows in t3.roleplay_groups(rows) if name != "G+H+I"}
                return values, [{"stage": stage, "execution": "reused_not_new_model_execution"} for stage in values]
            monkeypatch.setattr(t3, "verify_reference_roleplay_reuse", reused_personas)
        result = asyncio.run(t3.reference_work(manifest, output, SimpleNamespace(model_io=nullcontext),
                                              deadline_at=time.monotonic() + 60))
        assert original_raw == initial_raw
        return SimpleNamespace(result=result, requests=requests, reviews=reviews, events=events,
                               counters=counters, cache=cache, writes=writes, initial_raw=initial_raw)

    return run


def test_reference_resumes_only_failed_personas_and_main_without_research_calls(run_offline):
    run = run_offline("reuse_personas")
    assert run.counters["qualitative"] == 0
    assert run.counters["roleplay"] == run.counters["main"] == 1
    assert {row["group"] for row in payload(run.requests[0])["rules"]} == set("GHI")
    assert len(run.result["reused_reference_roleplay"]) == 4
    summary = next(value for path, value in run.writes.items() if path.endswith("roleplay-stages.json"))
    assert [row["attempts"] for row in summary] == [0, 0, 0, 0, 1]


def test_reference_request_drift_stops_before_any_model_call(run_offline):
    with pytest.raises(ValueError, match="request changed"):
        run_offline("reuse_personas_drift")


def test_reference_dispatches_from_bound_computation_without_recomputing_today(run_offline):
    run = run_offline("frozen_computation")
    assert run.counters["roleplay"] == 5 and run.counters["qualitative"] == 3
    assert run.counters["main"] == 1
    assert all(payload(r)["raw"]["dimensions"]["21_research_workflow"]["data"]["date"] == "2026-09-12"
               for r in run.requests)
    assert any(path.endswith("computation-reuse.json") for path in run.writes)


@pytest.mark.parametrize("scenario,attempts", [("baseline", 1), ("baseline_repair", 2)])
def test_reference_initial_and_repair_requests_preserve_original_rule_scores(run_offline, scenario, attempts):
    run = run_offline(scenario)
    main_inputs = [payload(request) for request in run.requests
                   if "panel" in payload(request) and "rules" not in payload(request)]
    assert len(main_inputs) == attempts
    for data in main_inputs:
        original = data["rule_panel_before_agent_override"]
        assert len(original["investors"]) == sum(SIZES.values())
        assert {row["score"] for row in original["investors"]} == {50}
        assert any(row["score"] == 99 for row in data["panel"]["investors"])
    assert run.counters["roleplay"] == 5 and run.counters["qualitative"] == 3


def test_repairable_native_critical_reaches_model_with_exact_suggested_fix(run_offline):
    run = run_offline("repairable")
    issue = run.reviews[0]["issues"][0]
    assert issue["severity"] == "critical" and "commentary" in issue["suggested_fix"]
    later = run.requests[9:]
    assert later, "Native review currently terminates without allowing its in-scope Agent repair"
    assert any(issue["suggested_fix"] in request["input"] for request in later)
    assert run.counters["qualitative"] == 3 and run.counters["roleplay"] == 5


def test_native_repair_remerges_and_rereviews_before_assemble_not_whole_run(run_offline):
    run = run_offline("repairable")
    assert run.counters["main"] == 2, "Only affected main analysis should be repaired"
    assert run.counters["qualitative"] == 3 and run.counters["roleplay"] == 5
    assert run.reviews[0]["critical_count"] == 1 and run.reviews[-1]["critical_count"] == 0
    assert run.events.index("assemble") > max(i for i, event in enumerate(run.events) if event == "agent-main")
    assert run.cache["synthesis"]["dashboard"]["battle_plan"] == {"entry": 12, "stop": 10}
    assert run.cache["synthesis"]["overall_score"] == 50


def test_critical_review_is_persisted_before_any_repair_or_handoff(run_offline):
    run = run_offline("source_gap")
    assert run.reviews[0]["critical_count"] == 1
    assert run.cache.get("_review_issues") == run.reviews[0], "Blocked native review is currently only in final result, not native review artifact"


def test_fixed_missing_fact_returns_native_source_handoff_without_fetch_or_downgrade(run_offline):
    run = run_offline("source_gap")
    issue = run.reviews[0]["issues"][0]
    # Proposed minimal handoff contract; no new fact may be invented to satisfy it.
    tasks = run.result.get("source_requests", [])
    assert any(row.get("dimension") == issue["dim"] and row.get("native_issue") == issue for row in tasks)
    assert run.cache["raw_data"]["dimensions"]["0_basic"]["data"] == {}
    assert run.result["native_review"]["critical_count"] == 1
    assert run.counters["assemble"] == 0


def test_native_warning_is_repaired_or_explicitly_acknowledged_before_delivery(run_offline):
    run = run_offline("warning")
    assert run.reviews[0]["warning_count"] == 1
    assert run.cache["agent_analysis"].get("review_acknowledged", {}).get("20_valuation_models"), "Existing reference renders without warning repair or acknowledgement"
    assert run.cache["raw_data"]["dimensions"]["20_valuation_models"]["data"]["dcf"]["intrinsic_per_share"] == 0
    assert run.reviews[-1]["warning_count"] == 1, "Acknowledgement must not alter native issue severity"


def test_full_skill_completion_lists_pending_visual_artifacts_instead_of_html_only_success(run_offline):
    run = run_offline("clean")
    # Future closure can hand off to a separately authorized offline visual step;
    # this test does not grant a browser/process permission or require live rendering.
    delivery = run.result.get("skill_delivery", {})
    assert delivery.get("passed") is False
    assert set(delivery.get("pending_artifacts", [])) >= {"share_card_png", "war_report_png", "browser_validation"}
    assert run.result["report_delivery_allowed"] is False


def test_clean_baseline_does_not_schedule_an_extra_entire_model_round(run_offline):
    run = run_offline("clean")
    assert run.counters["qualitative"] == 3
    assert run.counters["roleplay"] == 5
    assert run.counters["main"] == 1
    assert run.result["new_model_requests"] == 9
    assert len(run.cache["panel"]["investors"]) == 66
    assert run.cache["panel"]["consensus_score"] == 50


def test_unchanged_native_critical_never_renders_or_silently_acknowledges_success(run_offline):
    run = run_offline("no_progress")
    assert run.result["native_review"]["critical_count"] == 1
    assert run.counters["assemble"] == 0
    assert run.result["report_delivery_allowed"] is False
    assert run.counters["qualitative"] == 3 and run.counters["roleplay"] == 5
    assert run.counters["main"] == 2
    assert run.result["native_recovery"]["stop_reason"] == "no_content_progress"


def test_real_content_changes_still_stop_after_existing_two_recoveries(run_offline):
    run = run_offline("two_changes")
    assert run.counters["main"] == 3
    assert run.counters["qualitative"] == 3 and run.counters["roleplay"] == 5
    assert run.result["native_recovery"]["stop_reason"] == "bounded_recovery_exhausted"
    assert len(run.result["native_recovery"]["reviews"]) == 3
    assert all(row["attempts"] == 1 for row in run.result["native_recovery"]["stages"])
    assert run.counters["assemble"] == 0


def test_only_named_roleplay_group_and_consuming_main_are_repaired(run_offline):
    run = run_offline("seat")
    assert run.counters["roleplay"] == 6 and run.counters["qualitative"] == 3
    assert run.counters["main"] == 2
    stages = run.result["native_recovery"]["stages"]
    assert [row["stage"] for row in stages] == ["roleplay-C-recovery-1", "agent-analysis-recovery-1"]
    assert run.result["native_review"]["critical_count"] == 0
    assert len(run.cache["panel"]["investors"]) == 66


def test_recovery_content_hash_ignores_random_execution_ids_not_fact_changes():
    old = {"request_id": "random-1", "generated_at": "old", "dim_commentary": {"0_basic": "fact 12"}}
    new = {"request_id": "random-2", "generated_at": "new", "dim_commentary": {"0_basic": "fact 12"}}
    assert t3.recovery_content_hash(old) == t3.recovery_content_hash(new)
    new["dim_commentary"]["0_basic"] = "fact 13"
    assert t3.recovery_content_hash(old) != t3.recovery_content_hash(new)


def test_native_schema_valid_main_revision_is_not_rejected_by_local_field_whitelist(run_offline):
    run = run_offline("warning_rewrite")
    assert run.result["native_recovery"]["stages"][0]["status"] == "completed"
    assert run.cache["agent_analysis"]["raw_fixed"] is False
    assert run.cache["synthesis"]["dashboard"]["battle_plan"] == {"entry": 12, "stop": 10}
    assert run.cache["synthesis"]["overall_score"] == 50
    assert run.counters["qualitative"] == 3 and run.counters["roleplay"] == 5
    assert run.counters["main"] == 2
    assert run.result["native_review"]["critical_count"] == 0


def test_unacknowledged_warning_is_not_an_extra_native_html_critical(run_offline):
    run = run_offline("warning_unacknowledged")
    assert run.counters["assemble"] == 1
    assert run.result["native_review"]["warning_count"] == 1
    assert run.result["workflow_completion"]["passed"] is False
    assert run.result["report_delivery_allowed"] is False
    assert run.result["native_recovery"]["stop_reason"] == "no_content_progress"


def test_qualified_check_acknowledgement_is_recognized_but_not_for_a_critical():
    issue = {"severity": "warning", "category": "self-check", "dim": "review-engine",
             "issue": "check check_industry_mapping_sanity 自己炸了: TypeError: NoneType"}
    agent = {"review_acknowledged": {"review-engine.check_industry_mapping_sanity": "The native check crashed on the frozen null field; no source or check was changed."}}
    assert t3.pending_native_issues({"issues": [issue]}, agent) == []
    critical = {**issue, "severity": "critical"}
    assert t3.pending_native_issues({"issues": [critical]}, agent) == [critical]
    unrelated = {**issue, "issue": "check check_metals_materials_populated 自己炸了: TypeError: NoneType"}
    assert t3.pending_native_issues({"issues": [unrelated]}, agent) == [unrelated]


@pytest.mark.parametrize("key", ["warning_12_capital_flow", "warning:12_capital_flow", "warning.12_capital_flow"])
def test_native_warning_ack_does_not_require_an_unprefixed_dictionary_key(key):
    # Actual T3 recovery used warning_12_capital_flow. The pinned SKILL asks
    # for an explicit reason per warning, not one exact dictionary key shape.
    issue = {"severity": "warning", "category": "data", "dim": "12_capital_flow",
             "issue": "维度 12_capital_flow data 为空 (crash)"}
    agent = {"review_acknowledged": {key:
        "已按维度记录真实原因：native universe scan skipped for scoped recovery；未执行 suggested_fix，因此该维度仍为空。"}}
    original = deepcopy(agent)
    assert t3.pending_native_issues({"issues": [issue]}, agent) == []
    assert agent == original
    critical = {**issue, "severity": "critical"}
    assert t3.pending_native_issues({"issues": [critical]}, agent) == [critical]


@pytest.mark.parametrize("key,value", [
    ("warning_2_capital_flow", "Different dimension"),
    ("warning_12_capital_flow_extra", "Not an exact dimension identity"),
    ("warning_12_capital_flow", " "),
    ("warning_12_capital_flow", True),
    ("warnings_acknowledged", "All warnings acknowledged"),
])
def test_warning_ack_still_needs_a_specific_issue_and_textual_reason(key, value):
    issue = {"severity": "warning", "category": "data", "dim": "12_capital_flow",
             "issue": "维度 12_capital_flow data 为空 (crash)"}
    assert t3.pending_native_issues({"issues": [issue]},
        {"review_acknowledged": {key: value}}) == [issue]


def test_shared_deadline_blocks_recovery_without_a_new_request(monkeypatch, tmp_path):
    issue = {"severity": "critical", "category": "consistency", "dim": "0_basic",
             "issue": "placeholder", "suggested_fix": "Revise the commentary"}
    review = {"critical_count": 1, "warning_count": 0, "issues": [issue]}
    cache = {}
    module = SimpleNamespace(review_all=lambda _ticker: deepcopy(review),
                             write_review=lambda _ticker, value: cache.update(review=value))
    runner = SimpleNamespace(clock=lambda: 100, deadline_at=100,
                             json_stage=lambda *_args, **_kwargs: pytest.fail("Expired deadline cannot start a model"))
    monkeypatch.setattr(t3, "write_new", lambda *_args: None)
    result = asyncio.run(t3.recover_native_review(runner=runner, modules={"lib.self_review": module},
        output=tmp_path, ticker="FIXTURE.SH", raw={}, dimensions={}, panel={"investors": []},
        agent={"dim_commentary": {"0_basic": "[TODO]"}}, qualitative={}, data_gaps={}, skill="", task3="",
        native_validate=lambda _value: [], synthesize=lambda *_args: pytest.fail("Nothing was repaired")))
    assert result[-1]["stop_reason"] == "shared_deadline_exceeded"
    assert result[-1]["stages"] == []
    assert cache["review"]["critical_count"] == 1
