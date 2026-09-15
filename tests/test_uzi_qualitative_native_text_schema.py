"""Native Task 2.5 has content requirements, not minimum character counts.

All facts below are synthetic offline fixtures. Structural acceptance does not
establish source support, semantic quality, or completion of an actual report.
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as qualitative
from domain_packs.finance_ashare.subagents.uzi_llm_support import model_failure_record


OLD_CACHE_VERSION = "uzi-3.9.4-qualitative-stage-v5-company-stock-documents"
URL = "https://archive.example/offline-fixture.pdf"
SHORT_TEXT = {
    "finding": "套保余额为零",
    "causal_chain": "原料涨价→毛利下降",
    "estimated_impact": "-1%",
    "conclusion": "成本上升压缩毛利",
}


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("native text schema regressions must not access the network")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def evidence(**changes):
    return {
        "source": "offline synthetic archive",
        "url": URL,
        "finding": "测试归档明确披露了该项事实，真实内容支持须独立审计。",
        "retrieved_at": "2026-09-08T09:00:00Z",
        **changes,
    }


def association(**changes):
    return {
        "link_to": "8_materials",
        "chain_id": "fixture-cost-chain",
        "causal_chain": "原料价格上升可能通过采购成本增加传导至毛利率下降，幅度取决于价格传递。",
        "estimated_impact": "条件成立时毛利率下降",
        **changes,
    }


def dimension(dimension_id="8_materials", *, short=False):
    result = {
        "dimension_id": dimension_id,
        "evidence": [evidence(), evidence(finding="第二项独立测试事实：归档披露采购单位成本上升。")],
        "associations": [association()],
        "conclusion": "本项仅覆盖已有测试归档中的事实；缺失的事实与条件仍需独立核实。",
    }
    if short:
        result["evidence"][0]["finding"] = SHORT_TEXT["finding"]
        result["evidence"][1]["finding"] = "采购成本上升"
        result["associations"][0].update({key: SHORT_TEXT[key] for key in ("causal_chain", "estimated_impact")})
        result["conclusion"] = SHORT_TEXT["conclusion"]
    return result


def group(*, short=False):
    return {
        "agent_reviewed": True,
        "group_label": "C · Cost-Transmission",
        "dimensions": [dimension(key, short=short) for key in ("8_materials", "9_futures")],
    }


@pytest.mark.parametrize("field", SHORT_TEXT)
def test_short_concrete_text_is_structurally_valid_without_padding(field):
    if field == "finding":
        parsed = qualitative.QualitativeEvidence.model_validate(evidence(finding=SHORT_TEXT[field]))
    elif field == "conclusion":
        parsed = qualitative.QualitativeDimension.model_validate({**dimension(), field: SHORT_TEXT[field]})
    else:
        parsed = qualitative.QualitativeAssociation.model_validate(association(**{field: SHORT_TEXT[field]}))
    assert getattr(parsed, field) == SHORT_TEXT[field]


@pytest.mark.parametrize("field", SHORT_TEXT)
@pytest.mark.parametrize("blank", ["", " \n\t " * 8], ids=["empty", "whitespace"])
def test_text_fields_still_reject_empty_or_whitespace(field, blank):
    if field == "finding":
        model, payload = qualitative.QualitativeEvidence, evidence(finding=blank)
    elif field == "conclusion":
        model, payload = qualitative.QualitativeDimension, {**dimension(), field: blank}
    else:
        model, payload = qualitative.QualitativeAssociation, association(**{field: blank})
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("change", [{"url": None}, {"finding": " \n\t " * 8}])
def test_incomplete_factual_evidence_retains_safe_diagnostic(change):
    with pytest.raises(ValidationError) as caught:
        qualitative.QualitativeEvidence.model_validate(evidence(**change))
    record = model_failure_record(caught.value, stage="qualitative_C")
    assert record["validation_reasons"] == [{"field": "", "code": "qualitative_factual_evidence_incomplete", "missing_fields": list(change)}]
    assert "finding" not in record


def test_http_url_and_unknown_disclosure_rules_are_unchanged():
    with pytest.raises(ValidationError) as caught:
        qualitative.QualitativeEvidence.model_validate(evidence(url="ftp://archive.example/file"))
    assert model_failure_record(caught.value, stage="qualitative_C")["validation_reasons"] == [
        {"field": "url", "code": "qualitative_evidence_url_invalid"}
    ]
    parsed = qualitative.QualitativeEvidence.model_validate({
        "source": "unknown", "finding": "所给档案未列套保本金", "url": None, "retrieved_at": None,
    })
    assert parsed.url is None and parsed.retrieved_at is None
    with pytest.raises(ValidationError):
        qualitative.QualitativeEvidence.model_validate({"source": "unknown", "finding": " \n\t "})


def test_dimension_still_requires_two_records():
    payload = dimension()
    payload["evidence"] = payload["evidence"][:1]
    with pytest.raises(ValidationError):
        qualitative.QualitativeDimension.model_validate(payload)


class ShortTextProvider:
    available = True

    def __init__(self, *, disclose=False, foreign_url=False):
        self.settings = SimpleNamespace(uzi_llm_transient_retries=0)
        self.disclose = disclose
        self.foreign_url = foreign_url
        self.calls = []

    def parse(self, *, system_prompt, user_prompt, schema, **_kwargs):
        label = user_prompt.splitlines()[0].split("：", 1)[1]
        self.calls.append((system_prompt, user_prompt))
        payload = {"agent_reviewed": True, "group_label": label, "dimensions": []}
        dimensions = dict(qualitative._GROUPS)[label]
        for key in dimensions:
            row = dimension(key, short=True)
            if self.disclose:
                row["evidence"][1] = {"source": "unknown", "finding": "所给档案未列该字段"}
            if self.foreign_url:
                row["evidence"][0]["url"] = "https://foreign.example/not-frozen.pdf"
            payload["dimensions"].append(row)
        return schema.model_validate(payload)


def run_report(provider):
    rows = [{
        "snapshot_type": kinds[0], "symbol": "688041.SH", "snapshot_date": "2026-09-08",
        "facts": {"sources": [{"url": URL, "finding": "套保余额为零。采购成本上升。仅用于离线测试。"}]},
    } for kinds in qualitative._DIMENSION_TYPES.values()]
    original_rows = deepcopy(rows)
    result = qualitative.apply_qualitative_deep_dive(
        {"symbol": "688041.SH", "as_of_date": "2026-09-08", "results": [], "risk_flags": []},
        snapshot_rows=rows, llm_provider=provider,
    )
    assert rows == original_rows
    return result


def test_actual_report_path_accepts_short_text_from_offline_provider():
    provider = ShortTextProvider()
    result = run_report(provider)
    assert len(provider.calls) == 3
    assert result["qualitative_agent_execution"]["status"] == "completed"
    assert result["qualitative_deep_dive"]["8_materials"]["conclusion"] == SHORT_TEXT["conclusion"]


def test_unknown_disclosure_does_not_count_as_second_factual_reference():
    result = run_report(ShortTextProvider(disclose=True))
    assert result["qualitative_agent_execution"]["status"] != "completed"
    assert result["qualitative_agent_execution"]["completed_group_count"] == 3


def test_short_text_does_not_allow_foreign_document_urls():
    result = run_report(ShortTextProvider(foreign_url=True))
    execution = result["qualitative_agent_execution"]
    assert execution["status"] != "completed"
    assert any(reason["code"] == "qualitative_ungrounded_url"
               for failure in execution["failure_records"]
               for reason in failure.get("validation_reasons", []))


def test_schema_change_invalidates_v5_cache_and_preserves_old_bytes(tmp_path, monkeypatch):
    provider = SimpleNamespace(settings=SimpleNamespace(workspace_root=tmp_path))
    arguments = {"group_label": "C · Cost-Transmission", "system_prompt": "same system", "user_prompt": "same user"}
    with monkeypatch.context() as old:
        old.setattr(qualitative, "_QUALITATIVE_STAGE_CACHE_SCHEMA_VERSION", OLD_CACHE_VERSION)
        old_path = qualitative._qualitative_stage_cache_path(provider, **arguments)
        qualitative._write_qualitative_stage_cache(old_path, qualitative.QualitativeGroupResult.model_validate(group()))
    old_bytes = old_path.read_bytes()
    new_path = qualitative._qualitative_stage_cache_path(provider, **arguments)
    assert new_path != old_path
    assert qualitative._load_qualitative_stage_cache(old_path) is None
    assert old_path.read_bytes() == old_bytes
    current = qualitative.QualitativeGroupResult.model_validate(group(short=True))
    qualitative._write_qualitative_stage_cache(new_path, current)
    assert qualitative._load_qualitative_stage_cache(new_path) == current


def test_json_schema_declares_nonempty_text_without_extra_minimum_lengths():
    for model, fields in [
        (qualitative.QualitativeEvidence, ["finding"]),
        (qualitative.QualitativeAssociation, ["causal_chain", "estimated_impact"]),
        (qualitative.QualitativeDimension, ["conclusion"]),
    ]:
        properties = model.model_json_schema()["properties"]
        for field in fields:
            assert properties[field]["minLength"] == 1
