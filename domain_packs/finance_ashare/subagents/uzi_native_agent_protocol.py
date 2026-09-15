"""Pinned SKILL duties for split Agent stages, not a second methodology."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

_SKILL = Path(__file__).resolve().parents[1] / "vendor/uzi_3_9_2/skill/SKILL.md"
_SECTIONS = {
    "factcheck": ("### ⛔ HARD-GATE-FACTCHECK", "### ⛔ HARD-GATE-DATAGAPS"),
    "assumptions": ("### 🧠 你的判断环节（Task 1.5 假设审查）", "### Task 2 ·"),
    "dimensions": ("### Task 2 ·", "### Task 3 ·"),
    "panel_review": ("### Step 3.3 · 合并 Sub-Agent 结果", "### Task 4 ·"),
    "synthesis": ("### Task 4 ·", "### Task 5 ·"),
    "copy_review": ("### 🧠 你的金句审查", "### 完成验证"),
    "buy_zones": ("**4.5 四派系买入区间**", "### 写入（v2.2 闭环机制）"),
    "personas": ("### ⛔ HARD-GATE-PERSONA-ROLEPLAY", "### ⛔ HARD-GATE-QUALITATIVE"),
    "warning_review": ("### ⛔ HARD-GATE-AGENT-SELF-REVIEW", "### ⛔ HARD-GATE-FACTCHECK"),
}
_STAGE_SECTIONS = {
    "dimensions": ("factcheck", "assumptions", "dimensions"),
    "narrative": ("factcheck", "assumptions", "panel_review", "synthesis", "copy_review"),
    "buy_zone": ("factcheck", "buy_zones"),
    "roleplay": ("factcheck", "personas"),
    "warning_review": ("warning_review",),
}


@lru_cache(maxsize=1)
def native_factcheck_protocol() -> str:
    """The same global SKILL fact-check duty also applies to Task 2.5."""
    skill = _SKILL.read_text(encoding="utf-8")
    start, end = _SECTIONS["factcheck"]
    offset = skill.index(start)
    return skill[offset:skill.index(end, offset)].strip()


@lru_cache(maxsize=5)
def native_agent_protocol(stage: Literal["dimensions", "narrative", "buy_zone", "roleplay", "warning_review"]) -> str:
    skill = _SKILL.read_text(encoding="utf-8")
    sections = []
    for key in _STAGE_SECTIONS[stage]:
        start, end = _SECTIONS[key]
        offset = skill.index(start)
        sections.append(skill[offset:skill.index(end, offset)].strip())
    if stage == "roleplay":
        sections.append((_SKILL.parent / "references/task3-agent-evaluation.md").read_text(encoding="utf-8"))
    return (
        "以下为固定 UZI 3.9.4 SKILL 的原文职责，不是另一套公式或新交付门。"
        "本阶段仅完成当前 JSON 契约对应部分；其他部分由对应阶段完成。"
        "报告阶段不执行联网、改写冻结数据或脚本操作；需要补证或更改模型假设时，"
        "说明依据、未知与限制，交给显式数据中心生成新输入，不改本次冻结数值。"
        "原文示例及示例数字不是本股事实，不能作为证据。"
        "逐维评论承担 Task2 与假设审查；narrative 的 panel_insights 承担整体评审，"
        "core_conclusion/risks 承担估值冲突、催化剂与风险排序；"
        "四派买区由各自阶段计算并解释，不是 dashboard.battle_plan 的替代。"
        "输出前按原文复核本阶段的事实出处和适用条件；材料不够时明确披露，不虚构。\n"
        + "\n\n".join(sections)
        + "\n"
    )
