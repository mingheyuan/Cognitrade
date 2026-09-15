"""Explicit three-stage reuse is immutable, exact-request-bound, and fail closed."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_uzi_agent_comparison as t3


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))
    return t3.file_binding(path)


def sealed_request(config, task25, dims, raw, evidence):
    return {
        "model": config["model"], "instructions": t3.BOUNDARY + "\n" + task25,
        "input": "输出 JSON，顶层仅包含指定维度 " + t3.encoded(dims)
        + "。使用原生 evidence/associations/conclusion schema。\n"
        + t3.encoded({"raw": raw, "frozen_archived_evidence": evidence}),
        "text": {"format": {"type": "json_object"}}, "store": config["store"],
        "service_tier": config["service_tier"], "temperature": config["temperature"],
        "reasoning": {"effort": config["reasoning_effort"]},
    }


@pytest.fixture
def reuse_case(monkeypatch, tmp_path):
    monkeypatch.setattr(t3, "ROOT", tmp_path)
    reference = tmp_path / "pristine-reference"
    task_path = reference / "skills/deep-analysis/references/task2.5-qualitative-deep-dive.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text("OFFLINE native qualitative reference")
    native = {"path": str(reference), "commit": t3.PIN, "source_set_sha256": "offline-native",
              "files": {str(task_path.relative_to(reference)): t3.file_binding(task_path)["sha256"]}}
    monkeypatch.setattr(t3, "verify_reference", lambda _path: deepcopy(native))
    monkeypatch.setattr(t3, "host_source_binding", lambda: {"files": {}, "source_set_sha256": "new-host"})
    monkeypatch.setattr(t3, "verify_host_source", lambda _binding: None)
    settings = SimpleNamespace(default_model="offline-model", openai_base_url="https://offline.invalid/v1",
        codex_reasoning_effort="high", openai_service_tier="default", openai_disable_response_storage=True,
        uzi_llm_max_concurrency=4, uzi_llm_medium_request_timeout_seconds=600,
        strategy_backfill_batch_timeout_seconds=21600)
    monkeypatch.setattr(t3, "load_settings", lambda: settings)
    config = t3.public_config(settings, "medium")
    source = tmp_path / "workspace/reports/old"
    original_raw = {"ticker": "OFFLINE.SH", "dimensions": {}}
    derived_raw = deepcopy(original_raw)
    for dim in ("20_valuation_models", "21_research_workflow", "22_deep_methods"):
        derived_raw["dimensions"][dim] = {"data": {}}
    inputs = {
        "raw": write_json(source / "raw.json", original_raw),
        "auxiliary": write_json(source / "auxiliary.json", {}),
        "evidence": write_json(source / "evidence.json", []),
    }
    manifest = {
        "version": t3.MANIFEST_VERSION, "profile": "medium", "native_reference": native,
        "host_source": {"files": {}, "source_set_sha256": "different-old-host"},
        "model_configuration": config, "inputs": inputs, "data_boundary": t3.BOUNDARY,
        "driver": {"path": "/old/driver.py", "sha256": "different-old-driver"},
        "research_groups": [{"group": group, "dimensions": list(dims)} for group, dims in t3.RESEARCH],
    }
    write_json(source / "manifest.json", manifest)
    write_json(source / "reference/native-computation.json", {"raw": derived_raw, "features": {}, "panel": {}})
    requests, summaries = {}, []
    for group, dims in t3.RESEARCH:
        stage = f"qualitative-{group}"
        request = sealed_request(config, task_path.read_text(), dims, derived_raw, [])
        requests[stage] = request
        # Valid unknown disclosure is reusable structure, never complete research.
        value = {dim: {"evidence": [{"source": "unknown", "url": None, "retrieved_at": None,
                                    "finding": "Unknown in the frozen offline fixture."}],
                       "associations": [], "conclusion": "Frozen source gap remains."} for dim in dims}
        response = {"output_text": t3.encoded(value),
                    "sdk_metadata": {**config, "status": "completed", "simulated": True}}
        write_json(source / f"reference/{stage}-attempt-1.json", {
            "stage": stage, "attempt": 1, "status": "completed",
            "validation_scope": "driver_structure_not_native_CHECKS", "validation_suggestions": [],
            "request": request, "prompt_sha256": t3.digest(t3.encoded(request).encode()),
            "response": response, "response_sha256": t3.digest(t3.encoded(response).encode()),
            "http_seconds": 123.0, "elapsed_seconds": 130.0,
        })
        summaries.append({"stage": stage, "status": "completed", "attempts": 1, "value": value,
                          "validation_suggestions": []})
    write_json(source / "reference/qualitative-stages.json", summaries)
    write_json(source / "reference/result.json", {"status": "agent_stage_incomplete", "stage": "roleplay"})
    args = SimpleNamespace(output_directory=tmp_path / "workspace/reports/new", reference=reference,
        profile="medium", raw=Path(inputs["raw"]["path"]), auxiliary=Path(inputs["auxiliary"]["path"]),
        evidence=Path(inputs["evidence"]["path"]), materialized_snapshots=None, deadline_seconds=None,
        reuse_reference_qualitative_from=source / "manifest.json")
    return SimpleNamespace(args=args, source=source, manifest=manifest, requests=requests, raw=derived_raw)


def prepare_reuse(case):
    path = t3.prepare(case.args)
    manifest = json.loads(path.read_text())
    assert "reference_qualitative_reuse" in manifest, "Explicit reuse must be bound, not silently ignored"
    return manifest


def test_prepare_binds_exact_three_stages_despite_old_driver_and_later_overall_failure(reuse_case):
    before = {str(path): path.read_bytes() for path in reuse_case.source.rglob("*") if path.is_file()}
    manifest = prepare_reuse(reuse_case)
    plan = manifest["reference_qualitative_reuse"]
    assert plan["scope"] == "reference_qualitative_A_B_C_only"
    assert plan["fresh_computation_validation"] == "deferred_until_reference_worker_before_model_calls"
    assert [row["stage"] for row in plan["stages"]] == [f"qualitative-{group}" for group, _ in t3.RESEARCH]
    for name in ("source_manifest", "native_computation", "stage_summary"):
        assert t3.file_binding(Path(plan[name]["path"])) == plan[name]
    for row in plan["stages"]:
        assert t3.file_binding(Path(row["attempt_file"]["path"])) == row["attempt_file"]
        assert row["prompt_sha256"] == t3.digest(t3.encoded(reuse_case.requests[row["stage"]]).encode())
    assert before == {str(path): path.read_bytes() for path in reuse_case.source.rglob("*") if path.is_file()}


def test_verified_reuse_has_zero_new_attempts_no_old_timings_and_no_research_quality_claim(reuse_case):
    manifest = prepare_reuse(reuse_case)
    stages, sources = t3.verify_reference_qualitative_reuse(manifest, current_requests=reuse_case.requests)
    assert len(stages) == len(sources) == 3
    for stage, source in zip(stages, sources):
        assert stage["status"] == "completed" and stage["attempts"] == 0
        assert stage["execution"] == source["execution"] == "reused_not_new_model_execution"
        assert "http_seconds" not in stage and "elapsed_seconds" not in stage
        assert source["stage"] == stage["stage"]
        assert source["source_attempt"]["path"].endswith(stage["stage"] + "-attempt-1.json")
    qualitative = {dim: row for stage in stages for dim, row in stage["value"].items()}
    assert t3.qualitative_completion(qualitative)["passed"] is False


@pytest.mark.parametrize("field", ["profile", "model_configuration", "native_reference", "inputs", "data_boundary"])
def test_prepare_rejects_incompatible_old_source_before_reuse(reuse_case, field):
    old = deepcopy(reuse_case.manifest)
    if field == "profile":
        old[field] = "lite"
    elif field == "model_configuration":
        old[field]["model"] = "another-model"
    elif field == "native_reference":
        old[field]["source_set_sha256"] = "changed-native"
    elif field == "inputs":
        old[field]["evidence"] = write_json(reuse_case.source / "different-evidence.json", [{"finding": "different"}])
    else:
        old[field] = "different evidence boundary"
    write_json(reuse_case.source / "manifest.json", old)
    with pytest.raises(ValueError):
        t3.prepare(reuse_case.args)


@pytest.mark.parametrize("mutation", ["invalid_status", "repair_attempt", "prompt", "response_hash", "summary_value", "bad_structure", "active_source"])
def test_prepare_rejects_invalid_or_unbound_stage_without_implicit_rerun(reuse_case, mutation):
    path = reuse_case.source / "reference/qualitative-B-attempt-1.json"
    record = json.loads(path.read_text())
    summary_path = reuse_case.source / "reference/qualitative-stages.json"
    summary = json.loads(summary_path.read_text())
    if mutation == "invalid_status":
        record["status"] = "output_invalid"
    elif mutation == "repair_attempt":
        record["attempt"] = 2
    elif mutation == "prompt":
        record["request"]["instructions"] += "\nchanged scope"
        record["prompt_sha256"] = t3.digest(t3.encoded(record["request"]).encode())
    elif mutation == "response_hash":
        record["response"]["sdk_metadata"]["changed"] = True
    elif mutation == "summary_value":
        summary[1]["value"]["7_industry"]["conclusion"] = "Different conclusion"
    elif mutation == "bad_structure":
        summary[1]["value"] = {"unexpected": {}}
        record["response"]["output_text"] = t3.encoded(summary[1]["value"])
        record["response_sha256"] = t3.digest(t3.encoded(record["response"]).encode())
    else:
        (reuse_case.source / ".active-worker").write_text("reference:offline-active")
    write_json(path, record)
    write_json(summary_path, summary)
    with pytest.raises(ValueError):
        t3.prepare(reuse_case.args)


@pytest.mark.parametrize("target", ["source_manifest", "native_computation", "stage_summary", "A", "B", "C"])
def test_all_reuse_files_are_rechecked_after_prepare(reuse_case, target):
    manifest = prepare_reuse(reuse_case)
    plan = manifest["reference_qualitative_reuse"]
    binding = plan[target] if target in plan else next(
        row["attempt_file"] for row in plan["stages"] if row["stage"] == f"qualitative-{target}"
    )
    path = Path(binding["path"])
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="hash"):
        t3.verify_reference_qualitative_reuse(manifest, current_requests=reuse_case.requests)


@pytest.mark.parametrize("group", ["A", "B", "C"])
def test_freshly_recomputed_request_mismatch_rejects_the_entire_reuse_set(reuse_case, group):
    manifest = prepare_reuse(reuse_case)
    requests = deepcopy(reuse_case.requests)
    requests[f"qualitative-{group}"]["input"] += " changed fresh computation"
    with pytest.raises(ValueError, match="request|prompt"):
        t3.verify_reference_qualitative_reuse(manifest, current_requests=requests)


@pytest.mark.parametrize("side", ["reference", "host"])
def test_public_run_rechecks_reuse_files_before_credentials_or_worker_dispatch(reuse_case, monkeypatch, side):
    manifest = prepare_reuse(reuse_case)
    path = Path(manifest["reference_qualitative_reuse"]["stages"][0]["attempt_file"]["path"])
    path.write_text(path.read_text() + "\n")
    monkeypatch.setattr(t3, "load_settings", lambda: pytest.fail("Drift must stop before credentials"))
    with pytest.raises(ValueError, match="hash"):
        t3.run_manifest(reuse_case.args.output_directory / "manifest.json", side=side, allow_paid=True)


def test_no_explicit_reuse_flag_does_not_discover_prior_outputs(reuse_case):
    reuse_case.args.reuse_reference_qualitative_from = None
    manifest = json.loads(t3.prepare(reuse_case.args).read_text())
    assert "reference_qualitative_reuse" not in manifest


def test_prepare_cli_accepts_only_explicit_reuse_source(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(t3, "prepare", lambda args: seen.append(args) or Path("offline-manifest.json"))
    assert t3.main(["prepare", "--raw", "raw.json", "--auxiliary", "aux.json", "--output-directory", "new",
                   "--reuse-reference-qualitative-from", "old/manifest.json"]) == 0
    assert str(seen[0].reuse_reference_qualitative_from) == "old/manifest.json"
    assert "offline-manifest.json" in capsys.readouterr().out
