"""Pure contracts for the explicitly opt-in T3 experiment; never call a model."""
import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from scripts import run_uzi_agent_comparison as t3


def settings():
    return SimpleNamespace(default_model="configured-model", openai_base_url="https://configured.invalid/v1",
        codex_reasoning_effort="high", openai_service_tier="default", openai_api_key="DO-NOT-SERIALIZE",
        openai_disable_response_storage=True, uzi_llm_max_concurrency=4,
        uzi_llm_medium_request_timeout_seconds=600, strategy_backfill_batch_timeout_seconds=21600)


def test_public_config_does_not_expose_credentials_or_increase_limits():
    config = t3.public_config(settings(), "medium")
    assert "DO-NOT-SERIALIZE" not in str(config)
    assert config["model"] == "configured-model"
    assert config["model_concurrency"] == 4
    assert config["request_timeout_seconds"] == 600
    with pytest.raises(ValueError, match="configured"):
        t3.checked_deadline(config, 21601)


def test_group_partition_covers_all_actual_seats_and_rejects_unknown_group():
    rows = [{"investor_id": f"seat-{i}", "group": "ABCDEFGHI"[i % 9]} for i in range(66)]
    groups = t3.roleplay_groups(rows)
    assert len(groups) == 5
    assert sorted(row["investor_id"] for _, group in groups for row in group) == sorted(row["investor_id"] for row in rows)
    with pytest.raises(ValueError, match="group"):
        t3.roleplay_groups([{"investor_id": "new", "group": "UNKNOWN"}])


def test_panel_overrides_preserve_original_summary_and_source():
    panel = {"consensus_score": 10, "bullish_count": 0,
             "investors": [{"investor_id": "a", "group": "A", "score": 10, "signal": "bearish"}]}
    original = deepcopy(panel)
    result = t3.apply_panel_rows(panel, [{"investor_id": "a", "score": 80, "signal": "bullish",
        "confidence": 70, "headline": "Fact 12", "reasoning": "Reasons", "comment": "Comment"}])
    assert result["investors"][0]["score"] == 80
    assert result["consensus_score"] == 10
    assert result["bullish_count"] == 0
    assert panel == original
    with pytest.raises(ValueError, match="seat"):
        t3.apply_panel_rows(panel, [])


def test_input_hash_drift_is_rejected(tmp_path):
    source = tmp_path / "raw.json"
    source.write_text('{"a":1}')
    ref = t3.file_binding(source)
    assert t3.read_bound(ref) == {"a": 1}
    source.write_text('{"a":2}')
    with pytest.raises(ValueError, match="hash"):
        t3.read_bound(ref)


def test_paid_run_requires_opt_in_before_settings_or_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(t3, "load_settings", lambda: pytest.fail("Settings should not be loaded"))
    with pytest.raises(ValueError, match="allow-paid"):
        t3.run_manifest(tmp_path / "does-not-exist", side="reference", allow_paid=False)


def test_v3_prepare_and_old_manifest_rejection_precede_model_or_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(t3, "ROOT", tmp_path)
    monkeypatch.setattr(t3, "verify_reference", lambda path: {"path": str(path), "commit": t3.PIN})
    monkeypatch.setattr(t3, "load_settings", settings)
    raw, auxiliary = tmp_path / "raw.json", tmp_path / "aux.json"
    raw.write_text('{"ticker":"688041.SH","dimensions":{}}')
    auxiliary.write_text('{}')
    output = tmp_path / "workspace/reports/new-v3"
    path = t3.prepare(SimpleNamespace(output_directory=output, reference=tmp_path, profile="medium",
        raw=raw, auxiliary=auxiliary, deadline_seconds=None))
    assert json.loads(path.read_text())["version"] == t3.MANIFEST_VERSION == "uzi-agent-t3-v6-codex"
    assert json.loads(path.read_text())["host_source"] == t3.host_source_binding()
    old = output.parent / "old-v1.json"
    old.write_text('{"version":"uzi-agent-t3-v1"}')
    before = old.read_bytes()
    monkeypatch.setattr(t3, "load_settings", lambda: pytest.fail("Old semantics must stop before credentials"))
    with pytest.raises(ValueError, match="Unknown manifest"):
        t3.run_manifest(old, side="reference", allow_paid=True)
    with pytest.raises(ValueError, match="manifest"):
        t3.worker(old, "reference")
    assert old.read_bytes() == before


