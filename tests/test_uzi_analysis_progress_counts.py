"""Progress describes validated completions, never a parallel stage ordinal."""
from threading import Event

from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
from tests.test_uzi_392_analysis import (
    _lite_snapshots,
    _LiteAgentProvider,
    _RepairingBuyZoneProvider,
)


def _check_counts(progress):
    completed = [row for row in progress if "已完成" in row.get("current_action", "")]
    assert len(completed) == 7
    assert [row.get("completed_stages") for row in completed] == list(range(1, 8))
    assert len({row["stage"] for row in completed}) == 7
    tracked = [row for row in progress if "completed_stages" in row]
    assert [row["progress_percent"] for row in tracked] == sorted(row["progress_percent"] for row in tracked)
    return completed


def test_real_analysis_reverse_zone_completion_is_monotonic():
    order = ["youzi", "technical", "growth", "value"]
    ready = {name: Event() for name in order}
    ready[order[0]].set()
    progress = []

    class Provider(_LiteAgentProvider):
        def parse(self, **kwargs):
            if kwargs["schema"] is host.AgentBuyZonePayload:
                name = next(name for name in order if f" {name} 买入区间" in kwargs["user_prompt"])
                assert ready[name].wait(5)
            return super().parse(**kwargs)

    def observe(row):
        progress.append(row)
        if "已完成" not in row.get("current_action", ""):
            return
        stage = row.get("stage", "")
        if stage.startswith("buy_zone_"):
            index = order.index(stage.removeprefix("buy_zone_"))
            if index + 1 < len(order):
                ready[order[index + 1]].set()

    provider = Provider()
    provider.settings.uzi_llm_max_concurrency = 4
    result = host.execute_native_analysis(_lite_snapshots(), ["600000"], profile="lite",
                                          llm_provider=provider, progress_callback=observe)
    assert result["report_allowed"]
    completed = _check_counts(progress)
    assert [row["stage"] for row in completed[-4:]] == [f"buy_zone_{name}" for name in order]


def test_repair_attempts_do_not_count_as_extra_completed_stages():
    provider = _RepairingBuyZoneProvider()
    progress = []
    result = host.execute_native_analysis(_lite_snapshots(), ["600000"], profile="lite",
                                          llm_provider=provider, progress_callback=progress.append)
    assert result["report_allowed"] and provider.youzi_calls == 2
    _check_counts(progress)


def test_revalidated_cache_counts_once_in_each_new_analysis(tmp_path, monkeypatch):
    # Native rule panels choose signature phrases randomly. This test promises
    # identical prompt inputs; fix that fixture randomness rather than allowing
    # the cache to ignore changed methodology content.
    monkeypatch.setattr("random.choice", lambda rows: rows[0])
    first = _LiteAgentProvider()
    first.settings.workspace_root = tmp_path
    host.execute_native_analysis(_lite_snapshots(), ["600000"], profile="lite", llm_provider=first)
    second = _LiteAgentProvider()
    second.settings.workspace_root = tmp_path
    progress = []
    result = host.execute_native_analysis(_lite_snapshots(), ["600000"], profile="lite",
                                          llm_provider=second, progress_callback=progress.append)
    assert result["report_allowed"] and second.calls == 0
    completed = _check_counts(progress)
    assert all(row["cache_hit"] for row in completed)
