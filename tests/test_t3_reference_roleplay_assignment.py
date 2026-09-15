"""Capture the actual reference bridge offline; never import native/live services.

The synthetic seats preserve the observed 66-seat partition and the legacy
51/52-seat response shapes, without depending on audit files or model prose.
"""

import asyncio
import json
import sys
import time
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

from tests.codex_comparison_fakes import model_transport
import pytest

from scripts import run_uzi_agent_comparison as t3

SCHOOL_SIZES = {"A": 6, "B": 9, "C": 7, "D": 4, "E": 7, "F": 24, "G": 4, "H": 4, "I": 1}
ASSIGNED_SCHOOLS = {"A+B": "AB", "C": "C", "D+E": "DE", "F": "F", "G+H+I": "GHI"}
EXPECTED_COUNTS = {"A+B": 15, "C": 7, "D+E": 11, "F": 24, "G+H+I": 9}
LEGACY_SCHOOL_SIZES = {"A": 6, "B": 4, "C": 5, "D": 4, "E": 6, "F": 23, "G": 3}
ASSIGNMENT_PREFIX = "ROLEPLAY_ASSIGNMENT="


def seat(school, index):
    return {
        "investor_id": f"seat_{school.lower()}_{index:02d}",
        "group": school,
        "signal": "neutral",
        "score": 50,
        "headline": "Synthetic archived metric equals 12.",
        "reasoning": "Offline fixture reasoning based only on a synthetic frozen observation.",
    }


def native_rows():
    return [seat(school, index) for school, size in SCHOOL_SIZES.items() for index in range(size)]


def assigned_rows(group):
    return [row for row in native_rows() if row["group"] in ASSIGNED_SCHOOLS[group]]


def legacy_response(group):
    rows = [seat(school, index) for school, size in LEGACY_SCHOOL_SIZES.items() for index in range(size)]
    if group in {"A+B", "G+H+I"}:
        rows.append(seat("I", 0))
    elif group == "F":
        rows.append(seat("F", 23))
    return {"investors": rows}


def input_payload(request):
    # JSON data stays on its own line; later lines may carry repair feedback.
    for line in request["input"].splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and "raw" in value:
            return value
    raise AssertionError("Actual request must contain the frozen input object")


def instruction_assignment(request):
    lines = [line for line in request["instructions"].splitlines() if line.startswith(ASSIGNMENT_PREFIX)]
    assert len(lines) == 1, "Actual reference request must explicitly assign one group in instructions"
    return json.loads(lines[0].removeprefix(ASSIGNMENT_PREFIX))