def test_host_source_binding_covers_dirty_runtime_and_rejects_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(t3, "ROOT", tmp_path)
    module = tmp_path / "research_system/runtime/providers/example.py"
    module.parent.mkdir(parents=True)
    module.write_text("VALUE = 1\n")
    prompt = tmp_path / "domain_packs/finance_ashare/skill/reference.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("fixed native workflow")
    (tmp_path / ".env").write_text("SECRET=must-not-bind")
    source = t3.host_source_binding()
    assert set(source["files"]) == {str(module.relative_to(tmp_path)), str(prompt.relative_to(tmp_path))}
    assert "must-not-bind" not in json.dumps(source)
    t3.verify_host_source(source)
    module.write_text("VALUE = 2\n")
    with pytest.raises(ValueError, match="Host source changed"):
        t3.verify_host_source(source)


def test_missing_host_source_binding_is_not_a_verified_experiment():
    with pytest.raises(ValueError, match="Host source binding"):
        t3.verify_host_source(None)


def test_changed_driver_requires_new_manifest_before_credentials_or_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(t3, "ROOT", tmp_path)
    path = tmp_path / "workspace/reports/previous-experiment/manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": t3.MANIFEST_VERSION, "driver": {"sha256": "previous-driver"}}))
    before = path.read_bytes()
    monkeypatch.setattr(t3, "load_settings", lambda: pytest.fail("Stale driver must stop before credentials"))
    with pytest.raises(ValueError, match="Driver source changed"):
        t3.run_manifest(path, side="reference", allow_paid=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("probe", ["none", "business", "inside_model_io"])
def test_worker_prewarms_real_sdk_headers_and_still_blocks_subprocesses(tmp_path, probe):
    """Use the installed AsyncOpenAI.default_headers, with no API request.

    Run the real worker/guard in a child because Python audit hooks cannot be
    removed. Only native/source verification and model work are offline stubs.
    """
    code = '''
import asyncio,json,os,platform,subprocess,sys
from contextlib import nullcontext
from pathlib import Path
from openai import AsyncOpenAI
from openai._base_client import platform_headers
from scripts import run_uzi_agent_comparison as t3

t3.ROOT=Path(sys.argv[1]).resolve()
probe=sys.argv[2]
directory=t3.ROOT/'workspace/reports/offline-sdk-header-worker'
directory.mkdir(parents=True)
native={'path':str(t3.ROOT/'offline-native')}
host={'files':{},'source_set_sha256':'offline-source'}
config={'model':'offline-model'}
manifest={'version':t3.MANIFEST_VERSION,'driver':t3.file_binding(Path(t3.__file__)),
 'native_reference':native,'host_source':host,'inputs':{},'model_configuration':config,
 'deadline_seconds':30,'scope':'offline-sdk-header-regression'}
manifest_path=directory/'manifest.json'
t3.write_new(manifest_path,manifest)
os.environ['UZI_T3_CONFIRMED_MODEL_CONFIGURATION']=t3.encoded(config)
t3.verify_reference=lambda _path:native
t3.verify_host_source=lambda binding:None
t3.host_source_binding=lambda:host

async def offline_reference(_manifest,_output,isolation,*,deadline_at):
 warm_before_sdk=bool(platform._platform_cache.get((0,0)))
 async with AsyncOpenAI(api_key='offline-not-a-real-key',base_url='https://offline.invalid/v1',max_retries=0) as client:
  headers=client.default_headers
  header_status={'language':headers['X-Stainless-Lang'],'os':headers['X-Stainless-OS']}
 blocked=False
 if probe!='none':
  with isolation.model_io() if probe=='inside_model_io' else nullcontext():
   try:
    subprocess.run([sys.executable,'-c','raise SystemExit(99)'],check=True,capture_output=True)
   except RuntimeError:
    blocked=True
 return {'status':'completed','platform_warm_before_sdk':warm_before_sdk,
         'sdk_headers':header_status,'business_subprocess_blocked':blocked,'model_requests':0}

t3.reference_work=offline_reference
# Clear only test-process caches to exercise the first real SDK header access.
platform._platform_cache.clear()
platform._uname_cache=None
platform_headers.cache_clear()
exit_code=t3.worker(manifest_path,'reference')
print(json.dumps({'worker_exit':exit_code,'result':json.loads((directory/'reference/result.json').read_text())}))
'''
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), probe], cwd=t3.ROOT,
        env={key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout)
    value = report["result"]
    assert value["platform_warm_before_sdk"] is True
    assert value["model_requests"] == 0
    assert value["sdk_headers"]["language"] == "python"
    assert value["sdk_headers"]["os"] != "Unknown"
    assert value["host_source_unchanged"] is True and value["changed_input_files"] == []
    assert "offline-not-a-real-key" not in result.stdout
    if probe == "none":
        assert report["worker_exit"] == 0 and value["status"] == "completed"
        assert value["isolation_events"] == []
        assert value["business_subprocess_blocked"] is False
    else:
        assert report["worker_exit"] == 2 and value["status"] == "isolation_failed"
        assert value["business_subprocess_blocked"] is True
        assert value["isolation_events"] == [{"event": "subprocess.Popen", "status": "blocked"}]


