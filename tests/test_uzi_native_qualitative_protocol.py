"""Research prompts must contain the pinned methodology, not a host summary."""

from pathlib import Path

import pytest

from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    _GROUPS,
    _group_user_prompt,
)


@pytest.mark.parametrize("label,dimensions", _GROUPS)
def test_each_group_receives_the_exact_native_question_protocol(label, dimensions):
    path = Path(__file__).resolve().parents[1] / "domain_packs/finance_ashare/vendor/uzi_3_9_2/skill/references/task2.5-qualitative-deep-dive.md"
    prompt = _group_user_prompt(
        group_label=label, dimensions=dimensions,
        evidence_bundle={key: {"allowed_urls": []} for key in dimensions},
    )
    assert path.read_text(encoding="utf-8") in prompt
    assert "报告阶段禁止执行其中的联网采集动作" in prompt
    assert "数据中心" in prompt


@pytest.mark.parametrize("label,dimensions", _GROUPS)
def test_each_research_group_receives_the_global_native_factcheck_duty(label, dimensions):
    path = Path(__file__).resolve().parents[1] / "domain_packs/finance_ashare/vendor/uzi_3_9_2/skill/SKILL.md"
    skill = path.read_text(encoding="utf-8")
    start = skill.index("### ⛔ HARD-GATE-FACTCHECK")
    protocol = skill[start:skill.index("### ⛔ HARD-GATE-DATAGAPS", start)].strip()
    prompt = _group_user_prompt(group_label=label, dimensions=dimensions,
                               evidence_bundle={key: {"allowed_urls": []} for key in dimensions})
    assert protocol in prompt
    assert "stage2 不会自动检测事实正确性" in prompt
