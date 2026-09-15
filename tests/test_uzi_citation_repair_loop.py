"""Do not mistake an Agent's missing citation for missing frozen data."""
from copy import deepcopy
from types import SimpleNamespace
import json
from pathlib import Path
from jsonschema import Draft202012Validator

from domain_packs.finance_ashare.subagents.uzi_392_analysis import _agent_repairable
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import apply_qualitative_deep_dive
from domain_packs.finance_ashare.subagents.uzi_recovery import native_recovery
from tests.test_uzi_qualitative_deep_dive import _Provider, _rows, _deep_base


def test_citation_gap_is_not_cached_and_only_bad_group_is_repaired(tmp_path):
    class Provider(_Provider):
        settings = SimpleNamespace(workspace_root=tmp_path, default_model="test")

        def parse(self, *, system_prompt, user_prompt, schema, temperature=0.2):
            group = super().parse(system_prompt=system_prompt, user_prompt=user_prompt,
                                  schema=schema, temperature=temperature)
            if self.disclose_missing and group.group_label.startswith("A"):
                for dim in group.dimensions:
                    dim.evidence[1].source = "unknown"
                    dim.evidence[1].url = None
                    dim.evidence[1].retrieved_at = None
            return group

    provider = Provider()
    provider.disclose_missing = True
    rows = _rows()
    original = deepcopy(rows)
    first = apply_qualitative_deep_dive(_deep_base(), snapshot_rows=rows, llm_provider=provider)
    execution = first["qualitative_agent_execution"]
    assert execution["status"] == "incomplete"
    assert {r["kind"] for r in execution["failure_records"]} == {"agent_content_invalid"}
    assert all(r["stage"] == "qualitative_A · Macro-Policy" for r in execution["failure_records"])
    issue = {"severity": "critical", "category": "agent-os-boundary", "dim": "qualitative_deep_dive",
             "evidence": str(execution), "failure_records": execution["failure_records"]}
    assert _agent_repairable([issue], provider)
    recovery = native_recovery({"results": [{"qualitative_execution": execution,
                                              "self_review": {"issues": [issue]}}]})
    assert recovery["kind"] == "output_invalid"
    assert recovery["dimensions"] == []
    assert recovery["output_dimensions"] == ["13_policy", "3_macro"]
    schema = json.loads((Path(__file__).resolve().parents[1] /
        "domain_packs/finance_ashare/artifact_schemas/uzi_native_analysis.json").read_text())
    Draft202012Validator(schema["properties"]["recovery"]).validate(recovery)
    provider.disclose_missing = False
    second = apply_qualitative_deep_dive(_deep_base(), snapshot_rows=rows, llm_provider=provider,
                                        previous_failures=execution["failure_records"],
                                        repair_feedback=execution["errors"])
    assert second["qualitative_agent_execution"]["status"] == "completed"
    assert len(provider.system_prompts) == 4  # A retried, B/C reused.
    assert rows == original


def test_real_absence_is_source_gap_and_not_repeated(tmp_path):
    provider = _Provider(disclose_missing=True)
    provider.settings = SimpleNamespace(workspace_root=tmp_path, default_model="test")
    rows = _rows()
    for row in rows:
        row["facts"]["sources"] = []
    first = apply_qualitative_deep_dive(_deep_base(), snapshot_rows=rows, llm_provider=provider)
    execution = first["qualitative_agent_execution"]
    assert {r["kind"] for r in execution["failure_records"]} == {"source_gap"}
    issue = {"severity": "critical", "category": "agent-os-boundary", "dim": "qualitative_deep_dive",
             "evidence": str(execution), "failure_records": execution["failure_records"]}
    assert not _agent_repairable([issue], provider)
    recovery = native_recovery({"results": [{"qualitative_execution": execution,
                                              "self_review": {"issues": [issue]}}]})
    assert recovery["kind"] == "source_gap"
    assert len(recovery["dimensions"]) == 6
    second = apply_qualitative_deep_dive(_deep_base(), snapshot_rows=rows, llm_provider=provider,
                                        previous_failures=execution["failure_records"])
    assert second["qualitative_agent_execution"]["status"] == "incomplete"
    assert len(provider.system_prompts) == 3
