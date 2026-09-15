"""Replay the pinned macro main over bounded, already-validated PIT text.

Only source leaves and the historical clock are replaced in a fresh process.
This is not a historical search-engine reconstruction. Native sentiment is a
heuristic output, never a verified fact, including its empty-input defaults.
"""

from __future__ import annotations

import importlib
import multiprocessing as mp
import sys
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from research_system.market_data.contracts import canonical_hash

VERSION = "uzi-pit-macro-archive-v1"
NATIVE_SOURCE_SHA256 = "213ed66a2141d7040c2e5157ab8d763f39d034124b877d3fd65e11eb2e82f55b"
NATIVE_SEARCH_SOURCE_SHA256 = "a541c5f93eb251417b3420eab128a63f1f5fc2d4368e40f362079e18f9809289"
SCRIPTS = Path(__file__).resolve().parents[2] / "domain_packs/finance_ashare/vendor/uzi_3_9_2/scripts"
ARCHIVE_BASIS = "bounded_pit_archive_search_not_historical_web_search"
# Transport contracts and disclosed retrieval terms only, not sentiment logic.
QUERIES = {
    "rate_cycle": "{year} 中国 利率 货币政策 降息 最新",
    "us_rate": "{year} 美联储 利率周期 最新",
    "fx_trend": "{year} 人民币 汇率 走势",
    "geo_risk": "{year} 中美关系 贸易 制裁 {industry}",
    "commodity": "{year} 大宗商品 周期 CRB指数",
    "industry_macro": "{year} {industry} 宏观 政策 利好 利空",
}
TRUSTED_KEYS = frozenset(("rate_cycle", "us_rate", "fx_trend"))
TRUSTED_DOMAINS = ("stats.gov.cn", "pbc.gov.cn", "safe.gov.cn", "gov.cn",
                   "chinamoney.com.cn", "chinabond.com.cn")
ARCHIVE_TERMS = {
    "rate_cycle": ("货币政策", "存款准备金", "贷款市场报价利率", "LPR"),
    "us_rate": ("美联储", "美国联邦储备", "联邦基金利率"),
    "fx_trend": ("人民币汇率", "人民币 汇率", "汇率"),
    "geo_risk": ("中美", "贸易摩擦", "贸易政策", "贸易限制", "贸易壁垒", "贸易战", "制裁", "出口管制", "地缘"),
    "commodity": ("大宗商品", "CRB", "大宗原料"),
    "industry_macro": ("宏观政策", "产业政策", "政策支持", "国家政策", "政策环境"),
}


def _url_ok(url, *, trusted=False):
    parsed = urlsplit(str(url))
    host = (parsed.hostname or "").lower()
    return bool(parsed.scheme in {"https", "http"} and host and not parsed.username and not parsed.password
                and (not trusted or any(host == domain or host.endswith("." + domain)
                                        for domain in TRUSTED_DOMAINS)))


