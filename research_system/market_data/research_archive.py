"""Explicit research-source acquisition with immutable content and PIT proof.

Search results discover documents only. Their snippets, dates and URLs never
become historical evidence without validating the downloaded source document.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from itertools import zip_longest
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from research_system.adapters.vendors.uzi_web_search_adapter import (
    TRUSTED_DOMAINS_BY_DIM,
)
from research_system.market_data.contracts import EvidenceGrade, EvidenceInput
from research_system.market_data.research_archive_text import (
    ResearchTextError,
    archive_text,
    extract_pdf_text,
)

ARCHIVE_VERSION = "research-archive-v4-document-body"
RESEARCH_SOURCES = {
    "3_macro": ("stats.gov.cn", "pbc.gov.cn", "gov.cn"),
    "13_policy": ("gov.cn", "pbc.gov.cn", "miit.gov.cn"),
    "7_industry": ("stats.gov.cn", "miit.gov.cn", "cninfo.com.cn", "gov.cn"),
    "15_events": ("cninfo.com.cn",),
    "8_materials": ("shfe.com.cn", "dce.com.cn", "czce.com.cn", "cninfo.com.cn"),
    "9_futures": ("shfe.com.cn", "dce.com.cn", "czce.com.cn", "cninfo.com.cn"),
    "6_research": ("data.eastmoney.com", "pdf.dfcfw.com"),
}
# The original-body archive must not discard sources already supported by the
# existing UZI search adapter. These are public document hosts, not permission
# to enable browser/login sources; their switches remain with that adapter.
RESEARCH_SOURCES = {
    dimension: tuple(dict.fromkeys((*roots, *TRUSTED_DOMAINS_BY_DIM.get(dimension.split("_", 1)[1], ()))))
    for dimension, roots in RESEARCH_SOURCES.items()
}
_KEYWORDS = {
    "3_macro": "经济 利率 货币政策 数据发布",
    "13_policy": "政策 通知 公告",
    "7_industry": "行业 年度报告 经营情况",
    "15_events": "公告 合同 业绩 回购",
    "8_materials": "原材料 成本 年度报告 ESG",
    "9_futures": "期货 套期保值 衍生品 年度报告",
    "6_research": "研究报告 评级",
}
_MAX_BYTES = 12 * 1024 * 1024
_RESEARCH_CANDIDATE_LIMIT = 12
_RELEVANCE_TERMS = {
    "3_macro": ("经济", "GDP", "CPI", "PMI", "货币", "利率", "通胀", "信贷"),
    "13_policy": ("政策", "通知", "办法", "意见", "规定", "规划", "公告"),
    "7_industry": ("行业", "市场规模", "竞争", "经营情况", "主营业务", "年度报告"),
    "15_events": (
        "公告",
        "合同",
        "业绩",
        "回购",
        "增持",
        "减持",
        "重大事项",
        "年度报告",
    ),
    "8_materials": ("原材料", "现货", "仓单", "库存", "交割", "供需", "商品", "营业成本", "材料成本", "采购", "晶圆", "封装测试"),
    "9_futures": ("期货", "期权", "合约", "仓单", "交割", "结算", "套期保值", "套保", "衍生品", "对冲"),
    "6_research": ("研究报告", "证券研究", "投资评级", "盈利预测", "评级", "估值"),
}


class ArchiveEvidenceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _ArchiveBodyUnusable(ArchiveEvidenceError):
    """Only bounded statistics and fixed reasons; never retain the response/body."""

    def __init__(self, *, response, content, text, extraction, reasons):
        super().__init__("archive_body_unusable", "未取得可引用原文正文")
        declared = str((getattr(response, "headers", None) or {}).get("Content-Type") or "")
        declared = declared.split(";", 1)[0].strip().lower()
        content_type = {
            "": "missing", "application/pdf": "pdf", "text/html": "html",
            "application/xhtml+xml": "html", "text/plain": "plain_text",
            "application/json": "json", "application/octet-stream": "binary",
        }.get(declared, "other")
        status = getattr(response, "status_code", None)
        is_pdf = content.startswith(b"%PDF")
        self.body_diagnostics = {
            "version": "archive-body-diagnostics-v1",
            "http_status": status if type(status) is int and 100 <= status <= 599 else None,
            "content_type_class": content_type,
            "response_bytes": len(content),
            "text_characters": len(text),
            "stripped_text_characters": len(text.strip()),
            "is_pdf": is_pdf,
            "extraction_branch": "pdf" if is_pdf else "html",
            "page_count": extraction.get("page_count") if is_pdf else None,
            "pages_processed": extraction.get("pages_processed") if is_pdf else None,
            "pages_without_text_count": len(extraction.get("pages_without_text", [])) if is_pdf else None,
            "rejection_reasons": list(reasons),
        }


@dataclass(frozen=True)
class ArchiveCandidate:
    url: str
    title: str = ""
    published_at: datetime | None = None
    publisher_document_id: str | None = None
    listing_url: str | None = None
    listing_hash: str | None = None
    immutable_attachment: bool = False
    security_code: str | None = None
    attachment_proof_basis: str | None = None


def _fair_research_candidates(groups: list[list[ArchiveCandidate]]) -> list[ArchiveCandidate]:
    """Share the existing research budget across discovered source queues.

    These are only candidates. Each still passes the normal URL, document,
    relevance and PIT checks; a search snippet is never promoted to evidence.
    """
    selected, seen = [], set()
    for row in zip_longest(*groups):
        for candidate in row:
            if candidate is None or candidate.url in seen:
                continue
            seen.add(candidate.url)
            selected.append(candidate)
            if len(selected) == _RESEARCH_CANDIDATE_LIMIT:
                return selected
    return selected


def research_publication_projection(evidence) -> dict[str, Any]:
    """Disclose new observed-only records without relabelling old evidence.

    Storage keeps its non-null conservative PIT bound for compatibility.
    Reports and evidence details instead expose an unknown publisher release
    date plus the actual observation bound. Existing inputs stay byte-identical.
    """
    metadata = getattr(evidence, "metadata_json", None)
    if metadata is None:
        metadata = getattr(evidence, "metadata", {})
    if (
        getattr(evidence, "source_kind", None) != "research_document"
        or not isinstance(metadata, dict)
        or metadata.get("publication_time_basis") != "observed_upper_bound"
    ):
        return {}
    observed = evidence.observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return {
        "published_at": None,
        "publisher_published_at": None,
        "visible_by": observed.astimezone(timezone.utc).isoformat(),
        "publication_time_basis": "observed_upper_bound",
        "publication_time_note": "首次发布日期未知；visible_by 仅表示本系统实际观察到正文的时间，不得当作发布日或更早可见证明。",
    }


class ResearchDocumentStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def put(self, content: bytes, *, suffix: str) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        directory = self.root / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{digest}.{suffix}"
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ArchiveEvidenceError(
                    "archive_content_corrupt", "已归档文档内容 hash 不匹配"
                )
        else:
            with NamedTemporaryFile(dir=directory, delete=False) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, destination)
        return digest, str(destination.resolve())


def _html_document_body(soup: BeautifulSoup, url: str):
    """Select one document, shared by text and attachment extraction.

    Publisher-specific markup is scoped to its verified host, not a general
    license to treat every similarly named navigation container as an article.
    Never rewrite stored HTML or infer publication/version from this selection.
    """
    for node in soup(["script", "style", "nav", "header", "footer"]):
        node.decompose()
    parsed = urlparse(url)
    if (parsed.hostname == "paper.cnstock.com"
            and re.search(r"/content_\d+\.htm$", parsed.path)):
        articles = soup.select(".article-content")
        if len(articles) == 1:
            return articles[0], "paper.cnstock.com:.article-content"
    for selector in ("article", '[itemprop~="articleBody"]', "main"):
        nodes = soup.select(selector)
        if len(nodes) == 1:
            return nodes[0], selector
    main = soup.find(id=re.compile(r"(content|zoom|UCAP-CONTENT)$", re.IGNORECASE))
    if main is not None:
        return main, "legacy_document_id"
    return soup.body, "body_fallback"


class ResearchArchiveCollector:
    """Bounded public-source collector; transport/search are injectable in tests."""

    def __init__(
        self,
        *,
        store: ResearchDocumentStore,
        http=None,
        search=None,
        clock=None,
        browser_adapter=None,
    ):
        self.store = store
        self.http = http or requests.Session()
        self.search = search
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._cninfo_org_ids: dict[str, str] | None = None
        self.browser_adapter = browser_adapter

    def collect(
        self,
        *,
        dimension: str,
        symbol: str,
        subject: str,
        cutoff_at: datetime,
        subject_terms: tuple[str, ...] = (),
        cancellation=None,
        limit: int = 2,
        requested_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Acquire a bounded document inventory, not two native Agent findings.

        ``limit`` caps storage/work; it is not a minimum number of URLs. The
        subsequent explicit research Agent must bind distinct findings and
        propose raw revisions. This acquisition function does neither.
        ``subject_terms`` must come from the caller's cutoff-valid context.
        """
        if dimension not in RESEARCH_SOURCES:
            return {"evidence": [], "attempts": [], "status": "unsupported_source"}
        deadline = monotonic() + 120
        candidates: list[ArchiveCandidate] = []
        candidate_groups: list[list[ArchiveCandidate]] = []
        attempts: list[dict[str, Any]] = []
        discovery = []
        if dimension in {"7_industry", "15_events", "8_materials", "9_futures"}:
            discovery.append(
                (
                    "cninfo",
                    lambda: self._cninfo_candidates(
                        symbol, cutoff_at, dimension=dimension,
                        cancellation=cancellation, deadline=deadline,
                    ),
                )
            )
        if dimension == "6_research":
            discovery.append(
                (
                    "eastmoney_research",
                    lambda: self._research_candidates(symbol, cutoff_at),
                )
            )
        discovery.append(
            (
                "official_archive_search",
                lambda: self._official_candidates(
                    dimension, subject, cutoff_at, subject_terms=subject_terms
                ),
            )
        )
        if requested_urls:
            # A report URL is only a lead. In particular a PDF path/date is
            # not proof of its publication/version: preserve independently
            # discovered publisher metadata if the exact URL can be resolved.
            from research_system.market_data.research_requests import ResearchDocumentRequest
            if len(requested_urls) > _RESEARCH_CANDIDATE_LIMIT:
                raise ArchiveEvidenceError("archive_candidate_limit", "定向原文请求超过上限")
            requested_urls = list(dict.fromkeys(ResearchDocumentRequest(dimension=dimension, url=url).url for url in requested_urls))
        for source, discover in discovery:
            if (
                cancellation is not None and cancellation.is_set()
            ) or monotonic() >= deadline:
                break
            try:
                rows = discover()
                candidates.extend(rows)
                candidate_groups.append(rows)
                attempts.append(
                    {
                        "source": source,
                        "stage": "discovery",
                        "status": "completed",
                        "candidate_count": len(rows),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - source failures are isolated and disclosed
                attempts.append(
                    {
                        "source": source,
                        "stage": "discovery",
                        "status": "failed",
                        **_error_details(exc),
                        "retryable": True,
                    }
                )

        evidence: list[EvidenceInput] = []
        if requested_urls:
            discovered = {row.url: row for row in candidates}
            candidates = [discovered.get(url, ArchiveCandidate(url=url)) for url in requested_urls]
            candidate_groups = [candidates]
        linked: dict[str, list[ArchiveCandidate]] = {}

        def with_attachments(rows):
            for candidate in rows[:12]:
                yield candidate
                # Only an already archived publisher page may prove its
                # identified attachments. Never follow arbitrary page links.
                if not requested_urls:
                    yield from linked.pop(candidate.url, [])[:2]

        def candidate_stream():
            # Yield first, so the consumer validates cheap HTTP discoveries
            # and can stop once enough originals exist. Browser discovery is
            # reached only after those candidates fail to satisfy the target.
            # One directory must not consume every document slot before an
            # already discovered fallback source is attempted. Keep the same
            # twelve-candidate cap and prefer each source's first candidate.
            selected = _fair_research_candidates(candidate_groups) if len(candidate_groups) > 1 else candidates
            yield from with_attachments(selected)
            if (
                self.browser_adapter is not None
                and not requested_urls
                and not evidence
                and not (cancellation is not None and cancellation.is_set())
                and monotonic() < deadline
            ):
                try:
                    rows, trace = self._browser_candidates(dimension, symbol, subject)
                    attempts.extend(trace)
                    yield from with_attachments(rows)
                except Exception as exc:  # noqa: BLE001 - controlled optional source remains isolated
                    attempts.append(
                        {
                            "source": "controlled_browser",
                            "stage": "discovery",
                            "status": "failed",
                            **_error_details(exc),
                        }
                    )

        seen = set()
        hashes = set()
        research_attempts = 0
        for candidate in candidate_stream():
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            if (
                cancellation is not None and cancellation.is_set()
            ) or monotonic() >= deadline:
                break
            if dimension == "6_research":
                # Verified attachment candidates share the same total budget;
                # round-robin must not become extra requests or a new retry.
                if research_attempts >= _RESEARCH_CANDIDATE_LIMIT:
                    break
                research_attempts += 1
            try:
                item = self.archive(
                    candidate,
                    dimension=dimension,
                    cutoff_at=cutoff_at,
                    symbol=symbol,
                    subject=subject,
                    subject_terms=subject_terms,
                    cancellation=cancellation,
                    deadline=deadline,
                )
                duplicate = item.content_hash in hashes
                if not duplicate:
                    evidence.append(item)
                    hashes.add(item.content_hash)
                linked[candidate.url] = self._article_attachments(item, dimension)
                attempts.append(
                    {
                        "url": candidate.url,
                        "stage": "document",
                        "status": "reused_content" if duplicate else "archived",
                        "content_hash": item.content_hash,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - preserve per-source diagnostics
                attempts.append(
                    {
                        "url": urlparse(candidate.url)._replace(query="", fragment="").geturl()
                        if isinstance(exc, _ArchiveBodyUnusable) else candidate.url,
                        "stage": "document",
                        "status": "rejected",
                        **_error_details(exc),
                        "retryable": not isinstance(exc, ArchiveEvidenceError),
                    }
                )
            if len(evidence) >= max(1, limit):
                break
        inventory_ready = bool(evidence)
        if requested_urls:
            archived_urls = {attempt.get("url") for attempt in attempts
                             if attempt.get("status") in {"archived", "reused_content"}}
            inventory_ready = set(requested_urls).issubset(archived_urls)
        return {
            "evidence": evidence,
            "attempts": attempts,
            # Compatibility status denotes inventory only, never completion
            # of the SKILL's qualitative evidence/finding requirements.
            "status": "ready" if inventory_ready else "evidence_gap",
            **({"requested_urls": requested_urls} if requested_urls else {}),
            "document_inventory": {
                "status": "available" if evidence else "missing",
                "document_count": len(evidence),
            },
            "research_completion": "not_assessed",
            "raw_enrichment": "not_run",
            "unrecoverable": False,  # discovery misses do not prove universal absence
        }

    def archive(
        self,
        candidate: ArchiveCandidate,
        *,
        dimension: str,
        cutoff_at: datetime,
        symbol: str = "",
        subject: str = "",
        subject_terms: tuple[str, ...] = (),
        cancellation=None,
        deadline: float | None = None,
    ) -> EvidenceInput:
        observed = self.clock().astimezone(timezone.utc)
        _check_url(candidate.url, dimension)
        _check_document_url(candidate.url)
        deadline = min(deadline or float("inf"), monotonic() + 35)
        response = self.http.get(
            candidate.url, timeout=(10, 25), stream=True, allow_redirects=False
        )
        try:
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise ArchiveEvidenceError(
                    "archive_redirect_unverified", "原文重定向目标尚未核验"
                )
        except Exception:
            response.close()
            raise
        chunks, size = [], 0
        try:
            for chunk in response.iter_content(chunk_size=65536):
                if (
                    cancellation is not None and cancellation.is_set()
                ) or monotonic() >= deadline:
                    raise ArchiveEvidenceError(
                        "archive_cancelled_or_deadline", "原文采集已取消或达到时限"
                    )
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise ArchiveEvidenceError(
                        "archive_document_too_large", "原文超过归档大小限制"
                    )
                chunks.append(chunk)
        finally:
            response.close()
        content = b"".join(chunks)
        # Visibility is proven only once the complete response was observed,
        # not when a request that might cross the cutoff was started.
        observed = self.clock().astimezone(timezone.utc)
        published = candidate.published_at
        document_id = candidate.publisher_document_id
        modified = None
        if content.startswith(b"%PDF"):
            try:
                reader = PdfReader(BytesIO(content), strict=False)
                text, extraction = extract_pdf_text(reader, cancellation=cancellation, deadline=deadline)
            except ResearchTextError as exc:
                raise ArchiveEvidenceError(exc.code, exc.code) from None
            except Exception:  # noqa: BLE001 - PDF backends expose heterogeneous errors; return only a static reason
                raise ArchiveEvidenceError("archive_text_extraction_failed", "archive_text_extraction_failed") from None
            suffix, mime = "pdf", "application/pdf"
            version_proven = bool(
                candidate.immutable_attachment
                and document_id
                and candidate.listing_url
                and candidate.listing_hash
                and published is not None
            )
            proof_basis = candidate.attachment_proof_basis or "publisher_document_id_and_dated_attachment"
        else:
            soup = BeautifulSoup(content, "html.parser")
            main, body_selector = _html_document_body(soup, candidate.url)
            if main is not None and main.name != "article" and len(main.find_all("a", href=True)) > 8:
                body_length = len(main.get_text(" ", strip=True))
                linked_length = sum(
                    len(a.get_text(" ", strip=True))
                    for a in main.find_all("a", href=True)
                )
                if linked_length > max(1, body_length) * 0.55:
                    raise ArchiveEvidenceError(
                        "archive_listing_not_document", "列表/导航页不能充当报告原文"
                    )
            meta = {
                str(row.get("name") or row.get("property") or "").lower(): str(
                    row.get("content") or ""
                )
                for row in soup.find_all("meta")
            }
            published = (
                _metadata_time(
                    meta,
                    (
                        "article:published_time",
                        "pubdate",
                        "publishdate",
                        "publication_date",
                        "firstpublishedtime",
                    ),
                )
                or published
            )
            modified = _metadata_time(
                meta,
                (
                    "article:modified_time",
                    "lastmodifiedtime",
                    "last-modified",
                    "modified_date",
                ),
            )
            document_id = (
                meta.get("articleid")
                or meta.get("contentid")
                or meta.get("documentid")
                or document_id
            )
            text = main.get_text(" ", strip=True) if main else ""
            extraction = {"extractor": "beautifulsoup", "page_count": None, "pages_processed": None,
                          "pages_without_text": [], "html_body_selector": body_selector}
            title = soup.title.get_text(strip=True) if soup.title else ""
            if not candidate.title and title:
                candidate = ArchiveCandidate(**{**candidate.__dict__, "title": title})
            suffix, mime = "html", "text/html"
            # An old date on a current URL is insufficient. For HTML archives
            # require an explicit publisher document identity AND version time.
            version_proven = bool(
                document_id and published and modified and modified <= cutoff_at
            )
            proof_basis = "publisher_document_id_and_declared_version_time"
        # Keep the existing 80-character and first-500-character checks exact.
        # Report every matching cause, without retaining any source text.
        body_reasons = ["text_too_short"] if len(text.strip()) < 80 else []
        body_reasons.extend(
            reason for marker, reason in (
                ("access denied", "access_denied"), ("captcha", "captcha"),
                ("just a moment", "challenge"),
            ) if marker in text[:500].lower()
        )
        if body_reasons:
            raise _ArchiveBodyUnusable(
                response=response, content=content, text=text,
                extraction=extraction, reasons=body_reasons,
            )
        publication_metadata = {}
        if published is None:
            if observed > cutoff_at:
                # A download today cannot establish when this undated version
                # first became public before a historical cutoff.
                raise ArchiveEvidenceError(
                    "archive_publication_unknown", "无法核验原文首次发布时间"
                )
            # The legacy non-null published_at storage column remains a
            # conservative PIT availability bound. It is NOT a publisher
            # release date; all research-facing projections must carry this
            # distinction and expose the unknown release date as null.
            published = observed
            publication_metadata = {
                "publication_time_basis": "observed_upper_bound",
                "publisher_published_at": None,
                "visible_by": observed.isoformat(),
            }
        relevance = _relevance_basis(
            text, candidate, dimension=dimension, symbol=symbol, subject=subject,
            subject_terms=subject_terms,
        )
        if (
            published > cutoff_at
            or published > observed
            or (modified is not None and modified > cutoff_at)
        ):
            raise ArchiveEvidenceError(
                "archive_after_cutoff", "原文或修订版本晚于目标截止时间"
            )
        reconstruction = observed > cutoff_at
        if reconstruction and not version_proven:
            raise ArchiveEvidenceError(
                "historical_version_unproven", "仅有历史 URL/日期，无法证明当时正文版本"
            )
        digest, ref = self.store.put(content, suffix=suffix)
        # The database excerpt stays bounded. A new snapshot must explicitly
        # bind this separate immutable full-text file before it is materialized.
        try:
            excerpt, text_binding = archive_text(self.store, text, source_hash=digest, extraction=extraction)
        except ResearchTextError as exc:
            raise ArchiveEvidenceError(exc.code, exc.code) from None
        return EvidenceInput(
            provider=urlparse(candidate.url).hostname or "official_archive",
            source_url=candidate.url,
            source_kind="research_document",
            title=candidate.title[:512] or document_id,
            mime_type=mime,
            content_hash=digest,
            content_ref=ref,
            inline_content=excerpt,
            published_at=published,
            observed_at=observed,
            grade=EvidenceGrade.B if reconstruction else EvidenceGrade.A,
            immutable_proof=True,
            metadata={
                "archive_version": ARCHIVE_VERSION,
                "text_extraction": text_binding,
                "dimension": dimension,
                "evidence_mode": "public_archive_reconstruction"
                if reconstruction
                else "contemporaneous_archive",
                "proof_basis": proof_basis
                if reconstruction
                else "captured_before_cutoff",
                "publisher_document_id": document_id,
                "listing_url": candidate.listing_url,
                "listing_hash": candidate.listing_hash,
                "publisher_modified_at": modified.isoformat() if modified else None,
                "field_paths": ["$research"],
                "purpose": "research_context",
                "research_completion": "not_assessed",
                "relevance": relevance,
                **publication_metadata,
            },
        )

    def _article_attachments(
        self, document: EvidenceInput, dimension: str
    ) -> list[ArchiveCandidate]:
        """Discover an identified PDF through a validated publisher article.

        An arbitrary PDF URL/date is not proof. Require the archived article's
        identity/version and a same-publisher, non-mutable attachment identity;
        save the exact linking page hash with the PDF. Unknown/latest links are
        deliberately not promoted to historical originals.
        """
        metadata = document.metadata
        if (
            document.mime_type != "text/html"
            or not metadata.get("publisher_document_id")
            or not metadata.get("publisher_modified_at")
        ):
            return []
        content = Path(document.content_ref).read_bytes()
        if hashlib.sha256(content).hexdigest() != document.content_hash:
            raise ArchiveEvidenceError("archive_content_corrupt", "已归档发布页 hash 不匹配")
        soup = BeautifulSoup(content, "html.parser")
        main, _body_selector = _html_document_body(soup, document.source_url)
        rows = []
        for anchor in main.find_all("a", href=True) if main else []:
            url = urljoin(document.source_url, anchor["href"])
            parsed = urlparse(url)
            if parsed.hostname != urlparse(document.source_url).hostname:
                continue
            # Timestamp/ID-named attachments on versioned publisher pages are
            # distinct objects; latest.pdf/report.pdf cannot carry that proof.
            identity = re.fullmatch(
                r"(?:P|W)?(\d{10,})\.pdf", Path(parsed.path).name, re.IGNORECASE
            )
            if not identity or parsed.query or parsed.fragment:
                continue
            try:
                _check_url(url, dimension)
            except ArchiveEvidenceError:
                continue
            rows.append(
                ArchiveCandidate(
                    url=url,
                    title=anchor.get_text(" ", strip=True) or document.title,
                    published_at=document.published_at,
                    publisher_document_id=identity.group(1),
                    listing_url=document.source_url,
                    listing_hash=document.content_hash,
                    immutable_attachment=True,
                    security_code=metadata.get("relevance", {}).get("security_code"),
                    attachment_proof_basis="publisher_versioned_article_and_identified_attachment",
                )
            )
        return rows

    def _cninfo_candidates(
        self, symbol: str, cutoff_at: datetime, *, dimension: str,
        cancellation=None, deadline: float | None = None,
    ) -> list[ArchiveCandidate]:
        code = "".join(c for c in symbol if c.isdigit())[-6:]
        end = cutoff_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if self._cninfo_org_ids is None:
            identity_response = self.http.get(
                "http://www.cninfo.com.cn/new/data/szse_stock.json", timeout=(10, 20)
            )
            identity_response.raise_for_status()
            self._cninfo_org_ids = {
                str(row["code"]): str(row["orgId"])
                for row in identity_response.json().get("stockList") or []
                if row.get("code") and row.get("orgId")
            }
            identity_response.close()
        org_id = self._cninfo_org_ids.get(code)
        if not org_id:
            raise ArchiveEvidenceError(
                "archive_security_identity_missing", "未找到该股票的巨潮公告主体 ID"
            )
        searches = (
            ("年度报告", "ESG", "社会责任")
            if dimension in {"8_materials", "9_futures"}
            else ("年度报告",) if dimension == "7_industry" else ("",)
        )
        rows: dict[str, ArchiveCandidate] = {}
        for keyword in searches:
            if (cancellation is not None and cancellation.is_set()) or (
                deadline is not None and monotonic() >= deadline
            ):
                break
            for candidate in self._cninfo_listing_candidates(
                code=code, org_id=org_id, end=end, cutoff_at=cutoff_at,
                dimension=dimension, keyword=keyword,
            ):
                rows.setdefault(candidate.url, candidate)
        return list(rows.values())

    def _cninfo_listing_candidates(
        self, *, code: str, org_id: str, end: date, cutoff_at: datetime,
        dimension: str, keyword: str,
    ) -> list[ArchiveCandidate]:
        """Keep the publisher's actual target-stock listing as attachment proof."""
        response = self.http.post(
            "http://www.cninfo.com.cn/new/hisAnnouncement/query",
            data={
                "pageNum": 1,
                "pageSize": 30,
                "tabName": "fulltext",
                "column": "szse",
                "stock": f"{code},{org_id}",
                "searchkey": keyword,
                "seDate": f"{(end - timedelta(days=365)).isoformat()}~{end.isoformat()}",
            },
            timeout=(10, 20),
        )
        response.raise_for_status()
        payload = response.json()
        response.close()
        listing_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        self.store.put(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(),
            suffix="json",
        )
        rows = []
        for item in payload.get("announcements") or []:
            if (
                str(item.get("secCode") or "") != code
                or str(item.get("orgId") or "") != org_id
            ):
                continue
            stamp = item.get("announcementTime")
            path = str(item.get("adjunctUrl") or "")
            doc_id = str(item.get("announcementId") or "")
            if not stamp or not path.lower().endswith(".pdf") or not doc_id:
                continue
            published = datetime.fromtimestamp(float(stamp) / 1000, tz=timezone.utc)
            title = str(item.get("announcementTitle") or "")
            if dimension in {"7_industry", "8_materials", "9_futures"} and not any(
                label in title
                for label in (
                    "年度报告", "半年度报告", "招股说明书", "ESG", "环境",
                    "社会责任", "可持续发展", "套期保值", "衍生品",
                )
            ):
                continue
            if published <= cutoff_at:
                rows.append(
                    ArchiveCandidate(
                        urljoin("http://static.cninfo.com.cn/", path),
                        title,
                        published,
                        doc_id,
                        "http://www.cninfo.com.cn/new/hisAnnouncement/query",
                        listing_hash,
                        True,
                        code,
                    )
                )
        return rows

    def _research_candidates(
        self, symbol: str, cutoff_at: datetime
    ) -> list[ArchiveCandidate]:
        # Use the same endpoint as the installed AkShare adapter, but preserve
        # infoCode and query the target period. The generic adapter discards
        # that ID and fetches its whole current list before applying head(50).
        code = "".join(c for c in symbol if c.isdigit())[-6:]
        end = cutoff_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        listing_url = "https://reportapi.eastmoney.com/report/list"
        response = self.http.get(
            listing_url,
            params={
                "industryCode": "*",
                "industry": "*",
                "rating": "*",
                "ratingChange": "*",
                "beginTime": (end - timedelta(days=365)).isoformat(),
                "endTime": end.isoformat(),
                "pageSize": "50",
                "pageNo": "1",
                "qType": "0",
                "code": code,
            },
            timeout=(10, 20),
        )
        response.raise_for_status()
        payload = response.json()
        response.close()
        listing_hash, _ref = self.store.put(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(),
            suffix="json",
        )
        result = []
        for row in (payload.get("data") or [])[:50]:
            doc_id = str(row.get("infoCode") or "")
            published = _parse_time(row.get("publishDate"))
            if (
                not doc_id
                or not re.fullmatch(r"[A-Za-z0-9_-]+", doc_id)
                or not published
                or published > cutoff_at
            ):
                continue
            if str(row.get("stockCode") or "") != code:
                continue
            result.append(
                ArchiveCandidate(
                    f"https://pdf.dfcfw.com/pdf/H3_{doc_id}_1.pdf",
                    str(row.get("title") or ""),
                    published,
                    doc_id,
                    listing_url,
                    listing_hash,
                    True,
                    code,
                )
            )
        return result

    def _official_candidates(
        self, dimension: str, subject: str, cutoff_at: datetime,
        *, subject_terms: tuple[str, ...] = (),
    ) -> list[ArchiveCandidate]:
        domains = " OR ".join(
            f"site:{domain}" for domain in RESEARCH_SOURCES[dimension]
        )
        # Macro is market-wide; an individual ticker would hide valid releases.
        topic = "" if dimension == "3_macro" else " OR ".join(dict.fromkeys([subject, *subject_terms]))
        if topic:
            topic = f"({topic})"
        query = f"({domains}) {topic} {_KEYWORDS[dimension]} before:{(cutoff_at.date() + timedelta(days=1)).isoformat()}"
        if self.search is not None:
            payload = self.search(
                query=query,
                dimension=dimension.split("_", 1)[1],
                max_results=8,
                trusted=False,
            )
        else:
            # The general adapter abandons a daemon after its soft timeout.
            # This bounded acquisition stage must drain actual calls before
            # becoming terminal, so use the same client synchronously.
            from ddgs import DDGS

            with DDGS(timeout=8) as client:
                payload = {
                    "results": list(
                        client.text(
                            query, region="cn-zh", safesearch="off", max_results=8
                        )
                    )
                }
        candidates = []
        for row in payload.get("results") or []:
            url = str(row.get("url") or row.get("href") or "")
            try:
                _check_url(url, dimension)
            except ArchiveEvidenceError:
                continue
            candidates.append(
                ArchiveCandidate(url=url, title=str(row.get("title") or ""))
            )
        return candidates

    def _browser_candidates(self, dimension: str, symbol: str, subject: str):
        """Reuse existing opt-in browser permissions for link discovery only.

        Its short page excerpts lack document-version proof. They never become
        evidence; selected links still pass the same original-body/PIT checks.
        """
        strategies = {
            "3_macro": ("stats_gov", "pbc_macro"),
            "13_policy": ("gov_policy", "miit_policy"),
            "7_industry": ("stats_gov", "miit_policy"),
            "15_events": ("cninfo",),
            "8_materials": ("shfe",),
            "9_futures": ("shfe",),
        }.get(dimension, ())
        if not strategies:
            return [], []
        output = self.browser_adapter.collect_many(
            source_ids=strategies, symbol=symbol, query=subject
        )
        candidates = []
        for item in dict(output.get("evidence") or {}).values():
            for link in item.get("links") or []:
                url = str(link.get("url") or "")
                try:
                    _check_url(url, dimension)
                    _check_document_url(url)
                except ArchiveEvidenceError:
                    continue
                candidates.append(
                    ArchiveCandidate(url=url, title=str(link.get("text") or ""))
                )
        return candidates, [
            {**row, "stage": "browser_discovery"}
            for row in output.get("source_trace") or []
        ]


def _check_document_url(url: str) -> None:
    parsed = urlparse(url)
    route = (parsed.path + "#" + parsed.fragment).lower()
    # PBC publishes actual articles at /<long document ID>/index.html. This
    # only permits fetching the page; body, publication and PIT checks below
    # must still establish its identity. Ordinary /release/index.html stays a
    # listing and a date-shaped route alone never proves historical content.
    identified_article = bool(
        (parsed.hostname or "").endswith(".pbc.gov.cn")
        and re.search(r"/\d{10,}/index\.html$", parsed.path, re.IGNORECASE)
        and not parsed.fragment
    )
    if (
        parsed.path in {"", "/"}
        or parsed.path.endswith("/")
        or (not identified_article and re.search(
            r"(?:^|/)(?:index|list|search|apidoc|api-doc)(?:[./#?]|$)|#/apidoc", route
        ))
    ):
        raise ArchiveEvidenceError(
            "archive_listing_not_document", "首页、列表、搜索/API 文档页不是报告原文"
        )


def _research_subject_term(value: Any) -> str | None:
    """Filter exact fallback labels, not real industries containing those words.

    Native fetchers substitute 综合 when company industry is missing. It is
    retained in raw snapshots, but is not evidence of a document's subject.
    """
    if not isinstance(value, str):
        return None
    term = value.strip()
    return term if term.casefold() not in {"", "综合", "未知", "unknown", "—"} else None


def _relevance_basis(
    text: str, candidate: ArchiveCandidate, *, dimension: str, symbol: str, subject: str,
    subject_terms: tuple[str, ...] = (),
) -> dict[str, Any]:
    matched = [
        term for term in _RELEVANCE_TERMS[dimension] if term.lower() in text.lower()
    ]
    if not matched:
        raise ArchiveEvidenceError(
            "archive_dimension_unrelated", "原文正文没有该研究维度的相关事实主题"
        )
    code = "".join(c for c in symbol if c.isdigit())[-6:]
    terms = list(dict.fromkeys(
        term for value in [subject, *subject_terms]
        if (term := _research_subject_term(value)) is not None
    ))
    matched_subjects = [term for term in terms if term in text]
    identity = None
    company_document = (urlparse(candidate.url).hostname or "").endswith(".cninfo.com.cn")
    if symbol and (dimension in {"6_research", "7_industry", "15_events"} or company_document):
        if candidate.security_code and candidate.security_code != code:
            raise ArchiveEvidenceError("archive_subject_unrelated", "发布机构声明的文档股票主体与目标不符")
        if candidate.security_code == code and candidate.listing_hash:
            identity = "publisher_listing_security_match"
        elif code and code in text:
            identity = "body_security_code_match"
        elif not company_document and matched_subjects:
            identity = "body_subject_match"
        else:
            raise ArchiveEvidenceError(
                "archive_subject_unrelated", "未能证明原文属于目标股票或其行业"
            )
    elif (subject or subject_terms) and dimension in {"13_policy", "8_materials", "9_futures"}:
        # A requested but unknown subject must not silently become unscoped.
        # Truly unscoped callers and market-wide macro keep their old rules.
        if not matched_subjects:
            raise ArchiveEvidenceError(
                "archive_subject_unrelated", "原文没有目标行业/原材料/合约的相关性依据"
            )
        identity = "body_subject_match"
    return {
        "dimension": dimension,
        "matched_body_terms": matched,
        "subject_basis": identity or "market_wide_macro",
        "subject": subject or None,
        "matched_subject_terms": matched_subjects,
        "security_code": candidate.security_code,
    }


def _error_details(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, _ArchiveBodyUnusable):
        # This branch never serializes arbitrary exception text, headers or URL.
        return {
            "reason": "archive_body_unusable", "message": "未取得可引用原文正文",
            "http_status": exc.body_diagnostics["http_status"],
            "body_diagnostics": dict(exc.body_diagnostics),
        }
    message = " ".join(str(exc).split())
    message = re.sub(
        r"(?i)(api[_-]?key|token|authorization|cookie|password)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[redacted]",
        message,
    )
    response = getattr(exc, "response", None)
    return {
        "reason": getattr(exc, "code", type(exc).__name__),
        "message": message[:320],
        "http_status": getattr(response, "status_code", None),
    }


def _check_url(url: str, dimension: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 80, 443}
    ):
        raise ArchiveEvidenceError(
            "archive_url_not_allowed", "原文地址不是已支持公开来源"
        )
    if not any(
        host == root or host.endswith("." + root)
        for root in RESEARCH_SOURCES.get(dimension, ())
    ):
        raise ArchiveEvidenceError(
            "archive_url_not_allowed", "原文不属于该维度支持的档案来源"
        )


def _metadata_time(metadata: dict[str, str], keys: tuple[str, ...]) -> datetime | None:
    return next(
        (parsed for key in keys if (parsed := _parse_time(metadata.get(key)))), None
    )


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("/", "-")
    try:
        if len(text) == 10:
            # Date-only publication proves EOD visibility, not an intraday time.
            return datetime.combine(
                date.fromisoformat(text), time.max, ZoneInfo("Asia/Shanghai")
            ).astimezone(timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            if parsed.tzinfo is None
            else parsed
        ).astimezone(timezone.utc)
    except ValueError:
        return None