@pytest.fixture
def captured_reference(monkeypatch, tmp_path, request):
    """Run real reference_work/StageRunner through a fully fake SDK boundary."""
    mode = getattr(request, "param", None)
    reference = tmp_path / "offline-reference"
    scripts = reference / "skills/deep-analysis/scripts"
    output = tmp_path / "offline-output"
    modules = {}
    for name in (
        "lib.stock_features", "compute_deep_methods", "lib.pipeline.score_fns", "lib.self_review",
        "lib.agent_analysis_validator", "lib.quant_signal", "lib.analysis_profile", "lib.cache", "assemble_report",
        "lib.data_integrity", "run_real_test", "inline_assets",
    ):
        modules[name] = SimpleNamespace(__file__=str(scripts / (name.replace(".", "/") + ".py")))
    modules["lib.stock_features"].extract_features = lambda *_args: {"synthetic_metric": 12}
    modules["lib.stock_features"].sanitize_features = deepcopy
    modules["lib.data_integrity"].refresh_recovery_artifact = lambda *_args: None
    modules["lib.cache"].read_task_output = lambda *_args: None
    for name in ("compute_dim_20", "compute_dim_21", "compute_dim_22"):
        setattr(modules["compute_deep_methods"], name, lambda *_args: {"data": {}})
    score = modules["lib.pipeline.score_fns"]
    score.score_dimensions = lambda _raw: {}
    score.generate_panel = lambda *_args: {"investors": native_rows()}
    score.generate_synthesis = lambda *_args, **_kwargs: {}
    modules["lib.analysis_profile"].get_profile = lambda _name: {}
    modules["lib.analysis_profile"].apply_profile_to_env = lambda _profile: None
    quant = modules["lib.quant_signal"]
    quant._fetch_top_holdings = lambda *_args: pytest.fail("No native acquisition is allowed")
    quant._fetch_all_holding_funds = lambda *_args: pytest.fail("No native acquisition is allowed")

    original_import = t3.importlib.import_module
    monkeypatch.setattr(t3.importlib, "import_module", lambda name: modules[name] if name in modules else original_import(name))
    # reference_work modifies these process-local values; restore them on fixture exit.
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("OPENAI_API_KEY", "offline-placeholder-not-a-real-key")
    monkeypatch.setenv("UZI_SKIP_REVIEW", "offline-before-test")
    monkeypatch.setenv("UZI_SCHOOL", "offline-before-test")
    original_binding, original_reader = t3.file_binding, t3.read_bound
    monkeypatch.setattr(t3, "file_binding", lambda path: original_binding(path) if path.exists()
                        else {"path": str(path), "sha256": "offline-fixture"})
    inputs = {"raw": {"ticker": "FIXTURE.SH", "dimensions": {}}, "auxiliary": {}}
    monkeypatch.setattr(t3, "read_bound", lambda binding: deepcopy(inputs[binding["fixture"]])
                        if "fixture" in binding else original_reader(binding))
    native_texts = {
        "skills/deep-analysis/SKILL.md": "OFFLINE_MAIN_ANALYSIS_REFERENCE",
        "skills/deep-analysis/references/task2.5-qualitative-deep-dive.md": "OFFLINE_QUALITATIVE_REFERENCE",
        "skills/deep-analysis/references/task3-agent-evaluation.md": (
            "OFFLINE_TASK3_REFERENCE: Full coordinator workflow for 51 seats.\n"
            "Legacy groups A+B (10), C (5), D+E (10), F (23), G (3).\n"
            "Each sub-agent returns its assigned investors; the coordinator merges the groups."
        ),
        "skills/investor-panel/references/quotes-knowledge-base.md": "Offline global background, not an assignment.",
        "skills/deep-analysis/scripts/lib/investor_personas.py": "# Offline global persona reference only",
    }
    monkeypatch.setattr(t3, "native_text", lambda _reference, path: native_texts[path])
    writes = {}

    def remember_write(path, value):
        assert path.name not in writes, "Attempt artifacts must not be overwritten"
        writes[path.name] = deepcopy(value)

    monkeypatch.setattr(t3, "write_new", remember_write)
    captures = {group: [] for group in ASSIGNED_SCHOOLS}
    qualitative_calls = []
    client_constructions = []
    expected_by_ids = {
        tuple(row["investor_id"] for row in assigned_rows(group)): group for group in ASSIGNED_SCHOOLS
    }

    class FakeClient:
        def __init__(self):
            client_constructions.append(True)
            self.responses = self
            self.with_raw_response = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def create(self, *, timeout, **request):
            assert timeout > 0
            payload = input_payload(request)
            if "rules" in payload:
                ids = tuple(row["investor_id"] for row in payload["rules"])
                assert ids in expected_by_ids, "Bridge must pass exactly one real assigned rule partition"
                group = expected_by_ids[ids]
                captures[group].append(deepcopy(request))
                response_value = legacy_response(group)
            else:
                assert "OFFLINE_QUALITATIVE_REFERENCE" in request["instructions"], "Must stop before main analysis"
                qualitative_calls.append(deepcopy(request))
                marker = "指定维度 "
                start = request["input"].index(marker) + len(marker)
                dims, _ = json.JSONDecoder().raw_decode(request["input"][start:])
                response_value = {
                    dim: {
                        "evidence": [{"source": "unknown", "finding": "Unknown in this offline frozen fixture."}],
                        "associations": [],
                        "conclusion": "Offline fixture explicitly retains the source gap.",
                    }
                    for dim in dims
                }
            text = json.dumps(response_value)
            response = SimpleNamespace(output_text=text, model_dump=lambda **_kwargs: {"output_text": text})
            return SimpleNamespace(status_code=200, headers={"x-request-id": "offline"}, parse=lambda: response)

    monkeypatch.setattr(t3, "codex_model_transport", model_transport(FakeClient))
    manifest = {
        "native_reference": {"path": str(reference)},
        "profile": "medium",
        "inputs": {name: {"fixture": name} for name in inputs},
        "model_configuration": {
            "model": "offline-fixture-model", "base_url": "https://offline.invalid/v1",
            "model_concurrency": 4, "request_timeout_seconds": 600,
            "store": False, "service_tier": "default", "temperature": 0.2, "reasoning_effort": "high",
        },
    }
    if mode:
        monkeypatch.setattr(t3, "ROOT", tmp_path)
        source = tmp_path / "workspace/reports/old"
        source.mkdir(parents=True)

        def write_source(relative, value):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
            return original_binding(path)

        manifest.update(version=t3.MANIFEST_VERSION, data_boundary=t3.BOUNDARY,
            research_groups=[{"group": name, "dimensions": list(dims)} for name, dims in t3.RESEARCH],
            inputs={name: write_source(name + ".json", value) for name, value in inputs.items()})
        old = deepcopy(manifest)
        old["driver"] = {"sha256": "old-driver-before-roleplay-repair"}
        write_source("manifest.json", old)
        old_raw = deepcopy(inputs["raw"])
        old_raw["dimensions"] = {dim: {"data": {}} for dim in (
            "20_valuation_models", "21_research_workflow", "22_deep_methods",
        )}
        write_source("reference/native-computation.json", {"raw": old_raw})
        old_requests = t3.current_qualitative_requests(manifest, old_raw, [])
        summary = []
        for group, dims in t3.RESEARCH:
            stage = f"qualitative-{group}"
            value = {dim: {"evidence": [{"source": "unknown", "finding": "Unknown frozen fact."}],
                           "associations": [], "conclusion": "Unknown frozen fact remains."} for dim in dims}
            response = {"output_text": t3.encoded(value), "body": {"output_text": t3.encoded(value)}, "http_status": 200}
            write_source(f"reference/{stage}-attempt-1.json", {
                "stage": stage, "attempt": 1, "status": "completed", "http_status": 200,
                "validation_scope": "driver_structure_not_native_CHECKS", "validation_suggestions": [],
                "request": old_requests[stage], "prompt_sha256": t3.digest(t3.encoded(old_requests[stage]).encode()),
                "response": response, "response_sha256": t3.digest(t3.encoded(response).encode()), "http_seconds": 123,
            })
            summary.append({"stage": stage, "status": "completed", "attempts": 1,
                            "value": value, "validation_suggestions": []})
        write_source("reference/qualitative-stages.json", summary)
        manifest["reference_qualitative_reuse"] = t3.prepare_reference_qualitative_reuse(source / "manifest.json", manifest)
        if mode == "fresh_mismatch":
            modules["compute_deep_methods"].compute_dim_20 = lambda *_args: {"data": {"new_fresh_fact": 1}}
        elif mode == "source_drift":
            path = source / "reference/qualitative-C-attempt-1.json"
            path.write_text(path.read_text() + "\n")
    error = None
    try:
        result = asyncio.run(t3.reference_work(
            manifest, output, SimpleNamespace(model_io=nullcontext), deadline_at=time.monotonic() + 600,
        ))
    except ValueError as exc:
        if mode not in {"fresh_mismatch", "source_drift"}:
            raise
        error, result = exc, None
    return SimpleNamespace(requests=captures, result=result, writes=writes, qualitative_calls=qualitative_calls,
                           client_constructions=client_constructions, error=error)