def test_stage_repairs_share_original_deadline_and_stop_after_two_repairs(tmp_path):
    ticks = [0.0]
    attempts = []
    async def transport(request, timeout):
        attempts.append(timeout)
        ticks[0] += 2
        return {"output_text": '{"answer":false}', "http_status": 200, "provider_request_id": "simulated"}
    stage = t3.StageRunner(tmp_path, {**t3.public_config(settings(), "medium"), "request_timeout_seconds": 10},
                           transport, deadline_at=100, clock=lambda: ticks[0])
    result = asyncio.run(stage.json_stage("test", "JSON only", "Return JSON", lambda value: ["native suggestion"]))
    assert result["status"] == "output_invalid"
    assert len(attempts) == 3
    assert attempts == [10, 8, 6]
    assert len(list(tmp_path.glob("test-attempt-*.json"))) == 3


def test_stage_provider_failure_is_not_schema_repair(tmp_path):
    calls = []
    async def transport(request, timeout):
        calls.append(request)
        raise RuntimeError("simulated provider error")
    runner = t3.StageRunner(tmp_path, t3.public_config(settings(), "medium"), transport,
                            deadline_at=10, clock=lambda: 0)
    result = asyncio.run(runner.json_stage("failed", "JSON", "JSON", lambda value: []))
    assert result["status"] == "provider_failed"
    assert len(calls) == 1
    assert result["value"] is None


def test_auxiliary_recording_never_invents_success_for_missing_response():
    records = {"top_holdings": {"000001": {"status": "recorded", "data": [{"code": "x"}]}},
               "all_holding_funds": {}}
    module = SimpleNamespace(_fetch_top_holdings=lambda *a: pytest.fail("network"),
                             _fetch_all_holding_funds=lambda *a: pytest.fail("network"))
    calls = []
    with t3.reference_auxiliary(module, records, calls):
        assert module._fetch_top_holdings("000001") == [{"code": "x"}]
        assert module._fetch_all_holding_funds("688041") == []
    assert calls[-1]["status"] == "unrecorded"


def test_native_protocol_is_not_host_grounding_or_unique_url_gate():
    entry = {"evidence": [{"source": "same", "url": "https://a.invalid/doc", "finding": "first", "retrieved_at": "now"},
                           {"source": "same", "url": "https://a.invalid/doc", "finding": "second", "retrieved_at": "now"}],
             "associations": [{"link_to": "x", "chain_id": "x", "causal_chain": "x", "estimated_impact": "unknown"}],
             "conclusion": "Unknown without additional evidence"}
    assert t3.qualitative_issues({"3_macro": entry, "13_policy": entry}, ["3_macro", "13_policy"]) == []


def test_unknown_disclosure_is_usable_structure_but_not_factual_evidence():
    entry = {"evidence": [{"source": "unknown", "url": None, "retrieved_at": None,
                           "finding": "该字段无公开数据，冻结资料未包含套保名义本金。"}],
             "associations": [], "conclusion": "无法量化套保损益，保留数据缺口。"}
    assert t3.qualitative_issues({"9_futures": entry}, ["9_futures"]) == []
    completion = t3.qualitative_completion({"9_futures": entry})
    assert completion["passed"] is False
    assert completion["dimensions"]["9_futures"]["unique_factual_findings"] == 0
    assert completion["dimensions"]["9_futures"]["unknown_evidence_count"] == 1
    entry["evidence"][0]["finding"] = ""
    assert t3.qualitative_issues({"9_futures": entry}, ["9_futures"])


