# SPDX-License-Identifier: Apache-2.0
"""Reading an Elasticsearch server the person runs: search with chosen fields and filters, one document, a count, and
the mapping. Nothing here writes to the server, administers an index or runs a query a caller or a model wrote.

The server is a declared outside service (:data:`ELASTICSEARCH`): plain HTTP on this machine, at
``http://127.0.0.1:9200`` or the local address ``G6_ELASTIC_URL`` names, reached only through
``g6.core.outside.call_service`` (a time limit for the whole call, a cap on the reply, no redirect followed). G6 never
starts, installs or bundles it. A server with security on gets the API key the person put in their key file
(:data:`ELASTIC_KEY`), sent to that server only.

The query G6 sends is built here from typed parts, never taken as JSON from the caller: a ``multi_match`` of the
caller's words over the chosen fields, and an exact ``term`` filter for each filter given. Every field is checked
against the index's mapping first, because Elasticsearch answers a query on a field it does not hold with no hits
rather than an error: a field that is not in the mapping, a ``term`` filter on an analysed ``text`` field (which
matches only a single lower-cased token) and a field inside a ``nested`` object (which needs a nested query) are
each refused with what the mapping does hold. A search whose shards did not all answer, or that timed out, is said to
be partial; a count whose shards did not all answer is refused (``G6_E_KNOWLEDGE_ELASTIC_COUNT_PARTIAL``), since it
counts only the shards that answered. Hits come back as the family's passages: the chosen fields' text from each hit's ``_source``, with the
document's address on the server as its source and the hash of its ``_source`` as what was read. Their scores are
``es_bm25``: Elasticsearch's ``_score`` for that one query.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import quote

from g6.core import keys, outside
from g6.knowledge._index import (READ_ONLY, Added, Hit, IndexBackend, IndexRefused, Removed, Retrieves, Searched,
                                 index_backends)
from g6.knowledge._types import Document, Passage, Source, now, sha256_hex

SCALE = "es_bm25"
#: The Elasticsearch server the person runs, declared so G6 can say how to get it and find it at call time.
ELASTICSEARCH = outside.OutsideProgram(
    name="Elasticsearch",
    purpose="the search server whose indexes ctx_elastic reads",
    install="run Elasticsearch yourself on this machine (https://www.elastic.co/downloads/elasticsearch, or its "
            "start-local script), its HTTP on plain http at 127.0.0.1:9200; set G6_ELASTIC_URL for another local "
            "port, or tunnel a remote server to one",
    licence="Elasticsearch's own (Elastic License 2.0, SSPL or AGPL-3.0, as Elastic publishes it; not checked "
            "here): a separate program G6 talks to over HTTP, never imports or bundles",
    url="http://127.0.0.1:9200",
    env="G6_ELASTIC_URL",
)
#: The API key of a server with security on; it goes only to that server, and none is sent when the file holds none.
ELASTIC_KEY = keys.Key("G6_ELASTIC_API_KEY", "the API key of the Elasticsearch you run, sent only to it "
                       "(ctx_elastic); leave it out for a server without security",
                       where="your server: POST /_security/api_key, or the key its start-local script prints")
#: How long one request to the server may take, and the most of a reply read.
TIMEOUT = 30.0
MAX_REPLY = 16 * 1024 * 1024
INDEX = re.compile(r"[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9_-]+)*")
FIELD = re.compile(r"[A-Za-z0-9_@][A-Za-z0-9_@-]*(?:\.[A-Za-z0-9_@-]+)*")
#: The field types full text runs on.
TEXT_TYPES = frozenset({"text", "match_only_text", "search_as_you_type"})

UNREACHABLE = outside.OUTSIDE_PROGRAM_MISSING
DENIED = "G6_E_KNOWLEDGE_ELASTIC_DENIED"
NOT_FOUND = "G6_E_KNOWLEDGE_ELASTIC_NOT_FOUND"
REJECTED = "G6_E_KNOWLEDGE_ELASTIC_REJECTED"
SERVER_ERROR = "G6_E_KNOWLEDGE_ELASTIC_SERVER_ERROR"
TIMED_OUT = "G6_E_KNOWLEDGE_ELASTIC_TIMEOUT"
REDIRECTED = "G6_E_KNOWLEDGE_ELASTIC_REDIRECTED"
PARSE_FAILED = "G6_E_KNOWLEDGE_ELASTIC_PARSE_FAILED"
FIELD_MISSING = "G6_E_KNOWLEDGE_FIELD_NOT_IN_MAPPING"
FILTER_ON_TEXT = "G6_E_KNOWLEDGE_FILTER_ON_TEXT"
NESTED = "G6_E_KNOWLEDGE_NESTED_FIELD"
NO_TEXT_FIELDS = "G6_E_KNOWLEDGE_NO_TEXT_FIELDS"
COUNT_PARTIAL = "G6_E_KNOWLEDGE_ELASTIC_COUNT_PARTIAL"
BAD_INPUT = "G6_E_KNOWLEDGE_ELASTIC_INPUT"

Caller = Callable[..., outside.ServiceReply]
Scalar = str | int | float | bool


class ElasticError(Exception):
    """A request the server did not answer usefully, or one G6 will not send, with its stable code."""

    def __init__(self, code: str, reason: str, *, status: int | None = None,
                 evidence: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {reason}")
        self.code, self.reason, self.status, self.evidence = code, reason, status, dict(evidence or {})


def check_index(name: str) -> str:
    """``name`` when it is one concrete index (lower case, no wildcard, list, path or leading dot); otherwise
    :class:`ElasticError`."""
    if not INDEX.fullmatch(name or "") or len(name) > 255:
        raise ElasticError(BAD_INPUT, f"{name!r} is not one index name (lower-case letters, digits, '-', '_' and "
                                      "inner dots; no wildcard, list, path, or hidden index starting with a dot)")
    return name


def check_field(name: str) -> str:
    if not FIELD.fullmatch(name or "") or len(name) > 255:
        raise ElasticError(BAD_INPUT, f"{name!r} is not a field name (letters, digits, '_', '@', '-' and inner dots; "
                                      "no wildcard)")
    return name


def _call(method: str, path: str, body: Mapping[str, Any] | None, caller: Caller | None) -> Any:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    key = keys.get(ELASTIC_KEY.name)
    if key:
        headers["Authorization"] = f"ApiKey {key}"
    send = caller or outside.call_service
    try:
        reply = send(ELASTICSEARCH, method, path, body=None if body is None else json.dumps(body).encode("utf-8"),
                     headers=headers, timeout=TIMEOUT, max_bytes=MAX_REPLY)
    except outside.ServiceError as exc:
        raise _failure(exc) from None
    try:
        return json.loads(reply.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ElasticError(PARSE_FAILED, f"the server's reply to {method} {path} is not JSON: {exc}") from None


def _failure(exc: outside.ServiceError) -> ElasticError:
    """The error a failed call stands for: nothing answering, a refusal, a missing index or document, a bad request,
    the server's own failure, a time limit or a redirect, each its own code and never an empty answer."""
    status, said = exc.status, exc.message
    if exc.code == outside.OUTSIDE_PROGRAM_MISSING:
        return ElasticError(UNREACHABLE, said)
    if exc.code == outside.SERVICE_TIMEOUT:
        return ElasticError(TIMED_OUT, said)
    if exc.code in (outside.SERVICE_NOT_LOCAL, outside.SERVICE_FAILED, outside.SERVICE_TOO_LARGE):
        return ElasticError(SERVER_ERROR if exc.code != outside.SERVICE_NOT_LOCAL else UNREACHABLE, said,
                            status=status)
    if status is not None and 300 <= status < 400:
        return ElasticError(REDIRECTED, f"{said}: the server redirected, and G6 does not follow a redirect",
                            status=status)
    if status in (401, 403):
        return ElasticError(DENIED, f"{said}: the server refused access; with security on, put its API key in your "
                                    f"key file as {ELASTIC_KEY.name}", status=status)
    if status == 404:
        return ElasticError(NOT_FOUND, said, status=status)
    if status in (400, 405, 406, 409, 413, 422):
        return ElasticError(REJECTED, said, status=status)
    return ElasticError(SERVER_ERROR, said, status=status)


