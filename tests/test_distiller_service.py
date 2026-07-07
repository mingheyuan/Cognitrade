from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.agents.distiller.service import DistillerService
from research_system.runtime.storage.jsonl import read_jsonl
from research_system.runtime.providers.provider_registry import LLMProviderError


class DistillerProviderStub:
    available = False


class DistillerChunkProviderStub:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        schema = kwargs["schema"]
        user_prompt = kwargs["user_prompt"]
        return schema.model_validate(
            {
                "source_record_id": f"llm-src-{self.calls}",
                "summary": {
                    "content_type": "text_records",
                    "overall_confidence": 0.72,
                    "needs_review": False,
                    "major_risks": [],
                    "processing_notes": ["llm_path"],
                },
                "logic_blocks": [
                    {
                        "block_id": f"llm-block-{self.calls}",
                        "block_type": "claim",
                        "source_record_id": f"llm-src-{self.calls}",
                        "source_span": {"start_line": 1, "end_line": 1, "excerpt": "excerpt"},
                        "speaker_type": "author",
                        "attribution_label": "author_original",
                        "text": user_prompt[:60],
                        "normalized_claim": f"claim-{self.calls}",
                        "symbols": [],
                        "sectors": ["AI"],
                        "time_horizon": None,
                        "direction": "bullish",
                        "evidence_refs": [],
                        "risk_flags": [],
                        "confidence": 0.72,
                    }
                ],
                "distilled_dimensions": [
                    {
                        "dimension_id": f"dim-{self.calls}",
                        "dimension_name": "AI",
                        "dimension_category": "technology",
                        "alias_hits": ["AI"],
                        "supported_claim_block_ids": [f"llm-block-{self.calls}"],
                        "evidence_block_ids": [],
                        "is_new_candidate": True,
                        "confidence": 0.72,
                        "normalization_notes": ["llm"],
                    }
                ],
            }
        )


class DistillerTimeoutProviderStub:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        raise LLMProviderError("llm_request_timeout: 25.0s")


class DistillerAliasProviderStub:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        schema = kwargs["schema"]
        return schema.model_validate(
            {
                "source_record_id": "alias-src",
                "summary": {
                    "content_type": "text_records",
                    "overall_confidence": 0.75,
                    "needs_review": False,
                    "major_risks": [],
                    "processing_notes": ["llm_path"],
                },
                "logic_blocks": [
                    {
                        "block_id": "claim-a",
                        "block_type": "claim",
                        "source_record_id": "alias-src",
                        "source_span": {"start_line": 1, "end_line": 1, "excerpt": "excerpt a"},
                        "speaker_type": "author",
                        "attribution_label": "author_original",
                        "text": "AI improves efficiency",
                        "normalized_claim": "AI improves efficiency",
                        "symbols": [],
                        "sectors": ["AI"],
                        "time_horizon": None,
                        "direction": "bullish",
                        "evidence_refs": [{"source_record_id": "alias-src", "line": 1}],
                        "risk_flags": [],
                        "confidence": 0.7,
                    },
                    {
                        "block_id": "claim-b",
                        "block_type": "claim",
                        "source_record_id": "alias-src",
                        "source_span": {"start_line": 2, "end_line": 2, "excerpt": "excerpt b"},
                        "speaker_type": "author",
                        "attribution_label": "author_original",
                        "text": "AI improves efficiency.",
                        "normalized_claim": "AI improves efficiency.",
                        "symbols": [],
                        "sectors": ["AI"],
                        "time_horizon": None,
                        "direction": "bullish",
                        "evidence_refs": [{"source_record_id": "alias-src", "line": 2}],
                        "risk_flags": [],
                        "confidence": 0.72,
                    },
                ],
                "distilled_dimensions": [
                    {
                        "dimension_id": "dim-ai",
                        "dimension_name": "AI",
                        "dimension_category": "technology",
                        "alias_hits": ["AI"],
                        "supported_claim_block_ids": ["claim-a"],
                        "evidence_block_ids": [],
                        "is_new_candidate": True,
                        "confidence": 0.75,
                        "normalization_notes": ["llm"],
                    },
                    {
                        "dimension_id": "dim-artificial-intelligence",
                        "dimension_name": "artificial intelligence",
                        "dimension_category": "technology",
                        "alias_hits": ["artificial intelligence"],
                        "supported_claim_block_ids": ["claim-b"],
                        "evidence_block_ids": [],
                        "is_new_candidate": True,
                        "confidence": 0.73,
                        "normalization_notes": ["llm"],
                    },
                ],
            }
        )


