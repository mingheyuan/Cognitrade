from datetime import date, datetime, timezone

import pytest

from research_system.api.routes.market_data_center import CollectionJobRequest
from scripts.run_uzi_single_stock_probe import (
    AuditWriter,
    collect_request,
    export_frozen_manifest,
    job_state,
    run_collect,
    run_freeze,
)


def test_payload_uses_real_local_today_and_api_fields_only():
    payload = collect_request(now=datetime(2026, 9, 8, 17, tzinfo=timezone.utc))
    assert payload["start_date"] == payload["end_date"] == "2026-09-09"
    assert payload["symbols"] == ["688041.SH"]
    assert payload["mode"] == "force_all" and payload["dimensions"] == "all"
    assert payload["publish_when_complete"] is False
    CollectionJobRequest.model_validate(payload)
    assert collect_request(as_of=date(2026, 9, 2))["start_date"] == "2026-09-02"


@pytest.mark.parametrize(
    "stage",
    [
        "native_postfetch",
        "research_archive",
        "research_enrichment",
        "company_profile_dependency_rebuild",
        "native_quant_auxiliary",
        "agent_recovery",
    ],
)
def test_child_stage_prevents_false_terminal_even_after_cancel(stage):
    for main in ("succeeded", "cancelled"):
        state = job_state({"status": main, "progress": {stage: {"status": "running"}}})
        assert not state["terminal"]
    assert (
        job_state(
            {"status": "cancelled", "checkpoint": {stage: {"status": "cancelled"}}}
        )["outcome"]
        == "cancelled"
    )


def test_collect_requires_explicit_authorization_and_never_freezes(tmp_path):
    calls = []
    states = iter(
        [
            {
                "id": "job",
                "status": "succeeded",
                "progress": {"native_quant_auxiliary": {"status": "running"}},
            },
            {
                "id": "job",
                "status": "succeeded",
                "progress": {
                    "native_quant_auxiliary": {
                        "status": "unassessed",
                        "reason_code": "deadline_exhausted",
                    }
                },
            },
        ]
    )

    def api(method, path, payload=None):
        calls.append((method, path))
        return {"id": "job", "status": "queued"} if method == "POST" else next(states)

    writer = AuditWriter(tmp_path / "audit")
    with pytest.raises(ValueError, match="execute"):
        run_collect(api, writer, collect_request(), execute=False)
    assert calls == []
    result = run_collect(
        api, writer, collect_request(), execute=True, sleep=lambda _: None
    )
    assert result["job_id"] == "job"
    assert result["outcome"] == "completed_with_input_gaps"
    assert sum(method == "POST" for method, _ in calls) == 1
    assert not any("freeze" in path or "recovery" in path for _, path in calls)
    assert len(list(writer.output.glob("poll-*.json"))) == 2


def test_profile_rebuild_gap_is_not_reported_as_complete_collection():
    state = job_state({
        "status": "succeeded",
        "checkpoint": {"company_profile_dependency_rebuild": {
            "status": "partial", "reason_code": "missing_original_documents",
        }},
    })
    assert state["terminal"]
    assert state["outcome"] == "completed_with_input_gaps"
    assert state["stages"]["company_profile_dependency_rebuild"]["reason_code"] == "missing_original_documents"


def test_audit_files_never_overwrite(tmp_path):
    writer = AuditWriter(tmp_path / "audit")
    first = writer.write("proof", {"original": True})
    with pytest.raises(FileExistsError):
        writer.write("proof", {"original": False})
    assert len(first["file_sha256"]) == 64


def test_freeze_never_silently_acknowledges_degradation(tmp_path):
    calls = []

    def api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET":
            return {
                "id": "job",
                "status": "partial",
                "can_confirm_freeze": True,
                "publication_candidate": {"proposal_hash": "a" * 64},
            }
        assert payload["acknowledge_degraded"] is False
        raise RuntimeError("collection_freeze_degraded_ack_required")

    with pytest.raises(RuntimeError, match="degraded_ack"):
        run_freeze(
            api,
            AuditWriter(tmp_path / "freeze"),
            job_id="job",
            symbol="688041.SH",
            as_of=date(2026, 9, 8),
            execute=True,
        )
    assert not any(path.endswith("report-manifests") for _, path, _ in calls)


def test_exact_materialized_export_keeps_raw_recording_and_old_input_unchanged(
    tmp_path,
):
    import json
    from copy import deepcopy

    from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
        build_uzi_raw,
    )
    from research_system.market_data.quant_auxiliary import quant_input_hash
    from tests.test_market_data_report_inputs import _manifest

    manifest = _manifest("600519.SH")
    item = manifest["items"][0]
    raw = build_uzi_raw(item["snapshots"], "600519.SH")
    binding = {
        "version": "native-quant-aux-v1",
        "status": "unassessed",
        "input_hash": quant_input_hash(raw),
    }
    manifest["workflow_pins"] = {
        "native_quant_auxiliary_version": "native-quant-aux-v1"
    }
    item["context"] = {"native_quant_auxiliary": binding}
    item["native_quant_auxiliary_recording"] = {
        "binding": binding,
        "receipt": None,
        "responses": {},
    }
    original = deepcopy(manifest)
    exported = export_frozen_manifest(manifest, AuditWriter(tmp_path / "fixed"))
    assert exported["business_fetch_calls"] == exported["model_calls"] == 0
    assert exported["stocks"][0]["auxiliary_status"] == "unassessed"
    assert set(exported["stocks"][0]["outputs"]) == {
        "raw",
        "snapshots",
        "auxiliary",
        "evidence",
    }
    assert json.loads(
        (tmp_path / "fixed" / "600519_SH-auxiliary.json").read_text()
    ) == {
        "binding": binding,
        "recording": item["native_quant_auxiliary_recording"],
    }
    assert manifest == original
    item["native_quant_auxiliary_recording"]["binding"] = {
        **binding,
        "input_hash": "wrong",
    }
    with pytest.raises(ValueError, match="binding_mismatch"):
        export_frozen_manifest(manifest, AuditWriter(tmp_path / "bad"))


