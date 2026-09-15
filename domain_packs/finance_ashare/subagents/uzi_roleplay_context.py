"""Task3 input views; pinned persona material is not current stock evidence."""

import ast
from functools import lru_cache
from pathlib import Path
from typing import Any

from domain_packs.finance_ashare.subagents.uzi_input_views import (
    PROMPT_EVIDENCE_VIEW_VERSION,
    compact_evidence_view,
    without_native_demo_holdings,
)

_VENDOR = Path(__file__).resolve().parents[1] / "vendor/uzi_3_9_2"


@lru_cache(maxsize=2)
def _native_constants(filename: str) -> dict[str, Any]:
    # Read literal reference data, not native import side effects or random
    # get_comment() output. The repository pins and verifies these files.
    tree = ast.parse((_VENDOR / "scripts/lib" / filename).read_text(encoding="utf-8"))
    return {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


def pinned_persona_context(investor_ids: list[str]) -> dict[str, Any]:
    voices = _native_constants("investor_personas.py")["PERSONAS"]
    knowledge = _native_constants("investor_knowledge.py")
    result = {}
    for investor_id in investor_ids:
        # IDs come from the fixed panel registry, never an arbitrary file path.
        path = _VENDOR / "skill/personas" / f"{investor_id}.yaml"
        valid_id = investor_id.replace("_", "").isalnum()
        result[investor_id] = {
            "yaml": path.read_text(encoding="utf-8") if valid_id and path.is_file() else None,
            "voice_templates": voices.get(investor_id),
            "market_scope": knowledge["MARKET_SCOPE"].get(investor_id),
            "industry_affinity": knowledge["INDUSTRY_AFFINITY"].get(investor_id),
            "historical_knowledge": knowledge["KNOWN_HOLDINGS"].get(investor_id),
        }
    return result


def roleplay_raw_summary(raw: dict[str, Any]) -> dict[str, Any]:
    notices: list[dict[str, Any]] = []
    view = compact_evidence_view(
        without_native_demo_holdings(raw), max_sequence_items=6, max_text_chars=500,
        indexed_sequences=True, truncations=notices,
    )
    return {
        "raw": view,
        "projection": {
            "version": PROMPT_EVIDENCE_VIEW_VERSION,
            "truncations": notices,
            "semantics": "原始数据关键摘要；样本索引及截短明确列出，未展示不等于来源缺失。"
                         "原生建模结果及假设不是新的市场观测，示例组合不是实际持仓。",
        },
    }