@pytest.mark.parametrize("group", ASSIGNED_SCHOOLS)
def test_actual_reference_request_has_explicit_group_ids_and_count_in_instructions(captured_reference, group):
    request = captured_reference.requests[group][0]
    assignment = instruction_assignment(request)
    assert assignment == {
        "group": group,
        "required_investor_ids": [row["investor_id"] for row in assigned_rows(group)],
        "required_investor_count": EXPECTED_COUNTS[group],
    }
    assert "只处理当前子组" in request["instructions"]
    assert "不是主控" in request["instructions"]
    assert "不得按旧51人数扩大边界" in request["instructions"]
    assert input_payload(request)["rules"] == assigned_rows(group)


@pytest.mark.parametrize("group", ASSIGNED_SCHOOLS)
def test_actual_reference_repairs_retain_same_explicit_group_assignment(captured_reference, group):
    requests = captured_reference.requests[group]
    assert len(requests) == 3
    assignments = [instruction_assignment(request) for request in requests]
    assert assignments == [assignments[0]] * 3
    assert assignments[0]["required_investor_ids"] == [row["investor_id"] for row in assigned_rows(group)]
    assert all(request["instructions"] == requests[0]["instructions"] for request in requests)
    assert "Output validation suggestions" not in requests[0]["input"]
    assert all("Output validation suggestions" in request["input"] for request in requests[1:])


