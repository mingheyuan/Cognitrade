"""Corner cases from pinned score_fns.generate_synthesis's six-field merge."""
from copy import deepcopy

import pytest

from scripts.run_uzi_agent_comparison import apply_panel_rows


@pytest.mark.parametrize("case", ["null", "verdict_and_confidence", "comment_fallback"])
def test_reference_context_merge_uses_native_field_and_null_semantics(case):
    panel = {"panel_consensus": 23, "investors": [{
        "investor_id": "one", "signal": "bearish", "score": 20,
        "confidence": 60, "headline": "Original headline", "comment": "Original comment",
        "verdict": "Original verdict", "reasoning": "Original reasoning",
    }]}
    update = {"investor_id": "one"}
    expected = deepcopy(panel)
    row = expected["investors"][0]
    if case == "null":
        update.update({key: None for key in ("signal", "score", "headline", "reasoning", "comment", "verdict")})
    elif case == "verdict_and_confidence":
        update.update({"verdict": "New verdict", "confidence": 99})
        row["verdict"] = "New verdict"  # Confidence is not a native override field.
    else:
        update.update({"comment": "", "headline": "Concrete fact 12%"})
        row.update({"headline": "Concrete fact 12%", "comment": "Concrete fact 12%"})
    original = deepcopy(panel)
    assert apply_panel_rows(panel, [update]) == expected
    assert panel == original
