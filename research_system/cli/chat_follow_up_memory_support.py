from __future__ import annotations


def short_term_memory_reply(state) -> str:
    if not state.last_user_query and not state.last_result_summary and not state.last_result_conclusion:
        return "\u8fd9\u8f6e\u4f1a\u8bdd\u91cc\u6211\u8fd8\u6ca1\u6709\u53ef\u56de\u987e\u7684\u4e0a\u4e00\u6761\u7814\u7a76\u5185\u5bb9\u3002"
    lines = ["\u521a\u521a\u6211\u4eec\u5728\u804a\uff1a"]
    if state.last_user_query:
        lines.append(f"\u95ee\u9898\uff1a{state.last_user_query}")
    if state.last_result_intent:
        lines.append(f"\u7c7b\u578b\uff1a{state.last_result_intent}")
    if state.last_result_summary:
        lines.append(f"\u6458\u8981\uff1a{state.last_result_summary}")
    elif state.last_result_conclusion:
        lines.append(f"\u7ed3\u8bba\uff1a{state.last_result_conclusion}")
    if state.last_run_id:
        lines.append(f"run_id\uff1a{state.last_run_id}")
    return "\n".join(lines)
