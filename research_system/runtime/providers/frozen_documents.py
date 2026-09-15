"""Content-addressed, bounded reads of caller-supplied evidence. No I/O authority.

The caller must supply only its already verified Manifest materialization.
This class cannot resolve paths, URLs, database IDs, or retrieve newer data.
"""
from __future__ import annotations

import hashlib
import json
import re
from threading import Lock

VERSION = "frozen-reader-v1"
READ_CHARACTERS = 6000


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FrozenDocuments:
    """Immutable bytes shared by groups; read journals belong to each session."""

    def __init__(self, entries: list[tuple[str, object]], *, input_hash: str):
        bodies, labels = {}, {}
        for label, value in entries:
            body = value if isinstance(value, str) else encoded(value)
            key = digest(body)
            bodies[key] = body
            labels.setdefault(key, []).append(label)
        self._bodies = bodies
        self._catalog = [{"id": key, "sha256": key, "characters": len(body),
                          "labels": sorted(set(labels[key]))} for key, body in sorted(bodies.items())]
        self.identity = {"version": VERSION, "input_hash": input_hash,
                         "catalog_hash": digest(encoded(self._catalog)), "documents": len(bodies)}

    def session(self):
        return FrozenReadSession(self)


class FrozenReadSession:
    def __init__(self, corpus: FrozenDocuments):
        self.corpus = corpus
        self._records = []
        self._lock = Lock()

    @property
    def tools(self):
        def spec(name, description, properties):
            return {"type": "function", "name": name, "description": description,
                    "inputSchema": {"type": "object", "properties": properties,
                                    "required": list(properties), "additionalProperties": False}}
        return [
            spec("frozen_list", "List fixed documents (8 per page); labels identify snapshots, raw text and source bindings. Never implies read coverage.",
                 {"offset": {"type": "integer", "minimum": 0}}),
            spec("frozen_search", "Literal text search inside these fixed documents. Empty document_id searches all; at most 12 excerpts. Paginate with offset. No matches is not proof a fact is absent.",
                 {"query": {"type": "string", "minLength": 1, "maxLength": 100},
                  "document_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}}),
            spec("frozen_read", "Read exact character range of a fixed document (max 6000 chars). Follow next_offset for more; unread remainder stays available. Citation sources are in the document/bindings, not the document ID.",
                 {"document_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                  "length": {"type": "integer", "minimum": 1, "maximum": READ_CHARACTERS}}),
        ]

    def call(self, name: str, arguments: dict) -> dict:
        specs = {tool["name"]: tool for tool in self.tools}
        if name not in specs or not isinstance(arguments, dict):
            raise ValueError("frozen_tool_not_allowed")
        properties = specs[name]["inputSchema"]["properties"]
        if set(arguments) != set(properties):
            raise ValueError("frozen_tool_arguments_invalid")
        for key, spec in properties.items():
            value = arguments[key]
            expected = int if spec["type"] == "integer" else str
            if type(value) is not expected:
                raise ValueError("frozen_tool_arguments_invalid")
            if expected is int and not spec.get("minimum", 0) <= value <= spec.get("maximum", 10**9):
                raise ValueError("frozen_tool_range_invalid")
        offset = arguments["offset"]
        key = arguments.get("document_id", "")
        if key and key not in self.corpus._bodies:
            raise ValueError("frozen_document_not_found")
        spans = []
        if name == "frozen_list":
            items = [{**item, "labels": [label[:140] for label in item["labels"][:3]],
                      "label_count": len(item["labels"])} for item in self.corpus._catalog[offset:offset + 8]]
            result = {"documents": items, "next_offset": offset + len(items)
                      if offset + len(items) < len(self.corpus._catalog) else None}
        elif name == "frozen_read":
            if not key:
                raise ValueError("frozen_document_not_found")
            body = self.corpus._bodies[key]
            if offset > len(body):
                raise ValueError("frozen_tool_range_invalid")
            end = min(offset + arguments["length"], len(body))
            spans = [{"document_id": key, "start": offset, "end": end}]
            result = {"document_id": key, "sha256": key, "start": offset, "end": end,
                      "characters": len(body), "text": body[offset:end],
                      "next_offset": end if end < len(body) else None}
        else:
            query = arguments["query"]
            if not 1 <= len(query.strip()) <= 100:
                raise ValueError("frozen_tool_query_invalid")
            matches = []
            seen, more = 0, False
            pattern = re.compile(re.escape(query), re.IGNORECASE)
            for doc_id, body in self.corpus._bodies.items():
                if key and doc_id != key:
                    continue
                for match in pattern.finditer(body):
                    seen += 1
                    if seen <= offset:
                        continue
                    if len(matches) == 12:
                        more = True
                        break
                    start, end = max(0, match.start() - 100), min(len(body), match.end() + 220)
                    matches.append({"document_id": doc_id, "sha256": doc_id, "start": start,
                                    "end": end, "text": body[start:end]})
                    spans.append({"document_id": doc_id, "start": start, "end": end})
                if more:
                    break
            result = {"matches": matches, "next_offset": offset + len(matches) if more else None,
                      "scope": "fixed_materialized_text_only_not_proof_of_absence"}
        result["corpus_hash"] = self.corpus.identity["catalog_hash"]
        # No text/query content in the audit trail. Response hash permits replay.
        with self._lock:
            self._records.append({"tool": name, "arguments_hash": digest(encoded(arguments)),
                                  "response_hash": digest(encoded(result)), "spans": spans})
        return result

    def audit(self):
        with self._lock:
            records = json.loads(encoded(self._records))
        intervals = {}
        for record in records:
            for span in record["spans"]:
                intervals.setdefault(span["document_id"], []).append((span["start"], span["end"]))
        coverage = []
        for key, spans in sorted(intervals.items()):
            merged = []
            for start, end in sorted(spans):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(end, merged[-1][1])
                else:
                    merged.append([start, end])
            count = sum(end - start for start, end in merged)
            coverage.append({"document_id": key, "ranges": merged, "read_characters": count,
                             "characters": len(self.corpus._bodies[key]),
                             "materialized_text_fully_read": count == len(self.corpus._bodies[key])})
        return {**self.corpus.identity, "tool_calls": len(records), "calls": records, "coverage": coverage,
                "original_document_fully_read_claimed": False}
