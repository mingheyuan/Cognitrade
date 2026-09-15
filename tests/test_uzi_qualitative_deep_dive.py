from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from types import SimpleNamespace

from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    QualitativeAssociation,
    QualitativeDimension,
    QualitativeEvidence,
    QualitativeGroupResult,
    _dimension_context,
    apply_qualitative_deep_dive,
)

_GROUP_DIMENSIONS = {
    "A · Macro-Policy": ("3_macro", "13_policy"),
    "B · Industry-Events": ("7_industry", "15_events"),
    "C · Cost-Transmission": ("8_materials", "9_futures"),
}


def test_historical_qualitative_evidence_requires_frozen_archive_content() -> None:
    rows = _rows()
    rows[0]["query_manifest"] = {"pit_mode": "historical_reconstruction"}
    assert _dimension_context(rows, "3_macro")["allowed_urls"] == []
    rows[0]["source_refs"] = [
        {
            "type": "market_data_evidence", "source_kind": "research_document",
            "url": f"https://archive.example/macro/{index}",
            "content": f"冻结归档正文证据 {index}", "content_hash": f"hash-{index}",
            "content_ref": f"archive/{index}", "evidence_mode": "public_archive_reconstruction",
        }
        for index in (1, 2)
    ]
    context = _dimension_context(rows, "3_macro")
    assert context["allowed_urls"] == ["https://archive.example/macro/1", "https://archive.example/macro/2"]
    assert all(record["finding"].startswith("冻结归档正文证据") for record in context["evidence_records"])
    # Unchanged manifests do not lose same-day evidence merely as time passes.
    assert len(_dimension_context(rows, "13_policy")["allowed_urls"]) == 2


def test_current_qualitative_rejects_bare_links_but_accepts_native_pdf_evidence() -> None:
    rows = [{"snapshot_type": "macro", "facts": {"sources": [
        {"url": "https://example.test/bare"},
        {"pdf": "https://example.test/frozen.pdf", "finding": "已经采集并冻结的宏观研究事实"},
    ]}}]
    assert _dimension_context(rows, "3_macro")["allowed_urls"] == ["https://example.test/frozen.pdf"]


def test_same_frozen_document_preserves_two_distinct_evidence_records() -> None:
    rows = [{"snapshot_type": "macro", "facts": {"sources": [
        {"url": "https://example.test/frozen", "finding": "冻结文档中的利率事实"},
        {"url": "https://example.test/frozen", "finding": "冻结文档中的汇率事实"},
    ]}}]
    context = _dimension_context(rows, "3_macro")
    assert context["allowed_urls"] == ["https://example.test/frozen"]
    assert len(context["evidence_records"]) == 2


def _deep_base() -> dict:
    return {
        "methodology_version": "3.9.4-adapted",
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
                    {"url": f"https://evidence.example/{dimension}/1", "title": f"{dimension} 已采集证据一"},
                    {"url": f"https://evidence.example/{dimension}/2", "title": f"{dimension} 已采集证据二"},
                ],
            },
            "source_refs": [],
        }
        for snapshot_type, dimension in mapping.items()
    ]