def test_research_completeness_counts_findings_not_unique_urls_or_duplicate_rows():
    first = {"source": "annual report", "url": "https://a.invalid/report.pdf",
             "retrieved_at": "2026-09-08", "finding": "营业收入增长 10%。"}
    entry = {"evidence": [first, {**first, "finding": "研发费用增长 20%。"}],
             "associations": [{"link_to": "8_materials"}], "conclusion": "真实不同事实。"}
    qualitative = {dim: deepcopy(entry) for _, dims in t3.RESEARCH for dim in dims}
    assert t3.qualitative_completion(qualitative)["passed"] is True
    qualitative["3_macro"]["evidence"][1] = {**first, "url": "https://b.invalid/same-finding"}
    assert t3.qualitative_issues({"3_macro": qualitative["3_macro"]}, ["3_macro"]) == []
    completion = t3.qualitative_completion(qualitative)
    assert completion["passed"] is False
    assert completion["dimensions"]["3_macro"]["unique_factual_findings"] == 1


@pytest.mark.parametrize("url", ["unknown", "unavailable", "file:///data/report", "https://", "https://[bad"])
def test_non_http_reference_cannot_satisfy_workflow_quality_but_is_not_native_critical(url):
    entry = {"evidence": [{"source": "report", "url": url, "finding": finding, "retrieved_at": "today"}
                           for finding in ("收入增长 10%", "成本增长 20%")],
             "associations": [], "conclusion": "Source not established"}
    assert t3.qualitative_issues({"3_macro": entry}, ["3_macro"]) == []
    completion = t3.qualitative_completion({"3_macro": entry})
    assert completion["passed"] is False
    assert completion["dimensions"]["3_macro"]["unique_factual_findings"] == 0
    assert completion["citation_authenticity_audited"] is False


def test_task3_optional_fields_preserve_native_panel_values_without_invention():
    native = {"investors": [{"investor_id": "a", "signal": "bearish", "score": 20,
                             "confidence": 63, "comment": "Native existing comment"}]}
    row = {"investor_id": "a", "signal": "neutral", "score": 50,
           "headline": "ROE 12%", "reasoning": "按方法论判断。"}
    assert t3.roleplay_issues({"investors": [row]}, native["investors"]) == []
    result = t3.apply_panel_rows(native, [row])
    assert result["investors"][0]["confidence"] == 63
    assert result["investors"][0]["comment"] == "Native existing comment"
    bare = t3.apply_panel_rows({"investors": [{"investor_id": "a"}]}, [row])
    assert "confidence" not in bare["investors"][0]
    assert bare["investors"][0]["comment"] == row["headline"]  # Pinned native display fallback.
    assert t3.roleplay_issues({"investors": [{"investor_id": "a"}]}, native["investors"])


@pytest.mark.parametrize("bad", [None, [], {"evidence": "not-list", "associations": [], "conclusion": "x"}])
def test_genuine_research_structure_errors_still_fail(bad):
    assert t3.qualitative_issues({"3_macro": bad}, ["3_macro"])


def test_stage_limit_is_work_conserving_and_never_exceeds_four(tmp_path):
    active = maximum = 0
    async def transport(request, timeout):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"output_text": '{}'}
    async def run():
        runner = t3.StageRunner(tmp_path, t3.public_config(settings(), "medium"), transport,
                               deadline_at=__import__("time").monotonic() + 2)
        return await asyncio.gather(*(runner.json_stage(f"group-{n}", "JSON", "JSON", lambda _: []) for n in range(5)))
    results = asyncio.run(run())
    assert maximum == 4
    assert all(row["status"] == "completed" for row in results)


def test_deadline_expired_before_dispatch_does_not_call_transport(tmp_path):
    async def transport(*args):
        pytest.fail("Must not start provider after deadline")
    runner = t3.StageRunner(tmp_path, t3.public_config(settings(), "medium"), transport,
                           deadline_at=0, clock=lambda: 1)
    result = asyncio.run(runner.json_stage("expired", "JSON", "JSON", lambda _: []))
    assert result["status"] == "deadline_exceeded"


def test_cancel_waits_for_transport_cleanup_before_releasing_slot(tmp_path):
    cleaned = False
    entered = asyncio.Event()
    async def transport(*args):
        nonlocal cleaned
        try:
            entered.set()
            await asyncio.sleep(10)
        finally:
            await asyncio.sleep(0)
            cleaned = True
    async def run():
        runner = t3.StageRunner(tmp_path, t3.public_config(settings(), "medium"), transport,
                               deadline_at=__import__("time").monotonic() + 2)
        task = asyncio.create_task(runner.json_stage("cancelled", "JSON", "JSON", lambda _: []))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned
        assert runner.slots._value == 4
    asyncio.run(run())