def test_actual_reference_requests_keep_exact_66_seat_partition(captured_reference):
    assert len(captured_reference.qualitative_calls) == 3
    ids = []
    for group, count in EXPECTED_COUNTS.items():
        rows = input_payload(captured_reference.requests[group][0])["rules"]
        assert rows == assigned_rows(group)
        assert len(rows) == count
        ids.extend(row["investor_id"] for row in rows)
    assert len(ids) == 66
    assert Counter(ids) == Counter(row["investor_id"] for row in native_rows())
    assert all(count == 1 for count in Counter(ids).values())


def test_actual_reference_keeps_all_five_legacy_rosters_invalid_without_filtering(captured_reference):
    result = captured_reference.result
    assert result["status"] == "agent_stage_incomplete" and result["stage"] == "roleplay"
    assert result["new_model_requests"] == result["model_requests"] == 18
    assert len(result["stages"]) == 5
    assert "agent-analysis-attempt-1.json" not in captured_reference.writes
    for group, expected_size in {"A+B": 52, "C": 51, "D+E": 51, "F": 52, "G+H+I": 52}.items():
        stage = next(row for row in result["stages"] if row["stage"] == f"roleplay-{group}")
        assert stage["status"] == "output_invalid" and stage["attempts"] == 3
        assert stage["value"] == legacy_response(group)
        assert len(stage["value"]["investors"]) == expected_size
        assert stage["validation_suggestions"] == ["Return exactly all requested native investor IDs once"]
        for attempt in range(1, 4):
            artifact = captured_reference.writes[f"roleplay-{group}-attempt-{attempt}.json"]
            assert artifact["status"] == "output_invalid"
            assert artifact["response"]["sdk_metadata"]["status"] == "completed"
            assert "http_status" not in artifact


@pytest.mark.parametrize("group", ASSIGNED_SCHOOLS)
def test_legacy_roster_replay_rejects_extra_seats_without_mutating_response(group):
    response = legacy_response(group)
    before = deepcopy(response)
    assert t3.roleplay_issues(response, assigned_rows(group)) == ["Return exactly all requested native investor IDs once"]
    assert response == before


@pytest.mark.parametrize("group", ASSIGNED_SCHOOLS)
@pytest.mark.parametrize("violation", ["missing", "extra", "duplicate"])
def test_requested_membership_gate_rejects_missing_extra_duplicate_and_keeps_skip(group, violation):
    expected = assigned_rows(group)
    valid = {"investors": deepcopy(expected)}
    valid["investors"][0]["signal"] = "skip"
    assert t3.roleplay_issues(valid, expected) == []
    invalid = deepcopy(valid)
    if violation == "missing":
        invalid["investors"].pop()
    elif violation == "duplicate":
        invalid["investors"].append(deepcopy(invalid["investors"][0]))
    else:
        invalid["investors"].append(next(row for row in native_rows() if row["group"] not in ASSIGNED_SCHOOLS[group]))
    before = deepcopy(invalid)
    assert t3.roleplay_issues(invalid, expected) == ["Return exactly all requested native investor IDs once"]
    assert invalid == before


@pytest.mark.parametrize("captured_reference", ["reuse"], indirect=True)
def test_actual_reference_reuses_only_three_stages_without_new_qualitative_calls(captured_reference):
    assert captured_reference.qualitative_calls == []
    assert len(captured_reference.client_constructions) == 1
    result = captured_reference.result
    assert result["new_model_requests"] == result["model_requests"] == 15
    assert result["status"] == "agent_stage_incomplete" and result["stage"] == "roleplay"
    assert len(result["reused_reference_qualitative"]) == 3
    assert all(row["execution"] == "reused_not_new_model_execution" for row in result["reused_reference_qualitative"])
    assert captured_reference.writes["qualitative-reuse.json"]["fresh_computation_validation"] == "passed"
    assert captured_reference.writes["qualitative-completion.json"]["passed"] is False
    assert all(row["attempts"] == 0 for row in captured_reference.writes["qualitative-stages.json"])
    assert not any(name.startswith("qualitative-") and "-attempt-" in name for name in captured_reference.writes)


@pytest.mark.parametrize("captured_reference", ["fresh_mismatch", "source_drift"], indirect=True)
def test_actual_reference_reuse_mismatch_stops_before_sdk_client_or_paid_fallback(captured_reference):
    assert isinstance(captured_reference.error, ValueError)
    assert captured_reference.result is None
    assert captured_reference.client_constructions == []
    assert captured_reference.qualitative_calls == []
    assert all(rows == [] for rows in captured_reference.requests.values())
