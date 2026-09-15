"""Frozen dictionary keys are not model typos or array-index aliases."""

import json
import socket
from copy import deepcopy

import pytest

from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
    _leaf_paths,
    _normalize_evidence_paths,
    _path_number,
)


@pytest.fixture
def frozen():
    return {"raw": {"dimensions": {"4_peers": {"data": {
        "global_peer_comparison": {"benchmarks": {"roe": {"2024": {"median": 18.5, "n": 4}}}},
        "history": [{"roe": 15.0}],
    }}}}}


@pytest.mark.parametrize("prefix", ["raw.dimensions.", "dimensions.", ""])
def test_real_year_dictionary_key_keeps_its_exact_frozen_identity(frozen, prefix):
    suffix = "4_peers.data.global_peer_comparison.benchmarks.roe.2024.median"
    expected = "raw.dimensions." + suffix
    paths = _normalize_evidence_paths([prefix + suffix], allowed_paths=set(_leaf_paths(frozen)))
    assert paths == [expected]
    assert _path_number(frozen, paths[0]) == 18.5


def test_year_container_expands_real_leaves_without_becoming_an_array(frozen):
    base = "raw.dimensions.4_peers.data.global_peer_comparison.benchmarks.roe.2024"
    paths = _normalize_evidence_paths([base], allowed_paths=set(_leaf_paths(frozen)))
    assert paths == [base + ".median", base + ".n"]


def test_real_array_dot_index_alias_still_works(frozen):
    paths = _normalize_evidence_paths(["4_peers.data.history.0.roe"], allowed_paths=set(_leaf_paths(frozen)))
    assert paths == ["raw.dimensions.4_peers.data.history[0].roe"]
    assert _path_number(frozen, paths[0]) == 15.0


def test_every_advertised_leaf_can_be_copied_verbatim(frozen):
    allowed = set(_leaf_paths(frozen))
    for path in allowed:
        assert _normalize_evidence_paths([path], allowed_paths=allowed) == [path]


def test_unknown_year_child_is_not_guessed_or_replaced_with_a_sibling(frozen):
    allowed = set(_leaf_paths(frozen))
    unknown = "raw.dimensions.4_peers.data.global_peer_comparison.benchmarks.roe.2024.median.fake"
    paths = _normalize_evidence_paths([unknown], allowed_paths=allowed)
    assert not set(paths) & allowed


def test_exact_numeric_dictionary_key_wins_over_index_heuristic():
    allowed = {"raw.dimensions.4_peers.data.keys.0", "raw.dimensions.4_peers.data.history[0]"}
    assert _normalize_evidence_paths(["raw.dimensions.4_peers.data.keys.0"], allowed_paths=allowed) == [
        "raw.dimensions.4_peers.data.keys.0",
    ]


def test_mixed_year_keys_and_array_indices_use_frozen_structure():
    leaf = "raw.dimensions.4_peers.data.by_year.2024.history[0].roe"
    alias = "4_peers.data.by_year.2024.history.0.roe"
    assert _normalize_evidence_paths([alias], allowed_paths={leaf}) == [leaf]


@pytest.mark.parametrize("prefix", ["raw.dimensions.", "dimensions.", ""])
def test_exact_frozen_field_takes_precedence_over_legacy_alias(prefix):
    field = "2_kline.data.indicators.macd"
    leaf = "raw.dimensions." + field
    allowed = {leaf, "raw.dimensions.2_kline.data.macd"}
    assert _normalize_evidence_paths([prefix + field], allowed_paths=allowed) == [leaf]


def test_year_path_with_prose_only_resolves_the_real_frozen_leaf(frozen):
    leaf = "raw.dimensions.4_peers.data.global_peer_comparison.benchmarks.roe.2024.median"
    assert _normalize_evidence_paths([leaf + "（行业中位数）"], allowed_paths=set(_leaf_paths(frozen))) == [leaf]


def test_split_year_and_array_paths_preserve_both_identities(frozen):
    year = "raw.dimensions.4_peers.data.global_peer_comparison.benchmarks.roe.2024.median"
    array = "raw.dimensions.4_peers.data.history[0].roe"
    assert _normalize_evidence_paths([year + "，4_peers.data.history.0.roe"], allowed_paths=set(_leaf_paths(frozen))) == [year, array]


def test_actual_analysis_stages_preserve_year_evidence_and_reuse_valid_cache(tmp_path, monkeypatch):
    from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
    from tests.test_uzi_392_analysis import _lite_snapshots
    from tests.test_uzi_synthesis_qualitative_context import CaptureProvider, research

    def denied(*args, **kwargs):
        raise AssertionError("Frozen path regression must not perform network I/O")

    monkeypatch.setattr(socket.socket, "connect", denied)
    host._initialize_native_imports()
    prepared = host._prepare_native_computation(
        host.build_uzi_raw(_lite_snapshots(), "600000"), profile="medium", school=None,
    )
    raw = prepared["raw_after_derivation"]
    raw["dimensions"]["0_basic"]["data"]["prices_by_year"] = {"2024": 12.5}
    year = "raw.dimensions.0_basic.data.prices_by_year.2024"
    before = deepcopy(prepared)

    class YearProvider(CaptureProvider):
        def __init__(self, workspace):
            super().__init__(workspace)
            self.returned_paths = []

        def parse(self, **kwargs):
            result = super().parse(**kwargs)
            if "dimensions" in result:
                result["dimensions"][0]["commentary"]["evidence_paths"] = [year]
            elif "conclusion_evidence_paths" in result:
                result["conclusion_evidence_paths"] = [year]
            elif "zone" in result:
                result["zone"]["basis_fields"] = [year]
            else:
                raise AssertionError("Unexpected fixture transport schema")
            self.returned_paths.append(deepcopy(result))
            return result

    def execute(provider):
        return host._generate_agent_analysis(
            raw, prepared["dimensions"], prepared["panel"], profile="medium", school=None,
            llm_provider=provider, recovery_tasks=[], review_feedback=[], full_agent_workflow=True,
            qualitative_research=research(),
        )

    first = YearProvider(tmp_path)
    result = execute(first)
    assert len(first.returned_paths) == 9
    assert len(first.requests) == 9, "Valid copied evidence must not cause model repair requests"
    assert sum(paths == [year] for paths in result["grounding"]["dim_commentary"].values()) == 4
    assert all(zone["basis_fields"] == [year] for zone in result["grounding"]["buy_zones"].values())
    payloads = [json.loads(path.read_text())["payload"] for path in (tmp_path / "cache/uzi_agent_stages").glob("*.json")]
    narrative = next(payload for payload in payloads if "conclusion_evidence_paths" in payload)
    assert narrative["conclusion_evidence_paths"] == [year]
    cached = YearProvider(tmp_path)
    assert execute(cached) == result
    assert not cached.requests, "Passing stage caches must remain reusable"
    assert prepared == before