class DistillerServiceTests(unittest.TestCase):
    def test_fallback_distills_inline_source_records(self) -> None:
        service = DistillerService(DistillerProviderStub())

        result = service.run(
            query="请蒸馏这段 KOL 观点",
            source_records=[
                {
                    "source_id": "src-1",
                    "title": "kol note",
                    "content": "AI时代好的产品体验会指数级提升。\n成本增加但效率提升值得。\n如果只是平替就意义不大。",
                    "source_type": "inline_text",
                    "author": "测试作者",
                    "platform": "test",
                }
            ],
        )

        self.assertIn("summary", result)
        self.assertTrue(result["logic_blocks"])
        self.assertTrue(result["distilled_dimensions"])
        self.assertTrue(result["summary"]["needs_review"])

    def test_fallback_loads_xueqiu_statuses_jsonl(self) -> None:
        service = DistillerService(DistillerProviderStub())

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "statuses.jsonl"
            path.write_text(
                (
                    '{"id": 1, "url": "https://xueqiu.com/test/1", "user_name": "作者A", "created_at": "2026-06-24 09:35:43", '
                    '"source": "雪球", "text": "创新药出海会带来中期增长，但临床进展仍有风险。"}\n'
                ),
                encoding="utf-8",
            )

            result = service.run(
                query=f"distill kol from {path}",
                source_records=[
                    {
                        "title": "distill request",
                        "content": str(path),
                        "source_type": "xueqiu_statuses_jsonl",
                        "path": str(path),
                    }
                ],
            )

        self.assertEqual(result["summary"]["content_type"], "xueqiu_statuses")
        self.assertTrue(any(block["block_type"] in {"claim", "risk", "catalyst", "evidence"} for block in result["logic_blocks"]))
        source_ids = [block["source_record_id"] for block in result["logic_blocks"]]
        self.assertEqual(source_ids.count("1"), len(source_ids))

    def test_chunked_llm_merges_multiple_chunks(self) -> None:
        provider = DistillerChunkProviderStub()
        service = DistillerService(provider)

        result = service.run(
            query="distill multi chunk records",
            source_records=[
                {
                    "source_id": "src-1",
                    "title": "note-1",
                    "content": "AI " * 150,
                    "source_type": "inline_text",
                },
                {
                    "source_id": "src-2",
                    "title": "note-2",
                    "content": "互联网 " * 150,
                    "source_type": "inline_text",
                },
                {
                    "source_id": "src-3",
                    "title": "note-3",
                    "content": "创新药 " * 150,
                    "source_type": "inline_text",
                },
                {
                    "source_id": "src-4",
                    "title": "note-4",
                    "content": "出海 " * 150,
                    "source_type": "inline_text",
                },
            ],
        )

        self.assertEqual(provider.calls, 2)
        self.assertGreaterEqual(len(result["logic_blocks"]), 2)
        self.assertIn("chunk_count=2", result["summary"]["processing_notes"])
        self.assertIn("llm_chunk_successes=2", result["summary"]["processing_notes"])
        self.assertIn("fallback_chunks=0", result["summary"]["processing_notes"])
        self.assertTrue(any(note.startswith("child_threads=") for note in result["summary"]["processing_notes"]))
        self.assertNotIn("fallback_distillation", result["summary"]["major_risks"])

    def test_distiller_invocations_emit_nested_trace_context(self) -> None:
        provider = DistillerChunkProviderStub()
        service = DistillerService(provider)

        recorded_contexts: list[dict] = []
        service.registry.add_observer(
            lambda *, stage, invocation, record, outputs, error: recorded_contexts.append(dict(invocation.context or {}))
        )

        service.run(
            query="distill multi chunk records",
            source_records=[
                {
                    "source_id": "src-1",
                    "title": "note-1",
                    "content": "AI " * 150,
                    "source_type": "inline_text",
                },
                {
                    "source_id": "src-2",
                    "title": "note-2",
                    "content": "internet " * 150,
                    "source_type": "inline_text",
                },
                {
                    "source_id": "src-3",
                    "title": "note-3",
                    "content": "biotech " * 150,
                    "source_type": "inline_text",
                },
                {
                    "source_id": "src-4",
                    "title": "note-4",
                    "content": "export " * 150,
                    "source_type": "inline_text",
                },
            ],
        )

        context_pairs = {
            (
                str(context.get("task_name") or ""),
                str(context.get("agent_path") or ""),
            )
            for context in recorded_contexts
        }
        self.assertIn(
            ("distill:preprocess:source_normalizer", "distiller/preprocess/source_normalizer"),
            context_pairs,
        )
        self.assertIn(
            ("distill:preprocess:record_trimmer", "distiller/preprocess/record_trimmer"),
            context_pairs,
        )
        self.assertIn(
            ("distill:chunking:chunk_builder", "distiller/chunking/chunk_builder"),
            context_pairs,
        )
        self.assertTrue(
            any(
                task_name.startswith("distill:chunks:chunk_")
                and task_name.endswith(":llm_chunk_distiller")
                and agent_path.startswith("distiller/chunks/chunk_")
                and agent_path.endswith("/llm_chunk_distiller")
                for task_name, agent_path in context_pairs
            )
        )
        self.assertTrue(
            any(
                task_name.startswith("distill:chunks:chunk_")
                and (
                    task_name.endswith(":fallback_chunk_distiller")
                    or task_name.endswith(":llm_chunk_distiller")
                )
                and agent_path.startswith("distiller/chunks/chunk_")
                and (
                    agent_path.endswith("/fallback_chunk_distiller")
                    or agent_path.endswith("/llm_chunk_distiller")
                )
                for task_name, agent_path in context_pairs
            )
        )
        self.assertIn(
            ("distill:postprocess:chunk_result_merger", "distiller/postprocess/chunk_result_merger"),
            context_pairs,
        )

    def test_quote_speaker_type_and_risk_flag(self) -> None:
        service = DistillerService(DistillerProviderStub())

        result = service.run(
            query="distill quoted content",
            source_records=[
                {
                    "source_id": "src-quoted",
                    "title": "quoted",
                    "content": "> quoted line\noriginal thesis line with AI trend",
                    "source_type": "inline_text",
                }
            ],
        )

        quoted_blocks = [block for block in result["logic_blocks"] if block["text"].startswith(">")]
        self.assertTrue(quoted_blocks)
        self.assertEqual(quoted_blocks[0]["speaker_type"], "quoted")
        self.assertEqual(quoted_blocks[0]["attribution_label"], "explicit_quote")
        self.assertIn("quoted_content_present", quoted_blocks[0]["risk_flags"])

    def test_reply_repost_and_reported_attribution_are_separated(self) -> None:
        service = DistillerService(DistillerProviderStub())

        result = service.run(
            query="distill attribution variants",
            source_records=[
                {
                    "source_id": "src-attribution",
                    "title": "attr",
                    "content": "\u56de\u590d@someone \u8fd9\u4e2a\u903b\u8f91\u6210\u7acb\n// \u8f6c\u53d1\u4ed6\u4eba\u89c2\u70b9\n\u6587\u4e2d\u63d0\u5230 AI \u6210\u672c\u4e0b\u884c",
                    "source_type": "inline_text",
                }
            ],
        )

        by_text = {block["text"]: block for block in result["logic_blocks"]}
        self.assertEqual(by_text["\u56de\u590d@someone \u8fd9\u4e2a\u903b\u8f91\u6210\u7acb"]["speaker_type"], "reply")
        self.assertEqual(by_text["\u56de\u590d@someone \u8fd9\u4e2a\u903b\u8f91\u6210\u7acb"]["attribution_label"], "reply_to_other_user")
        self.assertIn("reply_attribution", by_text["\u56de\u590d@someone \u8fd9\u4e2a\u903b\u8f91\u6210\u7acb"]["risk_flags"])

        self.assertEqual(by_text["// \u8f6c\u53d1\u4ed6\u4eba\u89c2\u70b9"]["speaker_type"], "repost")
        self.assertEqual(by_text["// \u8f6c\u53d1\u4ed6\u4eba\u89c2\u70b9"]["attribution_label"], "repost_or_forward")
        self.assertIn("repost_attribution", by_text["// \u8f6c\u53d1\u4ed6\u4eba\u89c2\u70b9"]["risk_flags"])

        self.assertEqual(by_text["\u6587\u4e2d\u63d0\u5230 AI \u6210\u672c\u4e0b\u884c"]["speaker_type"], "reported")
        self.assertEqual(by_text["\u6587\u4e2d\u63d0\u5230 AI \u6210\u672c\u4e0b\u884c"]["attribution_label"], "reported_speech")
        self.assertIn("reported_attribution", by_text["\u6587\u4e2d\u63d0\u5230 AI \u6210\u672c\u4e0b\u884c"]["risk_flags"])

    def test_llm_timeout_marks_summary_risk(self) -> None:
        provider = DistillerTimeoutProviderStub()
        service = DistillerService(provider)

        result = service.run(
            query="distill with llm timeout",
            source_records=[
                {
                    "source_id": "src-timeout",
                    "title": "timeout note",
                    "content": "AI trend and internet platform comparison with enough content " * 30,
                    "source_type": "inline_text",
                }
            ],
        )

        self.assertEqual(provider.calls, 1)
        self.assertIn("fallback_distillation", result["summary"]["major_risks"])
        self.assertIn("llm_timeout", result["summary"]["major_risks"])
        self.assertTrue(any("llm_timeout" in note for note in result["summary"]["processing_notes"]))

    def test_dimension_aliases_and_duplicate_claims_are_folded(self) -> None:
        provider = DistillerAliasProviderStub()
        service = DistillerService(provider)

        result = service.run(
            query="distill duplicate claims and aliases",
            source_records=[
                {
                    "source_id": "src-alias",
                    "title": "alias note",
                    "content": "AI and artificial intelligence thesis " * 40,
                    "source_type": "inline_text",
                }
            ],
        )

        claim_blocks = [block for block in result["logic_blocks"] if block["block_type"] == "claim"]
        self.assertEqual(len(claim_blocks), 1)
        self.assertIn("duplicate_claim_folded", claim_blocks[0]["risk_flags"])
        self.assertGreaterEqual(len(claim_blocks[0]["evidence_refs"]), 2)

        dimensions = result["distilled_dimensions"]
        self.assertEqual(len(dimensions), 1)
        self.assertEqual(dimensions[0]["dimension_name"], "AI")
        self.assertIn("AI", dimensions[0]["alias_hits"])
        self.assertIn("artificial intelligence", dimensions[0]["alias_hits"])


if __name__ == "__main__":
    unittest.main()
