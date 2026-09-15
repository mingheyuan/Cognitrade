import json
from copy import deepcopy
from threading import local
from types import SimpleNamespace

from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    apply_qualitative_deep_dive,
)
from tests.test_uzi_qualitative_deep_dive import _deep_base, _Provider, _rows


class ReaderProvider(_Provider):
    supports_frozen_reading = True

    def __init__(self, root):
        super().__init__()
        self.settings = SimpleNamespace(workspace_root=root, default_model="fake", uzi_llm_max_concurrency=3)
        self._local = local()
        self.initial_lengths = []

    @property
    def last_call_metadata(self):
        return self._local.metadata

    def parse(self, *, system_prompt, user_prompt, schema, frozen_corpus, **kwargs):
        self.initial_lengths.append(len(system_prompt) + len(user_prompt))
        session = frozen_corpus.session()
        bundle = {}
        for doc in frozen_corpus._catalog:
            for label in doc["labels"]:
                if label.startswith("evidence."):
                    response = session.call("frozen_read", {"document_id": doc["id"], "offset": 0, "length": 6000})
                    assert response["next_offset"] is None
                    bundle[label.split(".", 1)[1]] = json.loads(response["text"])
        self._local.metadata = {"frozen_read_audit": session.audit()}
        return super().parse(system_prompt="\nFROZEN_EVIDENCE_BUNDLE=" + json.dumps(bundle),
                             user_prompt=user_prompt, schema=schema)


def test_large_input_uses_reader_and_cache_binds_all_content(tmp_path):
    rows = _rows()
    rows.append({"symbol": "600519", "snapshot_type": "fundamental", "snapshot_date": "2026-08-07",
                 "facts": {"annual_text": "原文完整保留" * 200_000}})
    before = deepcopy(rows)
    provider = ReaderProvider(tmp_path)
    first = apply_qualitative_deep_dive(_deep_base(), snapshot_rows=rows, llm_provider=provider)
    assert first["qualitative_agent_execution"]["status"] == "completed"
    assert max(provider.initial_lengths) < 25_000
    assert len(first["qualitative_agent_execution"]["frozen_read_audits"]) == 3
    assert rows == before
    second = apply_qualitative_deep_dive(_deep_base(), snapshot_rows=rows, llm_provider=provider)
    assert len(provider.initial_lengths) == 3  # All three results restored, including read audit.
    assert all(row["cache_hit"] for row in second["qualitative_agent_execution"]["frozen_read_audits"].values())
    rows[-1]["facts"]["annual_text"] += "末尾新增一条事实"
    third = apply_qualitative_deep_dive(_deep_base(), snapshot_rows=rows, llm_provider=provider)
    assert third["qualitative_agent_execution"]["status"] == "completed"
    assert len(provider.initial_lengths) == 6


def test_reader_never_includes_other_stock_or_future_snapshot(tmp_path):
    class ScopeProvider(ReaderProvider):
        def parse(self, **kwargs):
            bodies = "".join(kwargs["frozen_corpus"]._bodies.values())
            assert "FOREIGN_SECRET" not in bodies and "FUTURE_SECRET" not in bodies
            return super().parse(**kwargs)
    rows = _rows() + [
        {"symbol": "OTHER", "snapshot_type": "macro", "facts": "FOREIGN_SECRET"},
        {"symbol": "600519", "snapshot_type": "macro", "snapshot_date": "2026-08-08", "facts": "FUTURE_SECRET"},
    ]
    result = apply_qualitative_deep_dive({**_deep_base(), "symbol": "600519", "as_of_date": "2026-08-07"},
                                       snapshot_rows=rows, llm_provider=ScopeProvider(tmp_path))
    assert result["qualitative_agent_execution"]["status"] == "completed"
