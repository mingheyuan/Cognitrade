"""Frozen native computation reuse is explicit, source-bound and read-only."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import run_uzi_agent_comparison as t3


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return t3.file_binding(path)


@pytest.fixture
def recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(t3, "ROOT", tmp_path)
    source = tmp_path / "workspace/reports/old"
    reference = tmp_path / "pristine"
    module = reference / "skills/deep-analysis/scripts/lib/pipeline/score_fns.py"
    module.parent.mkdir(parents=True)
    module.write_text("# Offline native source fixture\n")
    native = {"path": str(reference), "commit": t3.PIN,
              "files": {str(module.relative_to(reference)): t3.file_binding(module)["sha256"]}}
    raw = {"ticker": "FIXTURE.SH", "dimensions": {"0_basic": {"data": {"price": 12}}}}
    inputs = {"raw": save(source / "raw.json", raw), "auxiliary": save(source / "aux.json", {})}
    manifest = {"version": t3.MANIFEST_VERSION, "native_reference": native, "profile": "medium",
                "model_configuration": {"model": "offline"}, "data_boundary": t3.BOUNDARY,
                "research_groups": [], "inputs": inputs}
    source_manifest = source / "manifest.json"
    save(source_manifest, manifest)
    derived = deepcopy(raw)
    derived["_integrity"] = {"computed": True}
    for dim in ("20_valuation_models", "21_research_workflow", "22_deep_methods"):
        derived["dimensions"][dim] = {"data": {"date": "2026-09-12"}}
    computation = {"raw": derived, "features": {"price": 12}, "features_before_derivation": {},
                   "feature_stage": "native_stage1_post_derivation_agent_input",
                   "dimensions": {"0_basic": 50}, "panel": {"investors": [{"score": 50}]}}
    save(source / "reference/native-computation.json", computation)
    save(source / "reference/native-data-gaps.json", {"tasks": []})
    save(source / "reference/native-source-audit.json", {
        "native_modules": {"lib.pipeline.score_fns": t3.file_binding(module)},
        "host_business_modules_imported": []})
    save(source / "reference/result.json", {"status": "agent_workflow_incomplete",
        "changed_input_files": [], "isolation_events": [], "host_source_unchanged": True})
    return SimpleNamespace(source=source, source_manifest=source_manifest,
                           manifest=manifest, computation=computation)


def bind(recorded):
    plan = t3.prepare_reference_computation_reuse(recorded.source_manifest, recorded.manifest)
    return {**recorded.manifest, "reference_computation_reuse": plan}


def test_default_never_implicitly_reuses_computation(recorded):
    assert t3.verify_reference_computation_reuse(recorded.manifest) is None


def test_explicit_native_replay_keeps_original_dates_and_files(recorded):
    before = {str(p): p.read_bytes() for p in recorded.source.rglob("*") if p.is_file()}
    manifest = bind(recorded)
    result = t3.verify_reference_computation_reuse(manifest)
    assert result["computation"] == recorded.computation
    assert result["data_gaps"] == {"tasks": []}
    assert result["provenance"]["execution"] == "reused_recorded_native_computation_not_recomputed"
    result["computation"]["raw"]["ticker"] = "CHANGED"
    assert t3.verify_reference_computation_reuse(manifest)["computation"] == recorded.computation
    assert before == {str(p): p.read_bytes() for p in recorded.source.rglob("*") if p.is_file()}


@pytest.mark.parametrize("name", ["native-computation", "native-data-gaps", "native-source-audit", "result"])
def test_tampered_bound_record_is_rejected(recorded, name):
    manifest = bind(recorded)
    path = recorded.source / "reference" / f"{name}.json"
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError):
        t3.verify_reference_computation_reuse(manifest)


@pytest.mark.parametrize("change", ["raw_fact", "input_hash", "host_import", "native_hash", "isolation", "active", "profile"])
def test_incompatible_or_untrusted_computation_cannot_be_bound(recorded, change):
    if change == "raw_fact":
        recorded.computation["raw"]["dimensions"]["0_basic"]["data"]["price"] = 999
        save(recorded.source / "reference/native-computation.json", recorded.computation)
    elif change == "input_hash":
        recorded.manifest["inputs"] = deepcopy(recorded.manifest["inputs"])
        recorded.manifest["inputs"]["raw"]["sha256"] = "changed"
    elif change == "profile":
        recorded.manifest["profile"] = "lite"
    elif change == "active":
        (recorded.source / ".active-worker").write_text("fixture")
    elif change == "isolation":
        save(recorded.source / "reference/result.json", {"changed_input_files": [],
            "isolation_events": ["network"], "host_source_unchanged": True})
    else:
        path = recorded.source / "reference/native-source-audit.json"
        audit = json.loads(path.read_text())
        if change == "host_import":
            audit["host_business_modules_imported"] = ["domain_packs.injected"]
        else:
            audit["native_modules"]["lib.pipeline.score_fns"]["sha256"] = "changed"
        save(path, audit)
    with pytest.raises(ValueError):
        bind(recorded)
