"""Bounded archive search + isolated replay of the pinned native moat fetcher.

This is a data-centre derivation, never a report-time acquisition. The caller
owns PIT/document validation and freezes the supplied corpus. Archive lookup
does not claim to reconstruct a historical search engine's results. Native
filters, keyword scores and result assembly run unchanged over recorded leaves.
"""

from __future__ import annotations

import importlib
import multiprocessing as mp
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from time import monotonic

from research_system.market_data.contracts import canonical_hash

VERSION = "uzi-pit-moat-archive-v3"
NATIVE_SOURCE_SHA256 = "0d466adca618ee8d34e5c8e18bad597f95a379159652a6b2ef9c76424b919427"
SCRIPTS = Path(__file__).resolve().parents[2] / "domain_packs/finance_ashare/vendor/uzi_3_9_2/scripts"
ARCHIVE_BASIS = "bounded_pit_archive_search_not_historical_web_search"
# Transport query keys, not a copy of native scoring formulas. The exact native
# query text is checked by the replay leaf; a changed upstream query fails shut.
QUERIES = {
    "intangible": "专利 核心技术 品牌壁垒 竞争优势",
    "switching": "客户粘性 转换成本 认证壁垒 大客户",
    "network": "平台效应 网络效应 用户生态",
    "scale": "市场份额 行业地位 规模优势 龙头",
    "rd": "研发投入 研发占比 技术实力",
}
ARCHIVE_TERMS = {
    "intangible": ("专利", "核心技术", "品牌壁垒", "竞争优势"),
    "switching": ("客户粘性", "转换成本", "认证", "客户", "长期合作"),
    "network": ("平台效应", "网络效应", "生态", "平台", "用户"),
    "scale": ("市场份额", "行业地位", "规模优势", "龙头"),
    "rd": ("研发投入", "研发占比", "技术实力", "研发", "研究开发"),
}


def archive_recording(*, symbol, basic, documents):
    """Select exact paragraph spans for each native query, with full lineage.

    The finite corpus and fixed topic-term matching are disclosed limitations.
No hit means unassessed, not proof that the company lacks a moat. Documents
have already passed the caller's publication/version/source checks; we also
bind their extracted text independently of the raw PDF/document content hash.
"""
    basic = deepcopy(basic)
    name = str(basic.get("name") or "").strip()
    if not name:
        raise ValueError("company_name_required")
    documents = deepcopy(documents)
    for document in documents:
        text = document.get("inline_content") or ""
        if sha256(text.encode()).hexdigest() != document.get("text_hash"):
            raise ValueError("archive_text_hash_mismatch")
    queries = []
    for key, ending in QUERIES.items():
        rows, seen = [], set()
        terms = ARCHIVE_TERMS[key]
        for document in sorted(documents, key=lambda row: (row["document_hash"], row["evidence_id"])):
            text = document["inline_content"]
            # Keep each hit's actual paragraph (bounded to 600 characters).
            # Offsets describe the original extracted text, not reflowed prose.
            positions = sorted({position for term in terms if (position := text.find(term)) >= 0})
            for position in positions:
                start = max(text.rfind("\n", 0, position) + 1, position - 200)
                newline = text.find("\n", position)
                end = min(len(text) if newline < 0 else newline, start + 600)
                identity = (document["document_hash"], start, end)
                if identity in seen or any(
                    digest == document["document_hash"] and start < previous_end and end > previous_start
                    for digest, previous_start, previous_end in seen
                ):
                    continue
                seen.add(identity)
                rows.append({
                    "title": document.get("title") or "",
                    "url": document["url"], "body": text[start:end],
                    "evidence_id": document["evidence_id"],
                    "document_hash": document["document_hash"],
                    "text_hash": document["text_hash"], "start": start, "end": end,
                })
                if len(rows) >= 6:
                    break
            if len(rows) >= 6:
                break
        queries.append({"key": key, "query": f"{name} 上市公司 {ending}",
                        "trusted": rows, "untrusted": []})
    return {
        "version": VERSION, "basis": ARCHIVE_BASIS, "symbol": symbol,
        "basic": basic, "queries": queries,
        "corpus": [{key: doc[key] for key in ("evidence_id", "document_hash", "text_hash")}
                   for doc in sorted(documents, key=lambda row: row["evidence_id"])],
        "selection": {"method": "fixed_topic_terms_in_bounded_archived_paragraphs",
                      "query_expansions": {key: list(terms) for key, terms in ARCHIVE_TERMS.items()},
                      "overlap_policy": "retain_first_window_per_overlapping_document_span",
                      "maximum_results_per_query": 6, "maximum_excerpt_characters": 600,
                      "negative_search_coverage_proven": False},
    }