def test_research_export_uses_only_fixed_six_dimension_document_relations():
    from copy import deepcopy

    from scripts.run_uzi_single_stock_probe import frozen_research_evidence

    document = {
        "type": "market_data_evidence",
        "source_kind": "research_document",
        "id": "document-1",
        "url": "https://example.test/published-report.pdf",
        "content": "Frozen archived excerpt, not a fresh download.",
        "content_hash": "a" * 64,
        "content_ref": "sha256:document",
        "published_at": "2026-09-08T01:00:00+00:00",
        "observed_at": "2026-09-08T02:00:00+00:00",
    }
    binding = {
        "snapshot_id": "macro-snapshot",
        "evidence_id": "document-1",
        "content_hash": document["content_hash"],
        "relation_id": "relation-1",
        "field_path": "raw",
        "purpose": "research",
    }
    item = {
        "symbol": "688041.SH",
        "bundle_id": "bundle-fixed",
        "bundle": {"cutoff_at": "2026-09-08T03:00:00+00:00"},
        "context": {"evidence_bindings": [binding]},
        "snapshots": [
            {
                "dimension_id": "3_macro",
                "snapshot_id": "macro-snapshot",
                "source_refs": [
                    document,
                    {"id": "api", "type": "market_data_evidence"},
                ],
            },
            {
                "dimension_id": "6_institution",
                "snapshot_id": "unrelated",
                "source_refs": [{**document, "id": "not-a-research-dimension"}],
            },
        ],
        "source_refs": [{**document, "id": "unrelated-summary-only"}],
    }
    manifest = {
        "manifest_id": "fixed",
        "manifest_hash": "b" * 64,
        "as_of_date": "2026-09-08",
    }
    original = deepcopy(item)
    result = frozen_research_evidence(manifest, item)
    assert result["documents"] == [document]
    assert result["evidence_bindings"] == [binding]
    assert result["dimensions"]["3_macro"] == {
        "snapshot_ids": ["macro-snapshot"],
        "evidence_ids": ["document-1"],
    }
    assert result["missing_dimensions"] == [
        "13_policy",
        "7_industry",
        "15_events",
        "8_materials",
        "9_futures",
    ]
    assert result["missing_inline_content_ids"] == []
    assert item == original
    item["snapshots"][0]["source_refs"].append({**document, "id": "newly-added-ref"})
    with pytest.raises(ValueError, match="binding mismatch"):
        frozen_research_evidence(manifest, item)


def test_default_cli_dry_run_never_reads_credentials_or_calls_api(
    tmp_path, monkeypatch
):
    from scripts import run_uzi_single_stock_probe as probe

    monkeypatch.setattr(
        probe, "configured_api", lambda _: pytest.fail("dry run cannot call API")
    )
    assert probe.main(["--stage", "collect", "--output", str(tmp_path / "dry")]) == 0
    assert not (tmp_path / "dry").exists()


@pytest.mark.parametrize(
    ("base_url_args", "expected_url"),
    [
        ([], "http://127.0.0.1:8080"),
        (["--base-url", "http://example.test:9000"], "http://example.test:9000"),
    ],
)
def test_cli_uses_web_port_by_default_and_preserves_explicit_override(
    tmp_path, monkeypatch, base_url_args, expected_url
):
    from scripts import run_uzi_single_stock_probe as probe

    configured_urls = []
    fake_api = object()

    def configure(url):
        configured_urls.append(url)
        return fake_api

    def collect(api, *_args, **_kwargs):
        assert api is fake_api
        return {"stage": "collect", "outcome": "collection_completed"}

    monkeypatch.setattr(probe, "configured_api", configure)
    monkeypatch.setattr(probe, "run_collect", collect)
    assert (
        probe.main(
            [
                "--stage",
                "collect",
                "--execute",
                "--output",
                str(tmp_path / "cli"),
                *base_url_args,
            ]
        )
        == 0
    )
    assert configured_urls == [expected_url]


def test_monitor_timeout_is_not_remote_task_timeout_or_cancellation(tmp_path):
    calls = []

    def api(method, path, payload=None):
        calls.append(method)
        return {"id": "existing", "status": "running"}

    result = run_collect(
        api,
        AuditWriter(tmp_path / "timeout"),
        collect_request(),
        execute=True,
        job_id="existing",
        max_wait=0,
    )
    assert result["outcome"] == "monitor_deadline_reached"
    assert result["remote_job_cancelled"] is False
    assert calls == []


def test_freeze_different_day_stops_before_business_write(tmp_path):
    calls = []

    def api(method, path, payload=None):
        calls.append(method)
        return {
            "id": "yesterday",
            "symbols": ["688041.SH"],
            "status": "succeeded",
            "start_date": "2026-09-07",
            "end_date": "2026-09-07",
        }

    with pytest.raises(ValueError, match="never relabel"):
        run_freeze(
            api,
            AuditWriter(tmp_path / "date"),
            job_id="yesterday",
            symbol="688041.SH",
            as_of=date(2026, 9, 8),
            execute=True,
        )
    assert calls == ["GET"]
