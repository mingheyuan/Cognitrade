from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
import unittest

import jsonschema

from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    QualitativeAssociation,
    QualitativeDimension,
    QualitativeEvidence,
    QualitativeGroupResult,
    apply_qualitative_deep_dive,
)


_GROUP_DIMENSIONS = {
    "A · Macro-Policy": ("3_macro", "13_policy"),
    "B · Industry-Events": ("7_industry", "15_events"),
    "C · Cost-Transmission": ("8_materials", "9_futures"),
}


def _deep_base() -> dict:
    return {
        "methodology_version": "3.9.2-adapted",
        "profile": "deep",
        "results": [],
        "summary": "test",
        "confidence": 0.5,
        "risk_flags": [],
    }


def _rows() -> list[dict]:
    mapping = {
        "macro": "3_macro",
        "policy": "13_policy",
        "industry": "7_industry",
        "events": "15_events",
        "materials": "8_materials",
        "futures": "9_futures",
    }
    return [
        {
            "snapshot_type": snapshot_type,
            "symbol": "600519",
            "snapshot_date": "2026-08-07",
            "quality_status": "available",
            "facts": {
                "finding": f"{dimension} 已观测事实",
                "sources": [
                    {"url": f"https://evidence.example/{dimension}/1"},
                    {"url": f"https://evidence.example/{dimension}/2"},
                ],
            },
            "source_refs": [],
        }
        for snapshot_type, dimension in mapping.items()
    ]


class _Provider:
    available = True

    def __init__(self, *, hallucinate=False) -> None:
        self.hallucinate = hallucinate
        self.system_prompts: list[str] = []
        self._lock = Lock()

    def parse(self, *, system_prompt, user_prompt, schema, temperature=0.2):
        if schema is not QualitativeGroupResult:
            raise AssertionError(schema)
        label = user_prompt.splitlines()[0].split("：", 1)[1]
        bundle = json.loads(user_prompt.split("本组证据=", 1)[1])
        dimensions = []
        for index, dimension in enumerate(_GROUP_DIMENSIONS[label]):
            urls = list(bundle[dimension]["allowed_urls"])
            if self.hallucinate and label.startswith("B") and index == 0:
                urls[0] = "https://fabricated.example/not-in-artifact"
            dimensions.append(
                QualitativeDimension(
                    dimension_id=dimension,
                    evidence=[
                        QualitativeEvidence(
                            source=f"冻结来源 {evidence_index + 1}",
                            url=url,
                            finding=f"{dimension} 的真实来源提供了可复核事实 {evidence_index + 1}",
                            retrieved_at="2026-08-07",
                        )
                        for evidence_index, url in enumerate(urls[:2])
                    ],
                    associations=[
                        QualitativeAssociation(
                            link_to=_GROUP_DIMENSIONS[label][1 - index],
                            chain_id=f"{dimension}-chain",
                            causal_chain=f"{dimension} 的已观测变化通过成本、需求或政策传导到相邻研究维度",
                            estimated_impact="方向需结合后续披露复核",
                        )
                    ],
                    conclusion=f"{dimension} 已由两条冻结来源交叉验证；结论只覆盖当前证据期，缺失内容不做推断。",
                )
            )
        with self._lock:
            self.system_prompts.append(system_prompt)
        return QualitativeGroupResult(agent_reviewed=True, group_label=label, dimensions=dimensions)


class UziQualitativeDeepDiveTests(unittest.TestCase):
    def test_provider_unavailable_is_a_blocking_deep_gap(self) -> None:
        result = apply_qualitative_deep_dive(
            _deep_base(),
            snapshot_rows=_rows(),
            llm_provider=None,
        )
        self.assertEqual(result["quality_status"], "data_gap")
        self.assertFalse(result["agent_reviewed"])
        self.assertEqual(result["qualitative_agent_execution"]["status"], "required_but_unavailable")
        self.assertIn("qualitative_deep_dive_required", result["data_gap"])

    def test_missing_cited_sources_stops_before_model_call(self) -> None:
        provider = _Provider()
        rows = _rows()
        rows[0]["facts"]["sources"] = rows[0]["facts"]["sources"][:1]
        result = apply_qualitative_deep_dive(
            _deep_base(),
            snapshot_rows=rows,
            llm_provider=provider,
        )
        self.assertEqual(result["qualitative_agent_execution"]["status"], "evidence_preflight_failed")
        self.assertEqual(provider.system_prompts, [])

    def test_three_parallel_groups_cover_six_dimensions_with_grounded_urls(self) -> None:
        provider = _Provider()
        result = apply_qualitative_deep_dive(
            _deep_base(),
            snapshot_rows=_rows(),
            llm_provider=provider,
        )
        self.assertEqual(result["quality_status"], "available")
        self.assertTrue(result["agent_reviewed"])
        self.assertEqual(set(result["qualitative_deep_dive"]), set(dimension for dims in _GROUP_DIMENSIONS.values() for dimension in dims))
        self.assertEqual(result["qualitative_agent_execution"]["group_count"], 3)
        self.assertGreaterEqual(result["qualitative_agent_execution"]["association_count"], 3)
        self.assertEqual(len(provider.system_prompts), 3)
        self.assertEqual(len(set(provider.system_prompts)), 1)

        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "domain_packs/finance_ashare/artifact_schemas/deep_analysis.json")
            .read_text(encoding="utf-8")
        )
        jsonschema.validate(result, schema)

    def test_ungrounded_model_url_invalidates_completion(self) -> None:
        result = apply_qualitative_deep_dive(
            _deep_base(),
            snapshot_rows=_rows(),
            llm_provider=_Provider(hallucinate=True),
        )
        self.assertEqual(result["quality_status"], "data_gap")
        self.assertFalse(result["agent_reviewed"])
        self.assertEqual(result["qualitative_agent_execution"]["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
