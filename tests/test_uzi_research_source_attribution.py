"""Offline prompt wiring, not a test of real-model citation correctness."""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import monotonic
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.subagents import (
    uzi_qualitative_deep_dive as qualitative,
)
from research_system.market_data import research_enrichment as enrichment
from research_system.market_data.contracts import canonical_hash
from research_system.strategies import runtime_pins
from tests.test_uzi_qualitative_company_document_context import (
    FINDINGS,
    SHARED_URL,
    attach_shared_document,
    fixed_rows,
)

POLICY_MARKER = "RESEARCH_SOURCE_ATTRIBUTION_V1"
POLICY_REQUIREMENTS = (
    "不能借用相邻文档的 URL",
    "不代表其中任一文档支持任一 finding",
    "可以转述，不要求逐字引用",
    'source="unknown"',
    "提供者未知但文档确有支持",
    "同一 URL 可以支持不同事实",
)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("attribution regressions must not call sources or real models")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


class CapturingProvider:
    available = True

    def __init__(self, *, workspace_root=None, disclose_gap=False):
        self.settings = SimpleNamespace(
            workspace_root=workspace_root, uzi_llm_transient_retries=0,
        )
        self.calls = []
        self.lock = Lock()
        self.disclose_gap = disclose_gap

    def parse(self, *, system_prompt, user_prompt, schema, **_kwargs):
        group_line = next(line for line in user_prompt.splitlines() if line.startswith("负责分组："))
        label = group_line.split("：", 1)[1]
        with self.lock:
            self.calls.append({"label": label, "system_prompt": system_prompt, "user_prompt": user_prompt})
        dimensions = dict(qualitative._GROUPS)[label]
        research = {"agent_reviewed": True, "group_label": label, "dimensions": []}
        for dimension in dimensions:
            evidence = [{
                # Unknown data producer, but an actual frozen document supports
                # each distinct fact. The schema must not reject that source.
                "source": "unknown", "url": SHARED_URL, "finding": finding,
                "retrieved_at": "2026-09-08",
            } for finding in FINDINGS[dimension]]
            if self.disclose_gap and dimension == dimensions[1]:
                evidence[1] = {
                    "source": "unknown", "url": None, "retrieved_at": None,
                    "finding": "固定资料未提供这一项事实，不能借半年度报告补造来源。",
                }
            research["dimensions"].append({
                "dimension_id": dimension, "evidence": evidence,
                "associations": [{
                    "link_to": dimensions[1] if dimension == dimensions[0] else dimensions[0],
                    "chain_id": f"{dimension}-fixture",
                    "causal_chain": "需求与成本变化可能传导至毛利，幅度取决于议价能力。",
                    "estimated_impact": "条件分析，不能量化。",
                }],
                "conclusion": "仅转述冻结事实，未提供的事实保留缺口。",
            })
        payload = research if schema is qualitative.QualitativeGroupResult else {
            "research": research, "proposals": [], "unresolved": [],
        }
        return schema.model_validate(payload)


def report_rows():
    rows = fixed_rows()
    archive = attach_shared_document(rows)
    # A complete in-memory frozen-text fixture, beyond the old 2k prefix.
    archive["content"] += "\n公开归档的其余说明。" * 700 + "\n归档末尾标记"
    archive["content_hash"] = sha256(archive["content"].encode()).hexdigest()
    future = next(row for row in rows if row.get("dimension") == "9_futures")
    future["facts"]["_uzi_raw_result"].update({
        "data": {"linked_contract": "无直接关联品种", "note": "综合 行业与期货市场无强相关品种"},
        "source": "legacy:fetch_futures", "error": "fixture-collection-gap", "_timeout": True,
    })
    return rows, archive


def run_report(provider, rows, *, repair=False):
    return qualitative.apply_qualitative_deep_dive(
        {"symbol": "688041.SH", "results": [], "risk_flags": []},
        snapshot_rows=rows, llm_provider=provider, research_profile="medium",
        repair_feedback=[f"{label}:qualitative_ungrounded_url" for label, _ in qualitative._GROUPS] if repair else None,
    )


def assert_shared_policy(prompt):
    assert POLICY_MARKER in prompt, "the actual request is missing shared source-attribution instructions"
    policy = qualitative.QUALITATIVE_SOURCE_ATTRIBUTION_RULES
    assert prompt.count(policy) == 1
    assert all(phrase in policy for phrase in POLICY_REQUIREMENTS)
    assert "unknown披露不算有效事实引用" not in prompt


