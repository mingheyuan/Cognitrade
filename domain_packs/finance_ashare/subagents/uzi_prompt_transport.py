"""Compact prompt wire format; canonical evidence and validators stay unchanged."""

import json
from typing import Any, Iterable

from .uzi_input_views import is_prompt_omission


PROMPT_TRANSPORT_VERSION = "uzi-evidence-wire-v1"
PROMPT_TRANSPORT_INSTRUCTIONS = (
    "HOST_EVIDENCE_WIRE_V1 仅压缩传输，不改变原始事实或允许引用的路径。"
    "__uzi_indexed_sequence__ 的 length 是原数组长度，items 的键是原始下标；"
    "未列出的下标仅未展示，不代表缺数据，引用仍使用原始 [下标]。"
    "__uzi_same_value_reference__ 是当前 JSON 根内的 RFC6901 指针，仅复用相同值；"
    "不合并来源或权限，引用仍使用该值所在的原始证据路径。"
    "ALLOWED_LEAF_EVIDENCE_PATHS 若为 __uzi_path_groups__，每组前缀与后缀直接拼接"
    "得到完整合法路径；返回完整路径，不返回压缩标记。\n"
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def evidence_prompt_json(value: Any) -> str:
    """Encode an already-selected view without dropping another fact.

    Sparse arrays replace repeated omission objects, not real/null values.
    Backward references share only byte-identical subtrees and point into the
    encoded JSON. Neither operation changes the Python evidence used to check
    model citations. Fall back to ordinary JSON if encoding would grow it.
    """
    seen: dict[str, str] = {}

    def encode(item: Any, pointer: str) -> Any:
        if isinstance(item, (dict, list)):
            original = _json(item)
            if len(original) >= 512:
                if original in seen:
                    return {"__uzi_same_value_reference__": seen[original]}
                seen[original] = pointer
        if isinstance(item, dict):
            return {key: encode(child, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
                    for key, child in sorted(item.items())}
        if isinstance(item, list):
            if any(is_prompt_omission(child) for child in item):
                return {"__uzi_indexed_sequence__": {
                    "length": len(item),
                    "items": {str(index): encode(child, pointer + f"/__uzi_indexed_sequence__/items/{index}")
                              for index, child in enumerate(item) if not is_prompt_omission(child)},
                }}
            return [encode(child, pointer + f"/{index}") for index, child in enumerate(item)]
        return item

    original = _json(value)
    encoded = _json(encode(value, ""))
    return encoded if len(encoded) < len(original) else original


def evidence_paths_prompt_json(paths: Iterable[str]) -> str:
    """Lossless prefix grouping, not a smaller citation whitelist."""
    paths = sorted(set(paths))
    original = _json(paths)
    if len(original) < 8192:
        return original
    groups: dict[str, list[str]] = {}
    for path in paths:
        # Include the delimiter in the prefix so expansion is concatenation,
        # including indexed primitive arrays and top-level scalar paths.
        split = max(path.rfind("."), path.rfind("["))
        prefix, suffix = (path[:split + 1], path[split + 1:]) if split >= 0 else ("", path)
        groups.setdefault(prefix, []).append(suffix)
    encoded = _json({"__uzi_path_groups__": groups})
    return encoded if len(encoded) < len(original) else original