def _native_worker(connection, recording):
    """Only this fresh spawned process replaces source leaves; parent untouched."""
    events = []
    try:
        sys.dont_write_bytecode = True

        def audit(event, args):
            if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
                events.append(event)
                raise RuntimeError("native_moat_external_io_forbidden")

        sys.addaudithook(audit)
        source = SCRIPTS / "fetch_moat.py"
        source_hash = sha256(source.read_bytes()).hexdigest()
        if source_hash != NATIVE_SOURCE_SHA256:
            raise ValueError("native_moat_source_changed")
        sys.path.insert(0, str(SCRIPTS))
        module = importlib.import_module("fetch_moat")
        if Path(module.__file__).resolve() != source:
            raise ValueError("native_moat_import_mismatch")
        calls = []
        by_query = {row["query"]: row for row in recording["queries"]}
        if len(by_query) != 5:
            raise ValueError("native_moat_query_recording_incomplete")

        def basic(ticker):
            if ticker.full != recording["symbol"]:
                raise ValueError("native_moat_symbol_mismatch")
            calls.append({"leaf": "fetch_basic"})
            return deepcopy(recording["basic"])

        def search(query, *, max_results, dim_key=None):
            if max_results != 6 or (dim_key is not None and dim_key != "14_moat"):
                raise ValueError("native_moat_query_contract_changed")
            row = by_query.get(query)
            if row is None:
                raise ValueError("native_moat_query_not_recorded")
            leaf = "trusted" if dim_key else "untrusted"
            calls.append({"leaf": leaf, "key": row["key"]})
            return deepcopy(row[leaf])

        module.ds.fetch_basic = basic
        module.search_trusted = search
        module.search = search
        native = module.main(recording["symbol"])
        if {row.get("key") for row in calls if row["leaf"] == "trusted"} != set(QUERIES):
            raise ValueError("native_moat_incomplete_execution")
        result = {"status": "completed" if not events else "isolation_failed",
                  "native_result": native, "recording_hash": canonical_hash(recording),
                  "source_sha256": source_hash, "source_path": str(source),
                  "actual_source": ARCHIVE_BASIS,
                  "calls": calls, "isolation_events": events}
    except BaseException as exc:  # noqa: BLE001 - child failure envelope never includes raw inputs
        result = {"status": "isolation_failed" if events else "failed",
                  "error_type": type(exc).__name__, "isolation_events": events}
    try:
        connection.send(result)
    finally:
        connection.close()


def replay_moat(recording, *, deadline_at, cancellation=None):
    """Execute once within the caller's existing deadline, cancelling the worker."""
    def stopped():
        if cancellation is not None and cancellation.is_set():
            return "cancelled"
        if monotonic() >= deadline_at:
            return "timed_out"
        return None

    if reason := stopped():
        return {"status": reason}
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_native_worker, args=(sender, deepcopy(recording)),
                              name="uzi-pit-moat-replay")
    started = False
    try:
        process.start()
        started = True
        sender.close()
        while not (reason := stopped()):
            if receiver.poll(min(0.05, max(0, deadline_at - monotonic()))):
                try:
                    result = receiver.recv()
                except EOFError:
                    return {"status": "failed", "error_type": "WorkerExited"}
                # Do not publish a result received after cancellation/deadline.
                return {"status": reason} if (reason := stopped()) else result
            if not process.is_alive():
                return {"status": "failed", "error_type": "WorkerExited"}
        return {"status": reason}
    finally:
        receiver.close()
        sender.close()
        if started:
            process.join(timeout=0.1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            process.close()