def flatten(properties: Mapping[str, Any], prefix: str = "", nested: bool = False) -> dict[str, str]:
    """A mapping's fields as ``{dotted path: type}``: objects walked into, multi-fields (``title.keyword``) listed, and
    a field inside a ``nested`` object marked ``nested:<type>``."""
    out: dict[str, str] = {}
    for name, spec in properties.items():
        if not isinstance(spec, Mapping):
            continue
        path = f"{prefix}{name}"
        kind = str(spec.get("type", "object"))
        inner = nested or kind == "nested"
        if isinstance(spec.get("properties"), Mapping):
            out.update(flatten(spec["properties"], f"{path}.", inner))
            if "type" not in spec or kind in ("object", "nested"):
                continue
        out[path] = f"nested:{kind}" if nested else kind
        for sub, sub_spec in (spec.get("fields") or {}).items():
            if isinstance(sub_spec, Mapping):
                sub_kind = str(sub_spec.get("type", "object"))
                out[f"{path}.{sub}"] = f"nested:{sub_kind}" if nested else sub_kind
    return out


def mapping(index: str, *, caller: Caller | None = None) -> dict[str, str]:
    """The fields of ``index`` (or of every index an alias names, merged) and their types."""
    found = _call("GET", f"/{quote(check_index(index), safe='')}/_mapping", None, caller)
    if not isinstance(found, Mapping) or not found:
        raise ElasticError(PARSE_FAILED, f"the server's mapping of {index} is not an object of indexes")
    fields: dict[str, str] = {}
    for concrete, spec in found.items():
        props = (spec or {}).get("mappings", {}).get("properties") if isinstance(spec, Mapping) else None
        if props is None:
            raise ElasticError(PARSE_FAILED, f"the mapping of {concrete} has no properties")
        for name, kind in flatten(props).items():
            if fields.get(name, kind) != kind:
                kind = f"{fields[name]}|{kind}"  # two indexes behind an alias disagree: both types are kept
            fields[name] = kind
    return fields