def archive_recording(*, symbol, basic, documents, as_of_date):
    """Freeze exact contiguous excerpts, with no selection by sentiment words.

    The caller validates publication/version proof, original file hashes and
    same-symbol snapshot bindings. Trusted leaves retain the native six-domain
    search scope. Generic leaves may use the same stock's archived documents.
    Missing industry is the native caller's default, not a sector inference.
    """
    as_of = date.fromisoformat(str(as_of_date))
    basic, documents = deepcopy(basic), deepcopy(documents)
    industry = str(basic.get("industry") or "综合")
    resolved_industry = bool(basic.get("industry") and industry != "综合")
    seen_ids = set()
    for document in documents:
        text = document.get("inline_content") or ""
        if sha256(text.encode()).hexdigest() != document.get("text_hash"):
            raise ValueError("macro_archive_text_hash_mismatch")
        if not document.get("document_hash") or document.get("evidence_id") in seen_ids:
            raise ValueError("macro_archive_document_binding_invalid")
        if not document.get("evidence_id") or not _url_ok(document.get("url")):
            raise ValueError("macro_archive_document_binding_invalid")
        seen_ids.add(document["evidence_id"])
    queries = []
    for key, template in QUERIES.items():
        rows, seen = [], set()
        for document in sorted(documents, key=lambda row: (row["document_hash"], row["evidence_id"])):
            text = document["inline_content"]
            if not _url_ok(document["url"], trusted=key in TRUSTED_KEYS):
                continue
            if key == "industry_macro" and (not resolved_industry or industry not in text):
                continue
            positions = sorted({pos for term in ARCHIVE_TERMS[key] if (pos := text.find(term)) >= 0})
            for position in positions:
                # Native consumes body[:200]. Put the actual topic inside that
                # range, without reflowing, concatenating or paraphrasing text.
                boundaries = [text.rfind(mark, 0, position) + len(mark)
                              for mark in ("\n\n", "。", "！", "？") if text.rfind(mark, 0, position) >= 0]
                start = max([0, position - 80, *boundaries])
                endings = [end + len(mark) for mark in ("\n\n", "。", "！", "？")
                           if (end := text.find(mark, position)) >= 0]
                end = min([len(text), start + 200, *endings])
                body = text[start:end]
                if key == "fx_trend" and "人民币" not in body:
                    continue
                identity = (document["document_hash"], start, end)
                if any(digest == identity[0] and start < previous_end and end > previous_start
                       for digest, previous_start, previous_end in seen):
                    continue
                seen.add(identity)
                rows.append({"title": document.get("title") or "", "url": document["url"],
                    "body": body, "evidence_id": document["evidence_id"],
                    "document_hash": document["document_hash"], "text_hash": document["text_hash"],
                    "start": start, "end": end, "span_hash": sha256(body.encode()).hexdigest()})
                if len(rows) >= 4:
                    break
            if len(rows) >= 4:
                break
        queries.append({"key": key, "query": template.format(year=as_of.year, industry=industry),
                        "trusted": rows if key in TRUSTED_KEYS else [],
                        "untrusted": [] if key in TRUSTED_KEYS else rows})
    return {"version": VERSION, "basis": ARCHIVE_BASIS, "symbol": symbol, "basic": basic,
        "as_of_date": as_of.isoformat(), "industry": industry,
        "industry_origin": "pit_basic" if resolved_industry else "native_default_unresolved",
        "queries": queries,
        "corpus": [{key: doc[key] for key in ("evidence_id", "document_hash", "text_hash")}
                   for doc in sorted(documents, key=lambda row: row["evidence_id"])],
        "selection": {"method": "fixed_topic_terms_in_bounded_archived_paragraphs",
            "query_expansions": {key: list(terms) for key, terms in ARCHIVE_TERMS.items()},
            "trusted_domains": list(TRUSTED_DOMAINS), "maximum_results_per_query": 4,
            "native_consumed_results_per_query": 3, "maximum_excerpt_characters": 200,
            "span_policy": "contiguous_sentence_or_blank_line_bounded_window_preserving_pdf_line_wraps",
            "negative_search_coverage_proven": False, "complete_archive_coverage_claimed": False,
            "industry_group_requires_resolved_basic_industry_in_document": True}}


def _validate_recording(recording):
    as_of = date.fromisoformat(recording["as_of_date"])
    if recording["version"] != VERSION or recording["basis"] != ARCHIVE_BASIS:
        raise ValueError("macro_recording_version_mismatch")
    if recording["industry"] != str(recording["basic"].get("industry") or "综合"):
        raise ValueError("macro_industry_recording_mismatch")
    if [row["key"] for row in recording["queries"]] != list(QUERIES):
        raise ValueError("macro_query_recording_incomplete")
    corpus = {row["evidence_id"]: row for row in recording["corpus"]}
    if len(corpus) != len(recording["corpus"]):
        raise ValueError("macro_corpus_duplicate")
    for query in recording["queries"]:
        key = query["key"]
        if query["query"] != QUERIES[key].format(year=as_of.year, industry=recording["industry"]):
            raise ValueError("macro_query_recording_mismatch")
        leaf, other = ("trusted", "untrusted") if key in TRUSTED_KEYS else ("untrusted", "trusted")
        if query[other] or len(query[leaf]) > 4:
            raise ValueError("macro_leaf_recording_mismatch")
        for row in query[leaf]:
            doc = corpus.get(row["evidence_id"])
            if not doc or any(row[field] != doc[field] for field in ("document_hash", "text_hash")):
                raise ValueError("macro_leaf_corpus_mismatch")
            if not _url_ok(row["url"], trusted=key in TRUSTED_KEYS) or not isinstance(row["title"], str):
                raise ValueError("macro_leaf_source_invalid")
            if (row["end"] - row["start"] != len(row["body"]) or row["start"] < 0
                    or not 0 < len(row["body"]) <= 200
                    or sha256(row["body"].encode()).hexdigest() != row["span_hash"]):
                raise ValueError("macro_leaf_span_mismatch")


