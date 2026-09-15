"""Bind a report's explicit period definition to its qualitative disclosures.

This is citation completion, not financial-period inference. Numeric facts,
comparisons and forecasts still need their own explicit period quotations.
The compiler and persistence boundary run the same check on verified text.
"""

import re
from datetime import date

PERIOD_BINDING_VERSION = "uzi-report-period-citation-v1"

# Only current company descriptors can inherit the issuer's report scope.
# In particular cost_share/growth/hedging_notional and macro series cannot.
REPORT_SCOPED_FIELDS = {
    "7_industry": {"industry", "name"},
    "8_materials": {"core_material", "industry_resolved", "supply_security"},
    "9_futures": {"hedging_disclosure_status"},
}
_DAY = r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
_DEFINITION = re.compile(
    r"(?:报告期|本报告期)(?:[、，, /]*(?:本期|本报告期))*\s*(?:指|是指|为|：|:)\s*"
    + _DAY + r"\s*(?:至|到|—|－|-)\s*" + _DAY)
_NON_CURRENT = re.compile(r"上年|去年|上期|前期|同期|此前|曾经|历史|预计|预测|计划|拟于|拟在|报告期后|期后事项|未来|次年|明年")


def _fact_sentence(text, citation):
    """Include the sentence around a shortened quote, not just the model cut."""
    start, end = citation["start"], citation["end"]
    # Checkbox disclosures are self-contained sections, not numeric tables.
    if re.fullmatch(r"衍生品投资情况\s*[□√适用不\s]+", citation["quote"]):
        preceding_lines = text[:start].splitlines()[-2:]
        return "\n".join([*preceding_lines, citation["quote"]])
    boundaries = "。！？；"
    left = max((text.rfind(char, 0, start) for char in boundaries), default=-1) + 1
    # A quoted sentence already ending at a boundary must not absorb the next.
    if end and text[end - 1] in boundaries:
        right = end
    else:
        ends = [position for char in boundaries if (position := text.find(char, end)) >= 0]
        right = min(ends) + 1 if ends else len(text)
    return text[left:right] if right - left <= 2000 else None


def report_period_citation(*, dimension, field, value, unit, period, symbol,
                           documents, citations):
    """Return one exact, same-issuer/same-document definition or no binding.

    The definition must be unique, have the requested dates, and accompany a
    value-supporting citation without a competing period. No URL/title metadata
    is trusted in lieu of the original document body. Does not change values.
    """
    from research_system.market_data.research_suggestions import literal_text_present

    if (field not in REPORT_SCOPED_FIELDS.get(dimension, ()) or unit not in {"文本", "text"}
            or not isinstance(value, str) or re.search(r"\d", value) or not symbol):
        return None
    code = symbol.split(".")[0]
    if not re.fullmatch(r"\d{6}", code):
        return None
    candidates = []
    for citation in citations:
        if not literal_text_present(value, citation["quote"]):
            continue
        rows = [doc for doc in documents if doc.get("evidence_id") == citation["evidence_id"]
                and doc.get("document_hash") == citation["document_hash"]]
        if len(rows) != 1:
            continue
        text = rows[0].get("inline_content")
        if not isinstance(text, str) or text[citation["start"]:citation["end"]] != citation["quote"]:
            continue
        issuer_codes = set(re.findall(r"(?:公司|证券|股票)代码\s*[：:]?\s*(\d{6})(?!\d)", text[:2000]))
        if issuer_codes != {code} or not re.search(r"\d{4}\s*年(?:半年度|年度|第?[一二三四1234]季度)报告", text[:400]):
            continue
        definitions = list(_DEFINITION.finditer(text))
        if len(definitions) != 1:
            continue
        definition = definitions[0]
        try:
            dates = tuple(date(*map(int, definition.groups()[i:i + 3])) for i in (0, 3))
        except ValueError:
            continue
        if dates != period:
            continue
        sentence = _fact_sentence(text, citation)
        if not sentence or _NON_CURRENT.search(sentence):
            continue
        years = {int(year) for year in re.findall(r"(?<!\d)(\d{4})\s*年", sentence)}
        if years - {period[0].year}:
            continue
        try:
            if any(not period[0] <= date(*map(int, parts)) <= period[1]
                   for parts in re.findall(_DAY, sentence)):
                continue
        except ValueError:
            continue
        candidate = {"evidence_id": citation["evidence_id"], "document_hash": citation["document_hash"],
                     "start": definition.start(), "end": definition.end(), "quote": definition.group()}
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None