def test_error_diagnostics_never_capture_body_headers_settings_or_message():
    class Failure(Exception):
        status_code = 429
        request_id = "req_simulated"
        headers: ClassVar = {"Authorization": "secret"}
        body: ClassVar = {"error": {"code": "gateway_concurrency_limit", "type": "rate_limit", "message": "secret"}}
    value = t3.safe_error(Failure("secret"))
    assert value["http_status"] == 429
    assert value["provider_error_code"] == "gateway_concurrency_limit"
    assert "secret" not in str(value)


def test_transport_cause_types_are_bounded_without_messages_or_urls():
    outer = RuntimeError("secret endpoint token")
    outer.__cause__ = ConnectionError("secret")
    outer.__cause__.__cause__ = OSError("secret")
    outer.__cause__.__cause__.__cause__ = outer
    value = t3.safe_error(outer)
    assert value["error_chain_types"] == ["RuntimeError", "ConnectionError", "OSError"]
    assert "secret" not in str(value)


def test_host_progress_records_queued_timing_in_order_without_evidence_or_headers(tmp_path):
    path = tmp_path / "host-progress.jsonl"
    record = t3.CompactProgressRecorder(path)
    events = [{"stage": "dimensions_1_5", "request_phase": "queued", "raw": {"sensitive": "excluded"},
        "recording": ["excluded"], "request_timing": {"queue_seconds": n, "http_seconds": 0,
        "local_request_id": f"request-{n}", "headers": {"Authorization": "excluded"}}} for n in range(100)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(record, events))
    record.close()
    text = path.read_text()
    rows = [json.loads(line) for line in text.splitlines()]
    assert [row["sequence"] for row in rows] == list(range(1, 101))
    assert len({row["audit_owner_id"] for row in rows}) == 1
    assert all(row["request_phase"] == "queued" for row in rows)
    assert sorted(row["request_timing"]["queue_seconds"] for row in rows) == list(range(100))
    assert "excluded" not in text and "Authorization" not in text


def _saved_reference_fixture(tmp_path):
    audit = t3.ROOT / "workspace/reports/uzi-native-alignment-20260908-rYqLft"
    reference = audit / "native-pristine-20260912"
    recording = audit / "profile-dependency-parity-recorded-20260911/current-native-agent-recording-medium/input.json"
    if not reference.exists() or not recording.exists():
        pytest.skip("Independent pristine and embedded recorded input fixtures are not installed")
    case = json.loads(recording.read_text())["case"]
    raw, agent = tmp_path / "recorded-raw.json", tmp_path / "recorded-agent.json"
    raw.write_text(json.dumps(case["raw"], ensure_ascii=False))
    agent.write_text(json.dumps(case["agent_analysis"], ensure_ascii=False))
    return reference, raw, agent


