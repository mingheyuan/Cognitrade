"""Keep native cost research's exposure and hedge direction explicit.

This tests the generation instructions, not semantic correctness of model output.
The native Task 2.5 protocol and delivery checks remain unchanged.
"""

import pytest

from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as q


@pytest.mark.parametrize("workflow", ["report", "data_center"])
def test_cost_group_separates_physical_exposure_from_futures_direction(workflow):
    prompt = q._group_user_prompt(
        group_label="C · Cost-Transmission",
        dimensions=("8_materials", "9_futures"),
        evidence_bundle={"8_materials": {}, "9_futures": {}},
        workflow=workflow,
    )
    assert "先区分未来采购与已有库存/未来销售的现货敞口" in prompt
    assert "期货价格上涨时，多头盈利、空头亏损；下跌时相反" in prompt
    assert "未来采购的涨价风险通常以买入期货对冲" in prompt
    assert "库存/销售的跌价风险通常以卖出期货对冲" in prompt
    assert "不能把期货空头写成抵消采购涨价" in prompt
    assert "公司实际套保方向和名义本金未核实时只能条件分析" in prompt
    assert "基差、期限、数量和会计确认" in prompt
    assert "不能据此断言当期毛利或非经常性损益" in prompt
    assert "NATIVE_TASK_2_5_PROTOCOL_BEGIN" in prompt
    assert "从公司年报披露的\"金融衍生品\"段落判断套保敞口" in prompt


@pytest.mark.parametrize("label,dimensions", [
    ("A · Macro-Policy", ("3_macro", "13_policy")),
    ("B · Industry-Events", ("7_industry", "15_events")),
])
def test_hedge_clarification_is_scoped_to_cost_group(label, dimensions):
    prompt = q._group_user_prompt(
        group_label=label, dimensions=dimensions,
        evidence_bundle={dimension: {} for dimension in dimensions},
    )
    assert "先区分未来采购与已有库存/未来销售的现货敞口" not in prompt