def assert_fixed_document_view(view, expected):
    """Independently resolve transport links, then compare every input field."""
    restored = deepcopy(view)
    for row in restored.values():
        for document in row["documents"]:
            if "text_reference" not in document:
                continue
            reference = document.pop("text_reference")
            assert reference["dimension"] in view
            candidates = view[reference["dimension"]]["documents"]
            index = reference["document_index"]
            assert type(index) is int and 0 <= index < len(candidates)
            original = candidates[index]
            assert "inline_content" in original  # No unresolved/cyclic link.
            assert original["evidence_id"] == reference["evidence_id"]
            assert original["document_hash"] == document["document_hash"]
            text = original["inline_content"]
            assert sha256(text.encode()).hexdigest() == reference["text_sha256"]
            document["inline_content"] = text
    assert restored == expected
    assert canonical_hash(restored) == canonical_hash(expected)


@pytest.mark.parametrize("damage", ["url", "evidence_id", "hash", "index", "body"])
def test_attribution_transport_check_rejects_changed_identity_or_text(damage):
    text = "冻结正文及末尾事实"
    document = {"evidence_id": "proof", "document_hash": "a" * 64,
                "url": SHARED_URL, "inline_content": text}
    expected = {key: {"documents": [deepcopy(document)]} for key in ("first", "second")}
    view = deepcopy(expected)
    linked = view["second"]["documents"][0]
    linked.pop("inline_content")
    linked["text_reference"] = {"dimension": "first", "document_index": 0,
                               "evidence_id": "proof", "text_sha256": sha256(text.encode()).hexdigest()}
    assert_fixed_document_view(view, expected)
    if damage in {"url", "evidence_id"}:
        linked[damage] = "unbound"
    elif damage == "hash":
        linked["text_reference"]["text_sha256"] = "f" * 64
    elif damage == "index":
        linked["text_reference"]["document_index"] = 1
    else:
        view["first"]["documents"][0]["inline_content"] = text[:-1]
    with pytest.raises(AssertionError):
        assert_fixed_document_view(view, expected)


@pytest.mark.parametrize("repair", [False, True], ids=["initial", "repair"])
def test_all_report_groups_receive_shared_policy_without_changing_frozen_inputs(repair):
    rows, archive = report_rows()
    before, digest = deepcopy(rows), canonical_hash(rows)
    provider = CapturingProvider()
    result = run_report(provider, rows, repair=repair)

    assert result["qualitative_agent_execution"]["status"] == "completed", result
    assert {call["label"] for call in provider.calls} == {label for label, _ in qualitative._GROUPS}
    for call in provider.calls:
        assert_shared_policy(call["system_prompt"])
        assert ("UZI_REPAIR_FEEDBACK=" in call["user_prompt"]) is repair
        documents = json.loads(call["system_prompt"].split("FROZEN_STOCK_DOCUMENTS=", 1)[1].split("\nFROZEN_EVIDENCE_BUNDLE=", 1)[0])
        visible = next(document for document in documents if document["url"] == SHARED_URL)
        assert visible["finding"] == archive["content"]
        assert visible["content_hash"] == archive["content_hash"]
        assert "legacy:fetch_futures" in call["system_prompt"]
        assert "fixture-collection-gap" in call["system_prompt"]
        assert '"_timeout":true' in call["system_prompt"]
    assert rows == before and canonical_hash(rows) == digest
    # Two distinct findings from the same URL, with an unknown producer, remain valid.
    assert result["qualitative_agent_execution"]["evidence_count"] == 12
    assert result["qualitative_agent_execution"]["unknown_evidence_count"] == 12