def test_reference_native_dependency_probe_isolated_and_never_requests_model(tmp_path):
    """Optional local real-source smoke; no network, no Settings/key, no Agent success claim."""
    reference, raw, _agent = _saved_reference_fixture(tmp_path)
    source = t3.verify_reference(reference)
    aux = tmp_path / "aux.json"
    aux.write_text('{"top_holdings":{},"all_holding_funds":{}}')
    output = tmp_path / "output"
    output.mkdir()
    manifest = {"native_reference": source, "profile": "medium", "inputs": {
        "raw": t3.file_binding(raw), "auxiliary": t3.file_binding(aux)},
        "model_configuration": t3.public_config(settings(), "medium"), "deadline_seconds": 600}
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(manifest))
    code = '''
import asyncio,json,sys,time
from pathlib import Path
from scripts import run_uzi_agent_comparison as t3
output=Path(sys.argv[2])
sys.dont_write_bytecode=True
isolation=t3.Isolation(output)
sys.addaudithook(isolation.audit)
result=asyncio.run(t3.reference_work(json.loads(Path(sys.argv[1]).read_text()),output,isolation,deadline_at=time.monotonic()+600))
t3.write_new(output/'smoke-result.json',{'result':result,'isolation_events':isolation.events})
'''
    env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
    result = subprocess.run([sys.executable, "-c", code, str(spec), str(output)], cwd=t3.ROOT,
                            env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads((output / "smoke-result.json").read_text())
    assert report["result"]["status"] == "input_dependency_missing"
    assert report["result"]["model_requests"] == 0
    assert report["result"]["gate_scope"] == "controlled_input_dependency_check_not_native_CHECKS"
    assert report["isolation_events"] == []
    sources = json.loads((output / "native-source-audit.json").read_text())
    assert sources["host_business_modules_imported"] == []


@pytest.mark.parametrize("research_case", ["complete", "unknown", "duplicate_finding", "few_associations", "render_failure"])
def test_reference_full_stage_wiring_with_simulated_sdk_not_live_agent(tmp_path, research_case):
    reference, raw_path, recorded_agent = _saved_reference_fixture(tmp_path)
    raw = json.loads(raw_path.read_text())
    for dim in ("20_valuation_models", "21_research_workflow", "22_deep_methods"):
        raw["dimensions"].pop(dim, None)
    unmodeled_raw = tmp_path / "pre-derivation-raw.json"
    unmodeled_raw.write_text(json.dumps(raw, ensure_ascii=False))
    aux = {"top_holdings": {str(row["fund_code"]): {"status": "recorded_empty", "data": []}
            for row in raw.get("fund_managers", []) if isinstance(row, dict) and row.get("fund_code")},
           "all_holding_funds": {key: {"status": "recorded_empty", "data": []}
            for key in {str(raw["ticker"]), str(raw["ticker"]).split(".")[0]}}}
    auxiliary = tmp_path / "simulated-empty-aux.json"
    auxiliary.write_text(json.dumps(aux))
    output = tmp_path / "output"
    output.mkdir()
    manifest = {"native_reference": t3.verify_reference(reference), "profile": "medium", "inputs": {
        "raw": t3.file_binding(unmodeled_raw), "auxiliary": t3.file_binding(auxiliary)},
        "model_configuration": t3.public_config(settings(), "medium"), "deadline_seconds": 600}
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(manifest))
    code = '''
import asyncio,json,os,sys,time
from pathlib import Path
from types import SimpleNamespace
from scripts import run_uzi_agent_comparison as t3
from tests.codex_comparison_fakes import model_transport
output=Path(sys.argv[2]); recorded=json.loads(Path(sys.argv[3]).read_text()); research_case=sys.argv[4]
for row in recorded['qualitative_deep_dive'].values():
 if research_case=='unknown':
  row['evidence']=[{'source':'unknown','url':None,'retrieved_at':None,'finding':'该字段无公开数据，冻结输入无法确定事实。'}]
  row['associations']=[]
 elif research_case=='duplicate_finding': row['evidence']=[row['evidence'][0],dict(row['evidence'][0])]
 elif research_case=='few_associations': row['associations']=[]
calls=[]
class FakeResponse:
 def __init__(self,value): self.output_text=json.dumps(value,ensure_ascii=False)
 def model_dump(self,**kwargs): return {'output_text':self.output_text,'simulated':True}
class FakeClient:
 def __init__(self,**kwargs): self.responses=SimpleNamespace(with_raw_response=SimpleNamespace(create=self.create))
 async def __aenter__(self): return self
 async def __aexit__(self,*args): pass
 async def create(self,**kwargs):
  text=kwargs['input'];calls.append(kwargs['instructions'])
  if '顶层仅包含指定维度' in text:
   dims=json.loads(text.split('指定维度 ',1)[1].split('。',1)[0])
   value={dim:recorded['qualitative_deep_dive'][dim] for dim in dims}
  elif '输出 JSON {investors:' in text:
   context=json.loads(text.split('\\n')[-1]); value={'investors':[]}
   for row in context['rules']:
    value['investors'].append({'investor_id':row['investor_id'],'signal':row.get('signal','neutral'),
     'score':row.get('score',0),'headline':'simulated SDK wiring only','reasoning':'simulated SDK wiring only'})
  else:
   value=json.loads(json.dumps(recorded))
   if 'native_review_issues' in text:
    context=json.loads(text.split('\\n')[-1])
    value['review_acknowledged']={issue['dim']:'Recorded input limitation retained; no new source request was performed.'
       for issue in context['native_review_issues'] if issue['severity']=='warning'}
  response=FakeResponse(value)
  return SimpleNamespace(status_code=200,headers={'x-request-id':'req_simulated'},parse=lambda:response)
t3.codex_model_transport=model_transport(FakeClient)
os.environ['OPENAI_API_KEY']='simulated-not-a-key'
sys.dont_write_bytecode=True
if research_case=='render_failure':
 manifest=json.loads(Path(sys.argv[1]).read_text())
 sys.path.insert(0,str(Path(manifest['native_reference']['path'])/'skills/deep-analysis/scripts'))
 import assemble_report
 def fail_render(ticker): raise TypeError('SIMULATED_SECRET_MUST_NOT_BE_SERIALIZED')
 assemble_report.assemble=fail_render
os.chdir(output)
isolation=t3.Isolation(output);sys.addaudithook(isolation.audit)
result=asyncio.run(t3.reference_work(json.loads(Path(sys.argv[1]).read_text()),output,isolation,deadline_at=time.monotonic()+600))
computed=json.loads((output/'native-computation.json').read_text())
import lib.stock_features as sf
post=sf.extract_features(computed['raw'],computed['raw']['dimensions'])
t3.write_new(output/'simulated-sdk-result.json',{'result':result,'call_count':len(calls),'isolation_events':isolation.events,
 'post_derive_agent_features_equal':t3.encoded(post)==t3.encoded(computed['features'])})
'''
    result = subprocess.run([sys.executable, "-c", code, str(spec), str(output), str(recorded_agent), research_case],
                            cwd=t3.ROOT, capture_output=True, text=True, timeout=60, check=False,
                            env={key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"})
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads((output / "simulated-sdk-result.json").read_text())
    assert report["call_count"] == 9 + sum(row["attempts"] for row in report["result"]["native_recovery"]["stages"])
    assert report["post_derive_agent_features_equal"] is True
    assert report["result"]["native_review"] is not None
    assert report["result"]["native_review"]["critical_count"] == 0
    if research_case == "render_failure":
        assert report["result"]["status"] == "system_failed"
        assert report["result"]["failure_stage"] == "native_render"
        assert report["result"]["workflow_completion"]["passed"] is True
        assert report["result"]["rendering"]["status"] == "failed"
        assert report["result"]["rendering"]["failure"] == {
            "error_type": "TypeError", "error_chain_types": ["TypeError"]}
        assert report["result"]["html"] is None
        assert report["result"]["report_delivery_allowed"] is False
        assert report["isolation_events"] == []
        assert "SIMULATED_SECRET_MUST_NOT_BE_SERIALIZED" not in json.dumps(report)
        return
    html = Path(report["result"]["html"])
    assert html.is_file() and html.is_relative_to(output)
    assert html.name == "full-report-standalone.html"
    assert "<html" in html.read_text().lower()
    ticker = str(raw.get("full") or raw["ticker"])
    cache_path = output / ".cache" / ticker
    gaps = json.loads((output / "native-data-gaps.json").read_text())
    synthesis = json.loads((cache_path / "synthesis.json").read_text())
    if gaps.get("tasks"):
        assert synthesis["data_gaps"]["total_gaps"] == len(gaps["tasks"])
        assert [(task["dim"], task["field"]) for task in synthesis["data_gaps"]["tasks"]] == [
            (task["dim"], task["field"]) for task in gaps["tasks"]]
    assert report["result"]["render_reviews"]
    assert list(output.glob("native-review-render-*.json"))
    assert report["result"]["rendering"]["html_file"] == t3.file_binding(html)
    assert report["result"]["rendering"]["browser_visual_validation"] == "not_run"
    assert report["result"]["workflow_completion"]["persona_count"] == 66
    assert report["result"]["workflow_completion"]["passed"] is (research_case == "complete")
    assert report["result"]["report_delivery_allowed"] is False
    assert report["result"]["skill_delivery"]["passed"] is False
    assert report["result"]["status"] == ("completed" if research_case == "complete" else "agent_workflow_incomplete")
    assert report["result"]["workflow_completion"]["qualitative"]["passed"] is (research_case == "complete")
    assert len(list(output.glob("qualitative-*-attempt-*.json"))) == 3
    for attempt_path in output.glob("qualitative-*-attempt-*.json"):
        attempt = json.loads(attempt_path.read_text())
        assert attempt["validation_scope"] == "driver_structure_not_native_CHECKS"
    assert report["result"]["host_business_modules_imported"] == []
    assert report["isolation_events"] == []
