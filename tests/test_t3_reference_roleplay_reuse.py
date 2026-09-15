"""Exact-request continuation must not repeat successful native persona groups."""
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
    monkeypatch.setattr(t3, "native_text", lambda *_args: "offline-native")
    source = tmp_path / "workspace/reports/old"
    raw = {"ticker": "FIXTURE.SH", "dimensions": {}}
    rows = [{"investor_id": name, "group": schools[0], "score": 50,
             "signal": "neutral", "headline": "Frozen fact", "reasoning": "Frozen reasoning"}
            for name, schools in t3.GROUPS]
    computation = {"raw": raw, "features": {"price": 12}, "panel": {"investors": rows}}
    config = {"model": "offline", "store": False, "service_tier": "default",
              "temperature": .1, "reasoning_effort": "high"}
    manifest = {"version": t3.MANIFEST_VERSION, "profile": "medium", "data_boundary": t3.BOUNDARY,
                "native_reference": {"path": str(tmp_path / "native"), "commit": t3.PIN},
                "model_configuration": config, "inputs": {"raw": save(source / "raw.json", raw)},
                "roleplay_groups": [{"group": name, "schools": list(schools)} for name, schools in t3.GROUPS]}
    save(source / "manifest.json", manifest)
    save(source / "reference/native-computation.json", computation)
    research = [{"stage": "qualitative-A", "status": "completed", "value": {"3_macro": {"conclusion": "Known"}}}]
    save(source / "reference/qualitative-stages.json", research)
    save(source / "reference/result.json", {"status": "agent_stage_incomplete", "stage": "roleplay",
         "changed_input_files": [], "isolation_events": [], "host_source_unchanged": True})
    requests = t3.current_roleplay_requests(manifest, computation, {"3_macro": {"conclusion": "Known"}})
    stages = []
    for name, _ in t3.GROUPS:
        stage = "roleplay-" + name
        value = {"investors": [deepcopy(next(row for row in rows if row["investor_id"] == name))]}
        status = "deadline_exceeded" if name == "G+H+I" else "completed"
        stages.append({"stage": stage, "status": status, "attempts": 1,
                       "value": value if status == "completed" else None, "validation_suggestions": []})
        response = {"output_text": t3.encoded(value), "http_status": 200}
        save(source / f"reference/{stage}-attempt-1.json", {
            "stage": stage, "status": status, "attempt": 1, "http_status": 200,
            "validation_scope": "driver_structure_not_native_CHECKS", "validation_suggestions": [],
            "request": requests[stage], "prompt_sha256": t3.digest(t3.encoded(requests[stage]).encode()),
            "response": response, "response_sha256": t3.digest(t3.encoded(response).encode())})
    save(source / "reference/roleplay-stages.json", stages)
    return SimpleNamespace(source=source, manifest=manifest, requests=requests, computation=computation)


def bind(case):
    return {**case.manifest, "reference_roleplay_reuse":
            t3.prepare_reference_roleplay_reuse(case.source / "manifest.json", case.manifest)}


def test_default_does_not_discover_any_old_personas():
    assert t3.verify_reference_roleplay_reuse({}) == ({}, [])


def test_only_successful_groups_reused_with_zero_new_attempts(recorded):
    before = {str(p): p.read_bytes() for p in recorded.source.rglob("*") if p.is_file()}
    manifest = bind(recorded)
    reused, sources = t3.verify_reference_roleplay_reuse(manifest, current_requests=recorded.requests)
    assert list(reused) == ["roleplay-" + name for name in ("A+B", "C", "D+E", "F")]
    assert len(sources) == 4
    assert all(row["attempts"] == 0 and row["execution"] == "reused_not_new_model_execution" for row in reused.values())
    assert before == {str(p): p.read_bytes() for p in recorded.source.rglob("*") if p.is_file()}


@pytest.mark.parametrize("target", ["manifest", "native-computation", "qualitative-stages", "roleplay-stages", "result", "roleplay-F-attempt-1"])
def test_every_bound_file_is_rechecked(recorded, target):
    manifest = bind(recorded)
    path = recorded.source / ("manifest.json" if target == "manifest" else f"reference/{target}.json")
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError):
        t3.verify_reference_roleplay_reuse(manifest, current_requests=recorded.requests)


@pytest.mark.parametrize("mutation", ["request", "summary", "response", "repaired", "active", "isolation", "model", "input", "no_success"])
def test_mismatched_or_untrusted_record_cannot_be_reused(recorded, mutation):
    path = recorded.source / "reference/roleplay-F-attempt-1.json"
    attempt = json.loads(path.read_text())
    if mutation == "request":
        attempt["request"]["input"] += "changed"
        attempt["prompt_sha256"] = t3.digest(t3.encoded(attempt["request"]).encode())
    elif mutation == "response":
        attempt["response"]["output_text"] = "{}"
    elif mutation == "repaired":
        attempt["attempt"] = 2
    elif mutation == "active":
        (recorded.source / ".active-worker").write_text("fixture")
    elif mutation == "isolation":
        save(recorded.source / "reference/result.json", {"isolation_events": ["business_fetch"]})
    elif mutation == "model":
        recorded.manifest["model_configuration"]["model"] = "other"
    elif mutation == "input":
        recorded.manifest["inputs"]["raw"]["sha256"] = "changed"
    else:
        summary_path = recorded.source / "reference/roleplay-stages.json"
        summary = json.loads(summary_path.read_text())
        if mutation == "summary":
            summary[3]["value"]["investors"][0]["score"] = 0
        else:
            for row in summary:
                row["status"] = "deadline_exceeded"
        save(summary_path, summary)
    save(path, attempt)
    with pytest.raises(ValueError):
        bind(recorded)


def test_current_request_drift_stops_reuse_even_if_old_record_is_valid(recorded):
    manifest = bind(recorded)
    requests = deepcopy(recorded.requests)
    requests["roleplay-C"]["input"] += "new research or persona facts"
    with pytest.raises(ValueError, match="request"):
        t3.verify_reference_roleplay_reuse(manifest, current_requests=requests)