def _query(text: str, fields: Sequence[str], filters: Mapping[str, Scalar]) -> dict[str, Any]:
    must: list[dict[str, Any]] = ([{"multi_match": {"query": text, "fields": list(fields)}}] if text.strip()
                                  else [{"match_all": {}}])
    terms = [{"term": {name: value}} for name, value in filters.items()]
    return {"bool": {"must": must, "filter": terms}}


def checked_fields(index: str, fields: Sequence[str], filters: Mapping[str, Scalar], *, text: bool,
                   caller: Caller | None) -> tuple[list[str], dict[str, str]]:
    """The fields to search (the ones given, or every text field of the mapping) and the mapping, each field and
    filter checked against it; :class:`ElasticError` naming what the mapping holds when one does not fit."""
    for name in (*fields, *filters):
        check_field(name)
    held = mapping(index, caller=caller)
    chosen = list(dict.fromkeys(fields)) or ([f for f, k in held.items() if k in TEXT_TYPES] if text else [])
    if text and not chosen:
        raise ElasticError(NO_TEXT_FIELDS, f"{index} has no text field to search ({_some(held)}); name the fields")
    missing = [f for f in (*chosen, *filters) if f not in held]
    if missing:
        raise ElasticError(FIELD_MISSING, f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not in the "
                                          f"mapping of {index}, so Elasticsearch would answer with no hits rather "
                                          f"than an error; it holds {_some(held)}",
                           evidence={"fields_held": held})
    deep = [f for f in (*chosen, *filters) if held.get(f, "").startswith("nested:")]
    if deep:
        raise ElasticError(NESTED, f"{', '.join(deep)} sit inside a nested object, which only a nested query "
                                   "reaches; this builds none, so it would find nothing there")
    analysed = [f for f in filters if held.get(f) in TEXT_TYPES]
    if analysed:
        hints = [k for k in held if any(k.startswith(f"{f}.") and held[k] == "keyword" for f in analysed)]
        raise ElasticError(FILTER_ON_TEXT, f"{', '.join(analysed)} {'is' if len(analysed) == 1 else 'are'} analysed "
                                           "text, so an exact filter on it matches only a single lower-cased word: "
                                           "filter on a keyword field" + (f" ({', '.join(hints)})" if hints else ""))
    return chosen, held


def _text_of(source: Mapping[str, Any], field: str) -> str:
    """The text of ``field`` in a hit's ``_source``; a multi-field (``title.keyword``) reads its parent's value."""
    value: Any = source
    for part in field.split("."):
        if not isinstance(value, Mapping):
            break  # the rest names a multi-field of the value already reached
        value = value.get(part)
    if isinstance(value, (list, tuple)):
        return "\n".join(str(v) for v in value if isinstance(v, (str, int, float)) and str(v).strip())
    return str(value) if isinstance(value, (str, int, float)) and str(value).strip() else ""