def _native_worker(connection, recording):
    events, calls, failures = [], [], []
    try:
        sys.dont_write_bytecode = True

        def audit(event, args):
            if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
                events.append(event)
                raise RuntimeError("native_macro_external_io_forbidden")

        sys.addaudithook(audit)
        source = SCRIPTS / "fetch_macro.py"
        source_hash = sha256(source.read_bytes()).hexdigest()
        if source_hash != NATIVE_SOURCE_SHA256:
            raise ValueError("native_macro_source_changed")
        if sha256((SCRIPTS / "lib/web_search.py").read_bytes()).hexdigest() != NATIVE_SEARCH_SOURCE_SHA256:
            raise ValueError("native_macro_search_source_changed")
        _validate_recording(recording)
        sys.path.insert(0, str(SCRIPTS))
        module = importlib.import_module("fetch_macro")
        if Path(module.__file__).resolve() != source.resolve():
            raise ValueError("native_macro_import_mismatch")
        from lib.web_search import trusted_domains_for
        if tuple(trusted_domains_for("3_macro")[:6]) != TRUSTED_DOMAINS:
            raise ValueError("native_macro_trusted_scope_changed")
        by_query = {row["query"]: row for row in recording["queries"]}

        def search(query, *, max_results, dim_key=None):
            row = by_query.get(query)
            leaf = "trusted" if dim_key is not None else "untrusted"
            if (max_results != 4 or (dim_key is not None and dim_key != "3_macro") or row is None
                    or ((row["key"] in TRUSTED_KEYS) != (leaf == "trusted"))):
                failures.append("native_macro_query_contract_changed")
                raise ValueError(failures[-1])
            calls.append({"leaf": leaf, "key": row["key"]})
            return deepcopy(row[leaf])

        as_of = date.fromisoformat(recording["as_of_date"])

        class HistoricalClock:
            @staticmethod
            def now():
                return datetime.combine(as_of, datetime.min.time())

        module.datetime = HistoricalClock
        module.search_trusted = search
        module.search = search
        native = module.main(recording["industry"])
        if failures or calls != [{"leaf": "trusted" if key in TRUSTED_KEYS else "untrusted", "key": key}
                                 for key in QUERIES]:
            raise ValueError("native_macro_incomplete_execution")
        snippets = native["data"]["web_search_snippets"]
        coverage = {key: {"status": "archive_evidence_present" if snippets[key] else "unassessed",
                          "evidence_count": len(snippets[key]), "verified_neutral_fact": False,
                          "factual_sentiment_verified": False} for key in QUERIES}
        result = {"status": "completed" if not events else "isolation_failed",
            "native_result": native, "coverage": coverage, "recording_hash": canonical_hash(recording),
            "native_output_hash": canonical_hash(native),
            "source_sha256": source_hash, "source_path": str(source), "actual_source": ARCHIVE_BASIS,
            "calls": calls, "isolation_events": events, "contract_failures": failures}
    except BaseException as exc:  # noqa: BLE001 - never expose document bodies or credentials
        result = {"status": "isolation_failed" if events else "failed", "error_type": type(exc).__name__,
                  "isolation_events": events, "contract_failures": failures}
    try:
        connection.send(result)
    finally:
        connection.close()


def replay_macro(recording, *, deadline_at, cancellation=None):
    """Use the collection deadline; never return a late or cancelled result."""
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
    process = context.Process(target=_native_worker, args=(sender, deepcopy(recording)), name="uzi-pit-macro-replay")
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
