import json
from copy import deepcopy

from domain_packs.finance_ashare.subagents.uzi_392_analysis import _compact, _compact_json, _leaf_paths
from domain_packs.finance_ashare.subagents.uzi_prompt_transport import evidence_prompt_json, evidence_paths_prompt_json
from tests.uzi_prompt_wire import decode_evidence, decode_paths


def test_dense_numeric_sequences_shrink_without_changing_indexed_evidence():
    contracts = [{"history": list(range(250)), "dates": [f"day-{n}" for n in range(250)]} for _ in range(3)]
    raw = {"materials": {"contracts": contracts, "materials_detail": deepcopy(contracts)}, "missing": None}
    original = deepcopy(raw)
    view = _compact(raw)
    encoded = evidence_prompt_json(view)
    decoded = decode_evidence(json.loads(encoded))
    assert decoded == view and raw == original
    assert set(_leaf_paths(decoded)) == set(_leaf_paths(view))
    assert len(encoded) < len(_compact_json(view)) / 4
    assert "__uzi_prompt_omitted__" not in encoded
    assert decoded["materials"]["contracts"][0]["history"][249] == 249
    assert decoded["missing"] is None


def test_references_preserve_types_sources_and_escaped_pointer_segments():
    rows = [{"value": n} for n in range(100)]
    original = {"a~/": {"items": rows, "flag": False}, "b": {"items": rows, "flag": 0},
                "c": {"source": "different", "items": rows}, "d": _compact(rows)}
    assert decode_evidence(json.loads(evidence_prompt_json(original))) == original
    restored = decode_evidence(json.loads(evidence_prompt_json(original)))
    assert restored["a~/"]["flag"] is False and type(restored["b"]["flag"]) is int


def test_small_payload_never_expands_and_grouping_preserves_all_paths():
    small = {"history": [1, False, None]}
    assert evidence_prompt_json(small) == _compact_json(small)
    paths = [f"raw.dimensions.8_materials.data.contracts[{i}].history[{j}]" for i in range(3) for j in range(250)]
    paths += ["raw.empty", "raw.items[0].name", "ticker"]
    encoded = evidence_paths_prompt_json(paths)
    assert decode_paths(json.loads(encoded)) == sorted(paths)
    assert len(encoded) < len(_compact_json(sorted(paths))) / 3


def test_reference_to_value_within_sparse_sequence_is_resolvable():
    shared = {"body": "evidence" * 100}
    value = {"a": _compact([shared] * 40), "z": shared}
    assert decode_evidence(json.loads(evidence_prompt_json(value))) == value