def _hit_passage(base: str, index: str, hit: Mapping[str, Any], fields: Sequence[str]) -> Passage | None:
    source = hit.get("_source")
    if not isinstance(source, Mapping):
        return None
    parts = list(dict.fromkeys(t for t in (_text_of(source, f) for f in fields) if t))
    text = "\n\n".join(parts)
    if not text:
        return None
    read = json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    ident = str(hit.get("_id", ""))
    concrete = str(hit.get("_index") or index)
    uri = f"{base.rstrip('/')}/{quote(concrete, safe='')}/_doc/{quote(ident, safe='')}"
    document = Document(text=text, source=Source(uri=uri, sha256=sha256_hex(read), retrieved_at=now(),
                                                 provenance="fetched"),
                        media_type="application/json", converted_by="g6.knowledge Elasticsearch hit fields",
                        locator=f"{concrete}/{ident}")
    return document.passage(0, len(text))


def search(index: str, query: str, *, fields: Sequence[str] = (), filters: Mapping[str, Scalar] | None = None,
           top_k: int = 10, caller: Caller | None = None) -> Searched:
    """The hits of ``query`` over ``fields`` (every text field when none is named) with exact ``filters``; see the
    module docstring. Every failure is an :class:`ElasticError`; a server that answered is a :class:`Searched`."""
    if not query.strip():
        raise ElasticError(BAD_INPUT, "there is no query")
    filters = dict(filters or {})
    chosen, held = checked_fields(index, fields, filters, text=True, caller=caller)
    body = {"query": _query(query, chosen, filters), "size": top_k, "track_total_hits": True}
    found = _call("POST", f"/{quote(index, safe='')}/_search", body, caller)
    try:
        raw = found["hits"]["hits"]
        total_field = found["hits"]["total"]
        total = int(total_field["value"] if isinstance(total_field, Mapping) else total_field)
        relation = str(total_field.get("relation", "eq")) if isinstance(total_field, Mapping) else "eq"
        shards = found.get("_shards") or {}
    except (KeyError, TypeError, ValueError):
        raise ElasticError(PARSE_FAILED, "the server's search reply has no hits.hits and hits.total") from None
    if not isinstance(raw, list):
        raise ElasticError(PARSE_FAILED, "the server's hits.hits is not a list")
    base = outside.service_url(ELASTICSEARCH)
    scored: list[tuple[Passage, float]] = []
    without_text = 0
    for hit in raw:
        passage = _hit_passage(base, index, hit, chosen) if isinstance(hit, Mapping) else None
        if passage is None:
            without_text += 1
            continue
        score = hit.get("_score")
        scored.append((passage, float(score) if isinstance(score, (int, float)) else 0.0))
    hits = [Hit(passage=p, score=s, score_scale=SCALE, rank=n) for n, (p, s) in enumerate(scored, start=1)]
    failed_shards = int(shards.get("failed", 0) or 0) if isinstance(shards, Mapping) else 0
    partial = [*([f"{failed_shards} of {shards.get('total')} shards failed"] if failed_shards else []),
               *(["the search timed out on the server"] if found.get("timed_out") else [])]
    evidence = {"fields": chosen, "field_types": {f: held.get(f, "") for f in chosen}, "filters": filters,
                "total": total,
                "total_relation": relation, "shards": dict(shards) if isinstance(shards, Mapping) else {},
                "partial": partial, "without_text": without_text}
    if raw and not hits:
        raise ElasticError(PARSE_FAILED, f"the server matched {total} documents but sent none of the fields' text "
                                         f"({', '.join(chosen)}) in _source: is _source turned off for {index}, or "
                                         "is the text kept elsewhere?", evidence=evidence)
    reason = "; ".join([*partial, *([f"{without_text} hits had none of the fields' text in _source"]
                                    if without_text else [])])
    return Searched(backend="elastic", query=query, outcome="ok" if hits else "empty", score_scale=SCALE, hits=hits,
                    total=total, reason=reason, evidence=evidence)


def get_doc(index: str, ident: str, *, caller: Caller | None = None) -> Document:
    """The document ``ident`` of ``index``: its ``_source`` as JSON text, untrusted, with its address on the server."""
    if not ident or len(ident) > 512:
        raise ElasticError(BAD_INPUT, "a document id is 1 to 512 characters")
    path = f"/{quote(check_index(index), safe='')}/_doc/{quote(ident, safe='')}"
    found = _call("GET", path, None, caller)
    if not isinstance(found, Mapping) or found.get("found") is not True or not isinstance(found.get("_source"),
                                                                                           Mapping):
        raise ElasticError(NOT_FOUND, f"{index} holds no document {ident!r}")
    text = json.dumps(found["_source"], sort_keys=True, ensure_ascii=False, indent=1)
    base = outside.service_url(ELASTICSEARCH)
    return Document(text=text, source=Source(uri=f"{base.rstrip('/')}{path}", sha256=sha256_hex(text),
                                             retrieved_at=now(), provenance="fetched"),
                    media_type="application/json", converted_by="g6.knowledge Elasticsearch _source",
                    locator=f"{index}/{ident}")