@pytest.mark.parametrize("label,dimensions", qualitative._GROUPS)
@pytest.mark.parametrize("disclose_gap", [False, True], ids=["unknown-producer", "missing-fact"])
def test_dc_uses_same_policy_and_keeps_raw_source_full_text_and_unknown_modes(label, dimensions, disclose_gap):
    raw = {"linked_contract": "无直接关联品种", "note": "综合 行业与期货市场无强相关品种"}
    body = "\n".join(finding for facts in FINDINGS.values() for finding in facts) + "\n冻结原文" * 700
    inputs = {dimension: {
        "raw": deepcopy(raw), "allowed_urls": [SHARED_URL],
        "documents": [{"evidence_id": "fixture-proof", "url": SHARED_URL,
                       "document_hash": "a" * 64, "inline_content": body, "retrieved_at": "2026-09-08"}],
    } for dimension in dimensions}
    inputs["_read_only_context"] = {"dimensions": {"9_futures": {
        "data": deepcopy(raw), "source": "legacy:fetch_futures",
        "error": "fixture-collection-gap", "_timeout": True,
    }}}
    before, digest = deepcopy(inputs), canonical_hash(inputs)
    provider = CapturingProvider(disclose_gap=disclose_gap)
    result = enrichment.request_fact_proposals(
        provider, group_label=label, dimensions=dimensions, inputs=inputs,
        deadline_at=monotonic() + 30,
    )

    assert result["status"] == "completed", result
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert_shared_policy(call["system_prompt"])
    # Exact quotes remain the write-proposal contract; the server binds hashes/offsets.
    assert "citations只含已绑定evidence_id和inline_content中唯一精确出现的quote" in call["system_prompt"]
    assert "不要生成旧值、old_exists、任何hash、start/end或period_start/end" in call["system_prompt"]
    assert "程序从本次固定输入确定它们" in call["system_prompt"]
    assert "retrieved_at原样使用documents.retrieved_at" in call["system_prompt"]
    encoded = call["user_prompt"].split("\n本组证据=", 1)[1].split("\nREAD_ONLY_RESEARCH_CONTEXT=", 1)
    # The wire view shares byte-identical bodies; reconstruct instead of
    # requiring duplicate full text. Neither binding nor body may be lost.
    view = json.loads(encoded[0])
    assert_fixed_document_view(view, {key: inputs[key] for key in dimensions})
    assert sum(document.get("inline_content") == body
               for row in view.values() for document in row["documents"]) == 1
    context = json.loads(encoded[1])
    assert context["dimensions"] == inputs["_read_only_context"]["dimensions"]
    assert inputs == before and canonical_hash(inputs) == digest
    assert result["proposals"] == []
    last = result["research"]["dimensions"][1]["evidence"][1]
    assert last["source"] == "unknown"
    assert last["url"] == (None if disclose_gap else SHARED_URL)


def test_url_less_disclosure_still_fails_only_existing_final_report_citation_check():
    rows, _ = report_rows()
    provider = CapturingProvider(disclose_gap=True)
    result = run_report(provider, rows)

    assert len(provider.calls) == 3  # No added input gate.
    assert result["qualitative_agent_execution"]["status"] == "incomplete"
    errors = result["qualitative_agent_execution"]["errors"]
    for _label, dimensions in qualitative._GROUPS:
        assert f"{dimensions[1]}:grounded_citations_below_two_after_analysis:1" in errors


def test_shared_policy_change_rekeys_real_report_stage_cache_without_deleting_old_entries(tmp_path, monkeypatch):
    rows, _ = report_rows()
    first = CapturingProvider(workspace_root=tmp_path)
    assert run_report(first, rows)["qualitative_agent_execution"]["status"] == "completed"
    old_files = {path: path.read_bytes() for path in (tmp_path / "cache/uzi_qualitative_stages").glob("*.json")}
    assert len(old_files) == 3
    cached = CapturingProvider(workspace_root=tmp_path)
    run_report(cached, rows)
    assert cached.calls == []

    policy = qualitative.QUALITATIVE_SOURCE_ATTRIBUTION_RULES
    monkeypatch.setattr(qualitative, "QUALITATIVE_SOURCE_ATTRIBUTION_RULES", policy + "\n离线提示词版本测试")
    changed = CapturingProvider(workspace_root=tmp_path)
    run_report(changed, rows)
    assert len(changed.calls) == 3
    assert all("离线提示词版本测试" in call["system_prompt"] for call in changed.calls)
    assert len(list((tmp_path / "cache/uzi_qualitative_stages").glob("*.json"))) == 6
    assert all(path.read_bytes() == body for path, body in old_files.items())


def test_deployed_prompt_pin_includes_shared_policy_source(monkeypatch):
    source_path = Path(qualitative.__file__).resolve()
    policy = qualitative.QUALITATIVE_SOURCE_ATTRIBUTION_RULES
    assert POLICY_MARKER in policy
    read_bytes = Path.read_bytes
    original = runtime_pins.deployed_prompt_hash.__wrapped__()
    observed = []

    def changed_bytes(path):
        body = read_bytes(path)
        if path.resolve() == source_path:
            observed.append(path)
            return body + b"\n# offline attribution pin change\n"
        return body

    monkeypatch.setattr(Path, "read_bytes", changed_bytes)
    changed = runtime_pins.deployed_prompt_hash.__wrapped__()
    assert observed == [source_path]
    assert original != changed
