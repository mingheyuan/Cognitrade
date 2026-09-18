"""Bind report citations to immutable archive metadata, never to wall time."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any


def document_attribution_conflicts(finding: str, publication_dates: list[str]) -> bool:
    """Two narrow, provable source-scope errors, not a semantic fact checker.

    A company document cannot attest to this host's collection state or an
    actual closing quote observed after its publication. Forecasts/conditions
    remain analysis. Other claims still require the native Agent fact-check.
    """
    if re.search(r"(?:本次固定数据|本地采集|采集器|固定数据.{0,30}(?:映射|查询)).{0,80}"
                 r"(?:未执行|未完成|缺失|超时|失败|未查询)", finding):
        return True
    # A claim explicitly attributed to a host snapshot/query is not a finding
    # from the attached company document, even if the numeric value is valid.
    # Do not reject ordinary document statements about market data or queries.
    if re.search(r"(?:本次|本轮|本地|固定|冻结)(?:的)?"
                 r"(?:市场|行情|期货|行业|宏观|政策|事件|数据)?快照"
                 # A date between the explicit host source and its reporting
                 # verb does not turn that source into the attached PDF.
                 r"(?:(?:截至|截止至|于|在)\s*20\d{2}"
                 r"(?:-\d{1,2}-\d{1,2}|年\d{1,2}月\d{1,2}日)[，,：:\s]*)?"
                 r"(?:显示|记录|返回|提示|将|为|中)", finding):
        return True
    if re.search(r"(?:本次|本轮|本地|有界)[^，,。；;\n]{0,20}"
                 r"(?:采集|查询)(?:结果|返回|未执行|未完成)", finding):
        return True
    # Explicit bounded-query diagnostics remain host observations when a date
    # precedes them. A nearby archived PDF does not become their evidence just
    # because the prose acknowledges that this is a query limitation.
    if re.search(r"历史有界(?:公告|事件)?查询窗口", finding):
        return True
    if re.search(r"(?:本次|本轮|本地|固定|冻结)(?:的)?"
                 r"(?:市场|行情|期货|行业|宏观|政策|公告|事件|数据)?快照"
                 r"[^。；;\n]{0,100}(?:provider_unavailable|verified_empty|查询窗口|有界查询)", finding):
        return True
    if not publication_dates or not re.search(r"收盘价|结算价|当日成交量|当日持仓量", finding):
        return False
    if re.search(r"预测|预期|预计|假设|情景|目标|如果|可能|若", finding):
        return False
    # Missing/ambiguous publication metadata is not proof of a contradiction.
    try:
        published = max(datetime.fromisoformat(value.replace("Z", "+00:00")).date()
                        for value in publication_dates)
    except (TypeError, ValueError):
        return False
    # Do not combine a historical quote with an unrelated future event date
    # elsewhere in the paragraph. Only a date attached to a quote is checked.
    for clause in re.split(r"[。；;\n]", finding):
        dates = re.findall(
            r"(20\d{2}-\d{2}-\d{2})[^，,。；;\n]{0,48}"
            r"(?:收盘价|结算价|当日成交量|当日持仓量)", clause,
        )
        for value in dates:
            try:
                if datetime.fromisoformat(value).date() > published:
                    return True
            except ValueError:
                continue
    return False


def citation_validation_context(evidence_bundle: dict, stock_documents=()) -> dict:
    records = [record for group in evidence_bundle.values()
               for record in group.get("evidence_records", [])]
    bindings = {}
    for record in [*records, *stock_documents]:
        for binding in record.get("archive_bindings", []):
            # Only explicit document bindings qualify; a nearby snapshot date,
            # publication date, or a bare URL cannot supply retrieval metadata.
            row = {key: binding.get(key) for key in
                   ("evidence_id", "url", "observed_at", "content_hash")}
            if row["evidence_id"] and row["url"] and row["content_hash"]:
                bindings[tuple(row.values())] = row
    return {"frozen_citation_bindings": list(bindings.values())}


def bind_citation(value: Any, context: Any) -> Any:
    if not isinstance(value, dict):
        return value
    bindings = context.get("frozen_citation_bindings", []) if isinstance(context, dict) else []
    evidence_id, url = value.get("evidence_id"), value.get("url")
    if evidence_id:
        candidates = [row for row in bindings if row["evidence_id"] == evidence_id]
        if url and any(row["url"] != url for row in candidates):
            # The native URL is the model's claimed source. A host-only ID
            # cannot silently replace a new/other source with a nearby PDF.
            candidates = [row for row in bindings if row["url"] == url]
            evidence_id = None
            if not candidates:
                return {**value, "evidence_id": None}
        elif not candidates:
            # evidence_id is a host-only hint, not a native required field.
            # A mistyped hint must not reject an otherwise unique frozen URL.
            # Still never accept an unknown URL or pick a different version.
            candidates = [row for row in bindings if url and row["url"] == url]
            if not candidates:
                if url:
                    # Match the native-URL-only path: this remains an unbound
                    # source for the later group check / explicit acquisition
                    # plan, not accepted evidence. A host hint must not hide
                    # the real URL before that boundary can inspect it.
                    return {**value, "evidence_id": None}
                if str(value.get("source") or "").strip().lower() == "unknown":
                    # Native Task 2.5 permits an explained missing fact. An
                    # invalid optional hint cannot turn it into a citation or
                    # make this non-factual disclosure fail schema parsing.
                    return {**value, "evidence_id": None, "retrieved_at": None}
                raise ValueError("qualitative_archive_binding_unknown")
            evidence_id = None
    else:
        candidates = [row for row in bindings if url and row["url"] == url]
        if not candidates:
            return value  # Existing unknown/non-archive and URL checks remain.
    # ID conflicts are never resolved by choosing a date or the latest row.
    if evidence_id and len(candidates) != 1:
        raise ValueError("qualitative_archive_binding_conflict")
    supplied = value.get("retrieved_at")
    if len(candidates) != 1 and supplied is not None:
        # Legacy full citations can disambiguate by their actual archive time,
        # but never by publication time or the host's current time.
        matching = [row for row in candidates if _same_observation(supplied, row["observed_at"])]
        if matching:
            candidates = matching
    # Repeated observations of identical original bytes are not conflicting
    # source versions. Do not make native URL-only citations choose a host
    # observation ID/date; retain all bindings and leave that date unknown.
    # Different content hashes still require an unambiguous fixed binding.
    identities = {(row["url"], row["content_hash"]) for row in candidates}
    if len(identities) != 1:
        raise ValueError("qualitative_archive_binding_ambiguous")
    binding = candidates[0]
    observations = {row["observed_at"] for row in candidates}
    observed = next(iter(observations)) if len(observations) == 1 else None
    # Once the document/version is unambiguous, model-copied metadata has no
    # authority. Join missing URL/date from the archive. Conflicting nonempty
    # URLs were handled above; never rewrite a claimed source or its finding.
    result = {**value, "evidence_id": binding["evidence_id"] if len(candidates) == 1 else None,
              "url": binding["url"], "retrieved_at": observed}
    if isinstance(context.get("citation_binding_audit"), list):
        context["citation_binding_audit"].append({
            "evidence_id": result["evidence_id"], "content_hash": binding["content_hash"],
            **({"equivalent_evidence_ids": sorted({row["evidence_id"] for row in candidates}),
                "observation_resolution": "multiple_observations_same_original_no_date_chosen"}
               if len(observations) > 1 else {}),
            "filled_fields": [key for key in ("evidence_id", "url", "retrieved_at")
                              if value.get(key) is None and result.get(key) is not None],
            "corrected_fields": [key for key in ("evidence_id", "url", "retrieved_at")
                                 if value.get(key) is not None and value[key] != result[key]],
        })
    return result


def _same_observation(supplied: Any, observed: Any) -> bool:
    if not isinstance(supplied, str) or not isinstance(observed, str):
        return False
    if supplied in (observed, observed[:10]):
        return True
    try:
        left, right = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (supplied, observed))
        return left.tzinfo is not None and right.tzinfo is not None and left == right
    except ValueError:
        return False