def count(index: str, query: str = "", *, fields: Sequence[str] = (), filters: Mapping[str, Scalar] | None = None,
          caller: Caller | None = None) -> tuple[int, list[str]]:
    """How many documents of ``index`` match (all of them with no query and no filter), and the fields searched. A
    count whose shards did not all answer is :class:`ElasticError` ``COUNT_PARTIAL``, never an ordinary count."""
    filters = dict(filters or {})
    chosen, _ = checked_fields(index, fields, filters, text=bool(query.strip()), caller=caller)
    found = _call("POST", f"/{quote(index, safe='')}/_count", {"query": _query(query, chosen, filters)}, caller)
    try:
        counted = int(found["count"])
        shards = found.get("_shards") or {}
        failed = int(shards.get("failed", 0) or 0) if isinstance(shards, Mapping) else 0
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ElasticError(PARSE_FAILED, "the server's count reply has no count") from None
    if failed:
        raise ElasticError(COUNT_PARTIAL, f"{failed} of {shards.get('total')} shards failed, so {counted} counts only "
                                          "the shards that answered: it is not the index's count",
                           evidence={"partial_count": counted, "shards": dict(shards)})
    return counted, chosen


def _some(held: Mapping[str, str], limit: int = 12) -> str:
    listed = [f"{name} ({kind})" for name, kind in list(held.items())[:limit]]
    more = f" and {len(held) - limit} more" if len(held) > limit else ""
    return (", ".join(listed) + more) if listed else "no fields"


class ElasticIndex(Retrieves):
    """One index on the person's Elasticsearch as a read-only family index (and so a ``g6.grounding`` retriever).
    Each call reaches the server; ``add`` and ``remove`` are refused, since G6 never writes to it."""

    backend = "elastic"
    score_scale = SCALE

    def __init__(self, index: str, *, fields: Sequence[str] = (), filters: Mapping[str, Scalar] | None = None,
                 caller: Caller | None = None, **_: Any) -> None:
        self.index, self.fields, self.filters, self.caller = check_index(index), tuple(fields), dict(filters or {}), \
            caller

    def find(self, query: str, top_k: int = 10) -> Searched:
        try:
            return search(self.index, query, fields=self.fields, filters=self.filters, top_k=top_k,
                          caller=self.caller)
        except ElasticError as exc:
            outcome = "unavailable" if exc.code in (UNREACHABLE, TIMED_OUT) else "failed"
            return Searched(backend="elastic", query=query, outcome=outcome, score_scale=SCALE, reason=exc.reason,
                            evidence={"code": exc.code, **exc.evidence})

    def add(self, passages: Sequence[Passage]) -> Added:
        raise IndexRefused(READ_ONLY, "G6 never writes to your Elasticsearch: index documents with your own tools")

    def remove(self, ids: Sequence[str]) -> Removed:
        raise IndexRefused(READ_ONLY, "G6 never deletes from your Elasticsearch")

    def status(self) -> dict[str, Any]:
        held = mapping(self.index, caller=self.caller)
        return {"backend": "elastic", "index": self.index, "fields": held,
                "documents": count(self.index, caller=self.caller)[0]}


BACKEND = IndexBackend(name="elastic", summary="one index on the Elasticsearch you run, read only, through "
                       "g6.core.outside (every call reaches the server)", score_scale=SCALE, open=ElasticIndex,
                       local=False, network=True, persistent=True, writable=False)
index_backends.register(BACKEND, replace=True)

__all__ = ["BACKEND", "COUNT_PARTIAL", "DENIED", "ELASTICSEARCH", "ELASTIC_KEY", "FIELD_MISSING", "FILTER_ON_TEXT", "NESTED",
           "NOT_FOUND", "NO_TEXT_FIELDS", "PARSE_FAILED", "REDIRECTED", "REJECTED", "SCALE", "SERVER_ERROR",
           "TIMED_OUT", "UNREACHABLE", "ElasticError", "ElasticIndex", "check_field", "check_index", "count",
           "flatten", "get_doc", "mapping", "search"]