class _Provider:
    available = True

    def __init__(self, *, hallucinate=False, reuse_single_url=False, disclose_missing=False, duplicate_finding=False,
                 extra_disclosure=False, unknown_cited_source=False) -> None:
        self.hallucinate = hallucinate
        self.reuse_single_url = reuse_single_url
        self.disclose_missing = disclose_missing
        self.duplicate_finding = duplicate_finding
        self.extra_disclosure = extra_disclosure
        self.unknown_cited_source = unknown_cited_source
        self.system_prompts: list[str] = []
        self._lock = Lock()

    def parse(self, *, system_prompt, user_prompt, schema, temperature=0.2):
        if schema is not QualitativeGroupResult:
            raise AssertionError(schema)
        with self._lock:
            self.system_prompts.append(system_prompt)
        label = user_prompt.splitlines()[0].split("：", 1)[1]
        if "本组证据引用=" in user_prompt:
            shared = json.loads(system_prompt.split("\nFROZEN_EVIDENCE_BUNDLE=", 1)[1])
            scope = json.loads(user_prompt.split("本组证据引用=", 1)[1])
            bundle = {key: shared[key] for key in scope["dimensions"]}
        else:
            bundle = json.loads(user_prompt.split("本组证据=", 1)[1])
        dimensions = []
        for index, dimension in enumerate(_GROUP_DIMENSIONS[label]):
            urls = list(bundle[dimension]["allowed_urls"])
            if self.hallucinate and label.startswith("B") and index == 0:
                urls[0] = "https://fabricated.example/not-in-artifact"
            if self.reuse_single_url and len(urls) == 1:
                urls *= 2
            evidence = [
                QualitativeEvidence(
                    source=f"冻结来源 {evidence_index + 1}",
                    url=url,
                    finding=f"{dimension} 的真实来源提供了可复核事实 {evidence_index + 1}",
                    retrieved_at="2026-08-07",
                )
                for evidence_index, url in enumerate(urls[:2])
            ]
            if self.disclose_missing:
                while len(evidence) < 2:
                    evidence.append(QualitativeEvidence(
                        source="unknown",
                        finding=f"{dimension} 第 {len(evidence) + 1} 项事实未归档，无法从冻结资料核实。",
                    ))
            if self.duplicate_finding and len(evidence) == 2:
                evidence[1].finding = evidence[0].finding
            if self.extra_disclosure:
                evidence.append(QualitativeEvidence(source="unknown", finding="额外的衍生品敞口未披露，无法从冻结资料核实。"))
            if self.unknown_cited_source:
                evidence[1] = QualitativeEvidence(
                    source="unknown", url=evidence[1].url,
                    finding="冻结文档列出了成本变化，但该条数据的最初提供者未披露。",
                    retrieved_at="2026-08-07",
                )
            dimensions.append(
                QualitativeDimension(
                    dimension_id=dimension,
                    evidence=evidence,
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
        return QualitativeGroupResult(agent_reviewed=True, group_label=label, dimensions=dimensions)


class _FlakyCachedProvider(_Provider):
    def __init__(self, workspace_root: Path) -> None:
        super().__init__()
        self.settings = SimpleNamespace(
            workspace_root=workspace_root,
            provider_name="openai",
            default_model="test-model",
            codex_reasoning_effort="high",
        )
        self.calls: dict[str, int] = {}

    def parse(self, *, system_prompt, user_prompt, schema, temperature=0.2):
        label = user_prompt.splitlines()[0].split("：", 1)[1]
        with self._lock:
            self.calls[label] = self.calls.get(label, 0) + 1
            attempt = self.calls[label]
        if label.startswith("C") and attempt == 1:
            raise RuntimeError("temporary connection failure")
        return super().parse(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            temperature=temperature,
        )


class _RepairAwareProvider(_FlakyCachedProvider):
    def __init__(self, workspace_root: Path) -> None:
        super().__init__(workspace_root)
        self.user_prompts: dict[str, list[str]] = {}

    def parse(self, *, system_prompt, user_prompt, schema, temperature=0.2):
        label = user_prompt.splitlines()[0].split("：", 1)[1]
        with self._lock:
            self.calls[label] = self.calls.get(label, 0) + 1
            self.user_prompts.setdefault(label, []).append(user_prompt)
            attempt = self.calls[label]
        original = self.hallucinate
        self.hallucinate = label.startswith("B") and attempt == 1
        try:
            return _Provider.parse(
                self,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                temperature=temperature,
            )
        finally:
            self.hallucinate = original


class UziQualitativeDeepDiveTests(unittest.TestCase):
    def test_full_agent_lite_uses_the_same_six_dimension_gate(self) -> None:
        result = apply_qualitative_deep_dive(
            _deep_base(),
            snapshot_rows=_rows(),
            llm_provider=_Provider(),
            research_profile="lite",
        )

        self.assertEqual(result["qualitative_agent_execution"]["status"], "completed")
        self.assertEqual(len(result["qualitative_deep_dive"]), 6)

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

    def test_missing_cited_sources_are_checked_after_model_attempt(self) -> None:
        provider = _Provider()
        rows = _rows()
        rows[0]["facts"]["sources"] = rows[0]["facts"]["sources"][:1]
        result = apply_qualitative_deep_dive(
            _deep_base(),
            snapshot_rows=rows,
            llm_provider=provider,
        )
        self.assertEqual(result["qualitative_agent_execution"]["status"], "incomplete")
        self.assertEqual(len(provider.system_prompts), 3)
        self.assertIn("missing_dimensions:13_policy,3_macro", result["qualitative_agent_execution"]["errors"])

    def test_neighbor_dimension_urls_cannot_satisfy_a_missing_dimension(self) -> None:
        provider = _Provider()
        rows = _rows()
        materials = next(row for row in rows if row["snapshot_type"] == "materials")
        materials["facts"]["sources"] = []
        rows.append({
            "snapshot_type": "industry",
            "symbol": "600519",
            "snapshot_date": "2026-08-07",
            "quality_status": "available",
            "facts": {
                "extra_material_like_links": [
                    {"url": "https://industry.example/not-materials/1"},
                    {"url": "https://industry.example/not-materials/2"},
                ]
            },
            "source_refs": [],
        })

        result = apply_qualitative_deep_dive(
            _deep_base(),
            snapshot_rows=rows,
            llm_provider=provider,
        )

        self.assertEqual(result["qualitative_agent_execution"]["status"], "incomplete")
        self.assertIn(
            "8_materials:frozen_citations_unavailable",
            result["qualitative_agent_execution"]["input_gaps"],
        )
        self.assertEqual(len(provider.system_prompts), 3)

    def test_same_url_can_support_two_distinct_findings(self) -> None:
        rows = _rows()
        for row in rows:
            row["facts"]["sources"] = row["facts"]["sources"][:1]
        provider = _Provider(reuse_single_url=True)
        result = apply_qualitative_deep_dive(_deep_base(), snapshot_rows=rows, llm_provider=provider)

        self.assertEqual(result["qualitative_agent_execution"]["status"], "completed")
        self.assertEqual(result["qualitative_agent_execution"]["evidence_count"], 12)
        self.assertEqual(len(provider.system_prompts), 3)
        for row in result["qualitative_deep_dive"].values():
            self.assertEqual(row["evidence"][0]["url"], row["evidence"][1]["url"])
            self.assertNotEqual(row["evidence"][0]["finding"], row["evidence"][1]["finding"])

    def test_one_fact_and_explicit_unknown_do_not_invent_a_second_source(self) -> None:
        rows = _rows()
        for row in rows:
            row["facts"]["sources"] = row["facts"]["sources"][:1]
        result = apply_qualitative_deep_dive(
            _deep_base(), snapshot_rows=rows, llm_provider=_Provider(disclose_missing=True),
        )

        self.assertEqual(result["qualitative_agent_execution"]["status"], "incomplete")
        self.assertEqual(result["qualitative_agent_execution"]["evidence_count"], 6)
        self.assertEqual(result["quality_status"], "data_gap")
        self.assertTrue(all("grounded_citations_below_two_after_analysis:1" in error
                            for error in result["qualitative_agent_execution"]["errors"]))
        for row in result["qualitative_deep_dive"].values():
            self.assertEqual(len(row["evidence"]), 1)
            self.assertEqual(row["disclosures"][0]["source"], "unknown")
            self.assertIsNone(row["disclosures"][0]["url"])
            self.assertIsNone(row["disclosures"][0]["retrieved_at"])

    def test_two_citations_with_extra_unknown_keep_disclosure_out_of_evidence(self) -> None:
        result = apply_qualitative_deep_dive(
            _deep_base(), snapshot_rows=_rows(), llm_provider=_Provider(extra_disclosure=True),
        )
        self.assertEqual(result["qualitative_agent_execution"]["status"], "completed")
        self.assertEqual(result["qualitative_agent_execution"]["evidence_count"], 12)
        self.assertEqual(result["qualitative_agent_execution"]["unknown_evidence_count"], 6)
        self.assertEqual(result["quality_status"], "degraded")
        self.assertTrue(all(len(row["evidence"]) == 2 and len(row["disclosures"]) == 1
                            for row in result["qualitative_deep_dive"].values()))

    def test_unknown_source_with_real_frozen_url_is_still_an_actual_citation(self) -> None:
        result = apply_qualitative_deep_dive(
            _deep_base(), snapshot_rows=_rows(), llm_provider=_Provider(unknown_cited_source=True),
        )
        self.assertEqual(result["qualitative_agent_execution"]["status"], "completed")
        self.assertEqual(result["qualitative_agent_execution"]["evidence_count"], 12)
        self.assertTrue(all(row["evidence"][1]["url"] and not row["disclosures"]
                            for row in result["qualitative_deep_dive"].values()))

    def test_all_unknown_is_preserved_but_cannot_complete_native_output(self) -> None:
        rows = _rows()
        for row in rows:
            row["facts"]["sources"] = []
        provider = _Provider(disclose_missing=True)
        result = apply_qualitative_deep_dive(_deep_base(), snapshot_rows=rows, llm_provider=provider)

        self.assertEqual(len(provider.system_prompts), 3)
        self.assertEqual(result["qualitative_agent_execution"]["status"], "incomplete")
        self.assertEqual(result["qualitative_agent_execution"]["completed_group_count"], 3)
        self.assertEqual(result["qualitative_agent_execution"]["evidence_count"], 0)
        self.assertEqual(result["qualitative_agent_execution"]["input_source_count"], 0)
        self.assertEqual(len(result["qualitative_deep_dive"]), 6)
        self.assertTrue(all(not row["evidence"] and len(row["disclosures"]) == 2
                            for row in result["qualitative_deep_dive"].values()))
        self.assertEqual(len(result["qualitative_agent_execution"]["errors"]), 6)
        self.assertTrue(all("no_grounded_citation_after_analysis" in error
                            for error in result["qualitative_agent_execution"]["errors"]))

    def test_duplicate_finding_cannot_pad_evidence_count(self) -> None:
        result = apply_qualitative_deep_dive(
            _deep_base(), snapshot_rows=_rows(), llm_provider=_Provider(duplicate_finding=True),
        )
        self.assertEqual(result["qualitative_agent_execution"]["status"], "incomplete")
        self.assertTrue(any(
            reason["code"] == "qualitative_repeated_finding"
            for failure in result["qualitative_agent_execution"]["failure_records"]
            for reason in failure.get("validation_reasons", [])
        ))

    def test_unknown_must_explain_missing_fact_and_facts_still_require_url(self) -> None:
        with self.assertRaises(ValueError):
            QualitativeEvidence(source="unknown", finding=" \n\t ")
        with self.assertRaises(ValueError):
            QualitativeEvidence(source="年报", finding="本期收入同比增长已经实际披露")

    def test_three_parallel_groups_cover_six_dimensions_with_grounded_urls(self) -> None:
        provider = _Provider()
        result = apply_qualitative_deep_dive(
            _deep_base(),
            snapshot_rows=_rows(),
            llm_provider=provider,
        )
        self.assertEqual(result["quality_status"], "available")
        self.assertTrue(result["agent_reviewed"])
        self.assertEqual(
            set(result["qualitative_deep_dive"]),
            {dimension for dims in _GROUP_DIMENSIONS.values() for dimension in dims},
        )
        self.assertEqual(result["qualitative_agent_execution"]["group_count"], 3)
        self.assertGreaterEqual(result["qualitative_agent_execution"]["association_count"], 3)
        self.assertEqual(len(provider.system_prompts), 3)
        self.assertEqual(len(set(provider.system_prompts)), 1)

    def test_ungrounded_model_url_invalidates_completion(self) -> None:
        result = apply_qualitative_deep_dive(
            _deep_base(),
            snapshot_rows=_rows(),
            llm_provider=_Provider(hallucinate=True),
        )
        self.assertEqual(result["quality_status"], "data_gap")
        self.assertFalse(result["agent_reviewed"])
        self.assertEqual(result["qualitative_agent_execution"]["status"], "incomplete")

    def test_successful_groups_are_cached_and_only_failed_group_is_retried(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            provider = _FlakyCachedProvider(Path(temporary_directory))
            progress: list[dict] = []

            first = apply_qualitative_deep_dive(
                _deep_base(),
                snapshot_rows=_rows(),
                llm_provider=provider,
                progress_callback=progress.append,
            )
            second = apply_qualitative_deep_dive(
                _deep_base(),
                snapshot_rows=_rows(),
                llm_provider=provider,
            )

        self.assertEqual(first["qualitative_agent_execution"]["status"], "incomplete")
        self.assertEqual(first["qualitative_agent_execution"]["completed_group_count"], 2)
        self.assertTrue(
            any(
                "C · Cost-Transmission 未通过执行或证据校验" in row["current_action"]
                for row in progress
            )
        )
        self.assertEqual(second["qualitative_agent_execution"]["status"], "completed")
        self.assertEqual(
            provider.calls,
            {
                "A · Macro-Policy": 1,
                "B · Industry-Events": 1,
                "C · Cost-Transmission": 2,
            },
        )

    def test_invalid_cached_group_is_discarded_and_regenerated(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            provider = _FlakyCachedProvider(Path(temporary_directory))
            first = apply_qualitative_deep_dive(
                _deep_base(),
                snapshot_rows=_rows(),
                llm_provider=provider,
            )
            second = apply_qualitative_deep_dive(
                _deep_base(),
                snapshot_rows=_rows(),
                llm_provider=provider,
            )
            self.assertEqual(first["qualitative_agent_execution"]["status"], "incomplete")
            self.assertEqual(second["qualitative_agent_execution"]["status"], "completed")
            calls_before = sum(provider.calls.values())
            cache_file = next(
                (Path(temporary_directory) / "cache" / "uzi_qualitative_stages").glob("*.json")
            )
            envelope = json.loads(cache_file.read_text(encoding="utf-8"))
            envelope["payload"]["dimensions"][0]["evidence"][0]["url"] = (
                "https://fabricated.example/not-in-artifact"
            )
            cache_file.write_text(json.dumps(envelope), encoding="utf-8")

            third = apply_qualitative_deep_dive(
                _deep_base(),
                snapshot_rows=_rows(),
                llm_provider=provider,
            )

        self.assertEqual(third["qualitative_agent_execution"]["status"], "completed")
        self.assertEqual(sum(provider.calls.values()), calls_before + 1)

    def test_failed_group_receives_previous_validation_error_and_caches_canonical_result(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            provider = _RepairAwareProvider(Path(temporary_directory))
            first = apply_qualitative_deep_dive(
                _deep_base(),
                snapshot_rows=_rows(),
                llm_provider=provider,
            )
            second = apply_qualitative_deep_dive(
                _deep_base(),
                snapshot_rows=_rows(),
                llm_provider=provider,
                repair_feedback=first["qualitative_agent_execution"]["errors"],
            )
            third = apply_qualitative_deep_dive(
                _deep_base(),
                snapshot_rows=_rows(),
                llm_provider=provider,
            )

        self.assertEqual(first["qualitative_agent_execution"]["status"], "incomplete")
        self.assertEqual(second["qualitative_agent_execution"]["status"], "completed")
        self.assertEqual(third["qualitative_agent_execution"]["status"], "completed")
        self.assertEqual(
            provider.calls,
            {
                "A · Macro-Policy": 1,
                "B · Industry-Events": 2,
                "C · Cost-Transmission": 1,
            },
        )
        self.assertIn("UZI_REPAIR_FEEDBACK=", provider.user_prompts["B · Industry-Events"][1])
        self.assertNotIn("UZI_REPAIR_FEEDBACK=", provider.user_prompts["A · Macro-Policy"][0])


if __name__ == "__main__":
    unittest.main()
